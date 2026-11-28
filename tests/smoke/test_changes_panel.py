"""The Changes of a chat in the side panel (item 368): the file a run created and edited, with its lines added and
removed, opening on its diff, and the tab counting the files; the same from `GET /api/runs/{id}/changes`. A file there
shows as it is now from its row, a web page drawn in a sandboxed frame, and a file the answer names by its name, in
words or in code, from the answer (item 1077). An opened change reads by its format (item 2017): a file changed
as a diff with its line numbers, signs and colours, a new file as the file, a new Markdown file formatted with its
source a press away. At 1440x900 and light, and at 390x844 and dark. A real server on a scripted model."""

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
# A page with a script that would write a line and a refresh that would take it elsewhere: the panel draws it with
# neither (item 1077's pre-push review: a script may take its frame to another site).
PAGE: str = (
    '<meta http-equiv="refresh" content="0;url=http://127.0.0.1:9/"><h1>Report</h1><p id="line">as written</p>'
    '<script>document.getElementById("line").textContent = "ran"; location = "http://127.0.0.1:9/"</script>\n'
)
ANSWER: str = "The notes are written in notes.md, and the page in `page.html`."
# A file that was there before the run, which the run edits: its change is a diff. And a new Python file.
APP_BEFORE: str = "import sys\n\n\ndef main() -> int:\n    x = 1\n    return x\n"
TOOL: str = "import os\n\n\ndef main() -> None:\n    print(os.getcwd())\n"


