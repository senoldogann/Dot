"""What a run changed, under its answer (item 597): a run that created a file and changed another shows one line that
says so, the files by name, Review (the Changes tab) and Undo, which asks from the plan as it is when pressed: a file
changed since stays as it is, and the line is drawn again from the plan after. A run whose files all went back says the
changes were taken back; one that changed no file shows nothing. Wide and light, and on a phone and dark. A real server
on a scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
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

TOKEN: str = "run-changes-card-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_PLAN: str = "plan.md"
_PLAN_BEFORE: str = "one\ntwo\n"


def _call(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    """Four runs: one creates a page and changes the plan, one changes nothing, one writes a draft, one a note."""
    return [
        _call("write_file", {"path": "site/index.html", "content": "<h1>Herald</h1>\n"}),
        _call("edit_file", {"path": _PLAN, "old": "two", "new": "2"}),
        _call(FINISH_TOOL, {"answer": "The page is made and the plan fixed.", "success": True}),
        _call(FINISH_TOOL, {"answer": "Nothing to change.", "success": True}),
        _call("write_file", {"path": "draft.md", "content": "A draft.\n"}),
        _call(FINISH_TOOL, {"answer": "The draft is written.", "success": True}),
        _call("write_file", {"path": "keep.md", "content": "Keep this.\n"}),
        _call(FINISH_TOOL, {"answer": "The note is kept.", "success": True}),
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
async def served(runtime: Runtime) -> AsyncIterator[tuple[str, RunRecord, RunRecord, RunRecord, RunRecord]]:
    """The server, the run that changed two files, one that changed none, one that wrote a draft and one a note."""
    (workspace_dir() / _PLAN).write_text(_PLAN_BEFORE, encoding="utf-8")
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
        runs: list[RunRecord] = []
        for goal in ("Make the herald's page and fix the plan.", "Look at the plan.", "Write a draft.", "Keep a note."):
            run: RunRecord = await manager.start(
                goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
            )
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            runs.append(run)
        yield f"http://127.0.0.1:{port}", runs[0], runs[1], runs[2], runs[3]
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


def _kept_in(errors: list[str]) -> Callable[[Exception], None]:
    def keep(error: Exception) -> None:
        errors.append(str(error))

    return keep


async def _open(browser: Browser, address: str, run_id: str, width: int, scheme: str) -> tuple[Page, list[str]]:
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", _kept_in(errors))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".answer").last).to_be_visible()
    return page, errors


async def _line(page: Page) -> Locator:
    """The line of what the run changed, at the head of the side panel's Changes tab (item 841), the tab opened."""
    await page.get_by_role("button", name="Task details").first.click()
    await page.get_by_role("tab", name="Changes").click()
    return page.locator("#panel-run-changes").get_by_role("region", name="What the task changed")


