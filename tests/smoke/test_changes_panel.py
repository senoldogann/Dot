"""The Changes of a chat in the side panel (item 368): the file a run created and edited, with its lines added and
removed, opening on its diff, and the tab counting the files; the same from `GET /api/runs/{id}/changes`. Wide and
light, and on a phone and dark. A real server on a scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "changes-panel-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Write the notes and fix their second line."
_RUN_WAIT_S: float = 60.0


def _call(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    return [
        _call("write_file", {"path": "notes.md", "content": "one\ntwo\nthree\n"}),
        _call("edit_file", {"path": "notes.md", "old": "two", "new": "2"}),
        _call(FINISH_TOOL, {"answer": "The notes are written.", "success": True}),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[str, RunRecord]]:
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        yield f"http://127.0.0.1:{port}", run
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


def _kept_in(errors: list[str]) -> Callable[[Exception], None]:
    """A listener that keeps the page's errors in ``errors``."""

    def keep(error: Exception) -> None:
        errors.append(str(error))

    return keep


async def test_the_api_lists_the_file_with_its_counts_and_its_diff(served: tuple[str, RunRecord]) -> None:
    address, run = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        answer = await client.get(f"/api/runs/{run.id}/changes")
    assert answer.status_code == 200, answer.text
    view = answer.json()
    assert (view["added"], view["removed"]) == (3, 0)
    [file] = view["files"]
    assert file["path"].endswith("/workspace/notes.md")
    assert (file["created"], file["gone"], file["changes"], file["note"]) == (True, False, 2, None)
    assert "+one\n+2\n+three\n" in file["diff"]


async def test_the_changes_tab_counts_the_files_and_opens_each_on_its_diff(
    served: tuple[str, RunRecord], tmp_path: Path
) -> None:
    address, run = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme in ((1280, "light"), (390, "dark")):
                context = await browser.new_context(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                errors: list[str] = []
                page.on("pageerror", _kept_in(errors))
                await page.add_init_script(
                    "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.inspector', 'on')"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".answer").last).to_be_visible()
                if width < 960:
                    await page.locator("button[aria-label='Task details']").click()
                tab: Locator = page.get_by_role("tab", name="Changes")
                await expect(tab).to_contain_text("1")
                await tab.click()
                section: Locator = page.locator("#panel-run-changes")
                row: Locator = section.locator("details.change")
                await expect(row).to_have_count(1)
                await expect(row.locator(".change-name")).to_have_text("notes.md")
                await expect(row.locator(".badge")).to_have_text("created")
                await expect(row.locator(".change-add")).to_have_text("+3")
                await expect(row.locator(".change-del")).to_have_count(0)
                # The lines of the diff are drawn once the file is opened (item 674).
                await expect(row.locator(".diff-line")).to_have_count(0)
                await row.locator(".change-head").click()
                await expect(row.locator(".diff-add")).to_have_text(["+one", "+2", "+three"])
                box = await row.locator(".diff").bounding_box()
                assert box is not None and box["x"] + box["width"] <= width, box
                await page.screenshot(path=str(tmp_path / f"changes-{width}-{scheme}.png"))
                assert errors == []
                await context.close()
        finally:
            await browser.close()