def _call(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    return [
        _call("write_file", {"path": "notes.md", "content": "one\ntwo\nthree\n"}),
        _call("edit_file", {"path": "notes.md", "old": "two", "new": "2"}),
        _call("write_file", {"path": "site/page.html", "content": PAGE}),
        _call("edit_file", {"path": "app.py", "old": "x = 1", "new": "x = 2"}),
        _call("write_file", {"path": "tool.py", "content": TOOL}),
        _call(FINISH_TOOL, {"answer": ANSWER, "success": True}),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    (home / "workspace").mkdir(exist_ok=True)
    (home / "workspace" / "app.py").write_text(APP_BEFORE, encoding="utf-8")
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
    assert (view["added"], view["removed"]) == (10, 1)
    [file] = [file for file in view["files"] if file["path"].endswith("notes.md")]
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
            for width, height, scheme in ((1440, 900, "light"), (390, 844, "dark")):
                context = await browser.new_context(
                    viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                errors: list[str] = []
                page.on("pageerror", _kept_in(errors))
                # The page's own window only: a sandboxed frame of a web page (item 1077) has no storage.
                await page.add_init_script(
                    "if (window === window.top) {"
                    " sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.inspector', 'on') }"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".answer").last).to_be_visible()
                if width < 960:
                    await page.locator("button[aria-label='Task details']").click()
                tab: Locator = page.get_by_role("tab", name="Changes")
                await expect(tab).to_contain_text("4")
                await tab.click()
                section: Locator = page.locator("#panel-run-changes")
                await expect(section.locator("details.change")).to_have_count(4)
                # Every file starts closed, and what it shows is drawn once it is opened (item 674).
                await expect(section.locator("details.change[open]")).to_have_count(0)
                await expect(section.locator(".code-view")).to_have_count(0)
                row: Locator = section.locator("details.change").filter(has_text="notes.md")
                await expect(row.locator(".change-name")).to_have_text("notes.md")
                await expect(row.locator(".badge")).to_have_text("created")
                await expect(row.locator(".change-add")).to_have_text("+3")
                await expect(row.locator(".change-del")).to_have_count(0)
                # A new Markdown file reads formatted, its source a press away, numbered and with no sign.
                await row.locator(".change-head").click()
                await expect(row.locator(".change-markdown .file-markdown")).to_contain_text("three")
                await row.get_by_role("button", name="Source").click()
                await expect(row.locator(".code-row .code-text")).to_have_text(["one", "2", "three"])
                await expect(row.locator(".code-sign")).to_have_count(0)
                await _code_views(section, width)
                box = await row.locator(".change-body").bounding_box()
                assert box is not None and box["x"] + box["width"] <= width, box
                await page.screenshot(path=str(tmp_path / f"changes-{width}-{scheme}.png"))
                await _previews(page, section, tmp_path / f"page-{width}-{scheme}.png")
                assert errors == []
                await context.close()
        finally:
            await browser.close()


async def _code_views(section: Locator, width: int) -> None:
    """A file changed opens on its diff: both line numbers, a sign and a tint for a line added or removed, the code
    coloured, no line wrapped, the view no wider than the page; a new Python file reads as the file, numbered from 1."""
    app: Locator = section.locator("details.change").filter(has_text="app.py")
    await app.locator(".change-head").click()
    view: Locator = app.locator(".code-view")
    await expect(view).to_have_attribute("data-signed", "true")
    await expect(view.locator(".code-removed .code-text")).to_have_text(["    x = 1"])
    await expect(view.locator(".code-added .code-text")).to_have_text(["    x = 2"])
    await expect(view.locator(".code-added .code-sign")).to_have_text("+")
    await expect(view.locator(".code-removed .code-num")).to_have_text(["5", ""])
    await expect(view.locator(".code-added .code-num")).to_have_text(["", "5"])
    await expect(view.locator(".tok-keyword").first).to_have_text("def")
    assert await view.locator(".code-text").first.evaluate("node => getComputedStyle(node).whiteSpace") == "pre"
    box = await view.bounding_box()
    assert box is not None and box["x"] + box["width"] <= width, box
    tool: Locator = section.locator("details.change").filter(has_text="tool.py")
    await tool.locator(".change-head").click()
    await expect(tool.locator(".code-view")).to_have_attribute("data-signed", "false")
    await expect(tool.locator(".code-row")).to_have_count(5)
    await expect(tool.locator(".code-num").first).to_have_text("1")
    await expect(tool.locator(".code-sign")).to_have_count(0)


async def _previews(page: Page, section: Locator, shot: Path) -> None:
    """Item 1077: a row shows its file as it is now without opening its diff; a web page is drawn in a frame that runs
    none of its scripts and follows none of its refreshes, its source a click away; the answer's file names, in words
    and in code, show their files."""
    view: Locator = page.locator(".file-view")
    pages: Locator = section.locator("details.change").filter(has_text="page.html")
    await pages.get_by_role("button", name="Show page.html as it is now").click()
    await expect(view).to_have_attribute("data-kind", "text")
    frame: Locator = view.locator("iframe.file-look")
    await expect(frame).to_have_attribute("sandbox", "")
    await expect(view.frame_locator("iframe.file-look").locator("#line")).to_have_text("as written")
    await page.wait_for_timeout(1000)
    await expect(view.frame_locator("iframe.file-look").locator("h1")).to_have_text("Report")
    await expect(view.frame_locator("iframe.file-look").locator("#line")).to_have_text("as written")
    await page.screenshot(path=str(shot))
    await view.get_by_role("button", name="Source").click()
    await expect(view.locator(".code-view")).to_contain_text("<h1>Report</h1>")
    await expect(pages).not_to_have_attribute("open", "")
    await view.get_by_role("button", name="Close the file").click()
    await expect(view).to_have_count(0)
    if await page.get_by_role("button", name="Close the details").is_visible():
        # The drawer of the details slides away, the answer under it.
        await page.get_by_role("button", name="Close the details").click()
        await expect(page.locator("aside.inspector")).not_to_have_attribute("data-open", "true")
    # In the answer: the name in words opens the Markdown formatted, the one in code the page.
    answer: Locator = page.locator(".answer").last
    word: Locator = answer.locator("button.file-word")
    await expect(word).to_have_text("notes.md")
    await word.click()
    await expect(view.locator(".file-markdown")).to_contain_text("three")
    await view.get_by_role("button", name="Close the file").click()
    await expect(view).to_have_count(0)
    await answer.locator("button.code-path").filter(has_text="page.html").click()
    await expect(view.frame_locator("iframe.file-look").locator("#line")).to_have_text("as written")
    await view.get_by_role("button", name="Close the file").click()
    await expect(view).to_have_count(0)