async def test_a_run_that_changed_files_says_so_and_undo_asks_from_the_plan_of_the_moment(
    served: tuple[str, RunRecord, RunRecord, RunRecord, RunRecord], tmp_path: Path
) -> None:
    """The plan is read again when Undo is pressed (item 597's review): the owner changed the plan after the line was
    drawn, so Undo asks to delete the page alone, says the plan stays, and the line keeps the plan, with no Undo."""
    address, changed, _, _, _ = served
    page_file: Path = workspace_dir() / "site" / "index.html"
    plan: Path = workspace_dir() / _PLAN
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, changed.id, 1280, "light")
            # The answer holds no line of what the run changed (item 841): a chat is no editor's list.
            await expect(page.locator(".answer .run-changes")).to_have_count(0)
            card: Locator = await _line(page)
            await expect(card.locator(".run-changes-words")).to_have_text("Created a file and changed 1")
            # In the plan's order, the file changed last first; no Review, the files and their diffs being under it.
            await expect(card.locator(".run-changes-file")).to_have_text([_PLAN, "index.html"])
            await expect(card.get_by_role("button", name="Review")).to_have_count(0)
            await expect(page.locator("#panel-run-changes details.change")).to_have_count(2)
            await page.screenshot(path=str(tmp_path / "changes-card-1280-light.png"))
            # The owner changes the plan after the line was drawn.
            await asyncio.to_thread(plan.write_text, "one\n2\nmine\n", encoding="utf-8")
            await card.get_by_role("button", name="Undo").click()
            dialog: Locator = page.get_by_role("dialog")
            asked: str = "The file it created is deleted. A file changed since stays as it is."
            await expect(dialog).to_contain_text(asked)
            # The page's Changes tab and Files card read the files again once the Undo is through (the review, round 2).
            async with page.expect_request(lambda request: request.url.endswith(f"/api/runs/{changed.id}/changes")):
                await dialog.get_by_role("button", name="Undo").click()
            await expect(card.locator(".run-changes-words")).to_have_text("Changed a file")
            await expect(card.locator(".run-changes-file")).to_have_text([_PLAN])
            await expect(card.get_by_role("button", name="Undo")).to_be_disabled()
            assert not await asyncio.to_thread(page_file.exists)
            assert await asyncio.to_thread(plan.read_text, encoding="utf-8") == "one\n2\nmine\n"
            assert errors == []
            await page.context.close()
            # On a phone the line holds the plan the run cannot take back.
            phone, phone_errors = await _open(browser, address, changed.id, 390, "dark")
            line: Locator = await _line(phone)
            await expect(line.locator(".run-changes-words")).to_have_text("Changed a file")
            await phone.screenshot(path=str(tmp_path / "changes-card-390-dark.png"))
            assert phone_errors == []
            await phone.context.close()
        finally:
            await browser.close()


async def test_undo_takes_the_run_back_and_the_line_says_so(
    served: tuple[str, RunRecord, RunRecord, RunRecord, RunRecord],
) -> None:
    address, _, _, drafted, _ = served
    draft: Path = workspace_dir() / "draft.md"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, drafted.id, 1280, "light")
            card: Locator = await _line(page)
            await expect(card.locator(".run-changes-words")).to_have_text("Created a file")
            # A double click asks once (the review, round 4): the second press comes while the first reads the plan.
            await card.get_by_role("button", name="Undo").dblclick()
            dialog: Locator = page.get_by_role("dialog")
            await expect(dialog).to_contain_text("The file it created is deleted.")
            await dialog.get_by_role("button", name="Undo").click()
            await expect(card).to_have_text("Its file changes were taken back.")
            await expect(dialog).to_have_count(0)
            assert not await asyncio.to_thread(draft.exists)
            # Opened again, the run's files are as they were and the line says nothing: no undo of this card's.
            await page.reload()
            await expect(page.locator(".answer").last.locator(".answer-foot")).to_be_visible()
            await expect(page.locator(".run-changes")).to_be_hidden()
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()


async def test_a_run_that_changed_no_file_shows_nothing_under_its_answer(
    served: tuple[str, RunRecord, RunRecord, RunRecord, RunRecord],
) -> None:
    address, _, unchanged, _, _ = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, unchanged.id, 1280, "light")
            await expect(page.locator(".answer").last.locator(".answer-foot")).to_be_visible()
            await expect(page.locator(".run-changes")).to_be_hidden()
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()


async def test_an_undo_whose_plan_cannot_be_read_says_so(
    served: tuple[str, RunRecord, RunRecord, RunRecord, RunRecord],
) -> None:
    """Item 597's review, round 3: Undo pressed while the plan cannot be read (the daemon gone, a run deleted) says so
    in a toast, where it did nothing; the note stays."""
    address, _, _, _, kept = served
    note: Path = workspace_dir() / "keep.md"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, _ = await _open(browser, address, kept.id, 1280, "light")
            card: Locator = await _line(page)
            await expect(card.locator(".run-changes-words")).to_have_text("Created a file")
            await page.route(f"**/api/runs/{kept.id}/actions", lambda route: route.fulfill(status=500, body="down"))
            await card.get_by_role("button", name="Undo").click()
            await expect(page.locator(".toast.error")).to_be_visible()
            await expect(page.get_by_role("dialog")).to_have_count(0)
            assert await asyncio.to_thread(note.exists)
            await page.context.close()
        finally:
            await browser.close()
