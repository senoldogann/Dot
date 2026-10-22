"""The group of steps of the flow that streams: its summary is the reader's fold while the run still works, not only
once it ends. A click opens it, a click folds it, and the events that follow repaint its head without undoing the
choice; folded it still shows the call that runs now. The end of the run leaves the group as the reader left it. A real
server on a scripted model, wide and on a phone."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "flow-fold-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
WAIT_S: float = 30.0
_GOAL: str = "Fold the steps while it works."
_FIRST: str = "sleep 3; echo one"
_SECOND: str = "echo two"
_ANSWER: str = "The fold is still mine."
_SCRIPT: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": _FIRST}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": _SECOND}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _ANSWER, "success": True}),)),
)


# A note written, then read: two calls whose mark (a file's) is the same and whose words (the path) are too.
_NOTE: str = "notes.txt"
_FILE_SCRIPT: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="write_file", arguments={"path": _NOTE, "content": "one"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="read_file", arguments={"path": _NOTE}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)),
)

# Where the head and each call's row put their mark and their words: the left edge of each, the head first.
_LINES: str = """(group) => [
  [group.querySelector(".steps-state"), group.querySelector(".steps-title")],
  ...[...group.querySelectorAll(".steps-body .tool-head")].map(
    (row) => [row.querySelector(".tool-state"), row.querySelector(".tool-sum")],
  ),
].map(([mark, words]) => [mark.getBoundingClientRect().left, words.getBoundingClientRect().left])"""


class _SecondReplyHeld(ScriptedProvider):
    """The script replays at once, but its second reply waits for the test: the run works, its first call reported,
    while the page is read and the group is clicked."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, _SCRIPT)
        self.calls: int = 0
        self.asked: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        if self.calls == 2:
            go: asyncio.Event = asyncio.Event()
            self.asked.put_nowait(go)
            await go.wait()
        return await super().complete(request, sink)

    async def release(self) -> None:
        """Let the reply the run waits for go."""
        async with asyncio.timeout(WAIT_S):
            (await self.asked.get()).set()


@asynccontextmanager
async def _served(model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """A runtime in a throw-away home whose model is ``model``, its runs, and the address a real server serves it at."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(
            eval_config(load_default_config()),
            await read_login_env(load_default_config().shell.login_env_timeout_s, ()),
        )
        try:
            await runtime.router.register(_PROVIDER, model)
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
                yield runtime, manager, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    """The run, once it is over."""
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


@pytest.mark.browser
async def test_the_step_group_folds_and_unfolds_while_the_run_still_works(tmp_path) -> None:
    """A theme that folds the steps by default leaves the group folded while the run works, its summary a click away
    from open. The reader opens it, folds it again, and the calls that stream after the fold leave it as they found it
    while the latest line still says what runs now. A last click opens it, and the end of the run keeps it open."""
    model: _SecondReplyHeld = _SecondReplyHeld()
    async with _served(model) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(
                    viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
                )
                # Neutral folds the steps by default; the reader's own choice is not in the browser yet.
                await page.add_init_script(
                    "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.theme', 'neutral');"
                    " localStorage.removeItem('dot.foldSteps');"
                )
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                group: Locator = page.locator(".timeline details.steps").first
                await expect(group).to_have_count(1)
                summary: Locator = group.locator("summary.steps-head")
                await expect(summary).to_be_visible()
                # The first call has streamed and the group is at work, folded as the theme folds it; its latest line
                # says what runs now.
                await expect(group.locator(".steps-latest")).to_contain_text("echo one")
                # The call in its row's words: the command, no tool's name before it (item 402).
                await expect(group.locator(".steps-latest")).to_have_text(_FIRST)
                assert await group.evaluate("(node) => node.open") is False
                await expect(group.locator(".steps-latest")).to_be_visible()
                # Folded and at work, the head names the command that runs, in place of how much it did so far, and
                # before the chevron, not after it.
                await expect(group.locator(".steps-count")).to_be_hidden()
                before_chevron: bool = await group.evaluate(
                    "(node) => node.querySelector('.steps-latest').getBoundingClientRect().right"
                    " <= node.querySelector('.steps-head .chev').getBoundingClientRect().left"
                )
                assert before_chevron
                await group.screenshot(path=str(tmp_path / "steps-folded-at-work.png"))
                # (1) A click opens it: the call it holds is visible, and the count of what it did so far is back.
                first_tool: Locator = group.locator(".steps-body details.tool").first
                await expect(first_tool).to_have_count(1)
                await summary.click()
                assert await group.evaluate("(node) => node.open") is True
                await expect(first_tool).to_be_visible()
                await expect(group.locator(".steps-count")).to_have_text("ran 1 command so far")
                # (2) A click folds it while the command still runs; the call's result streams, the head is painted
                # anew, and the group stays folded with the latest line in sight.
                await summary.click()
                assert await group.evaluate("(node) => node.open") is False
                await expect(first_tool).not_to_have_class(re.compile(r"\brunning\b"))
                assert await group.evaluate("(node) => node.open") is False
                await expect(group.locator(".steps-latest")).to_be_visible()
                await group.screenshot(path=str(tmp_path / "steps-folded-mid-run.png"))
                # (3) A click opens it again, the call in sight, and the end of the run keeps it open.
                await summary.click()
                assert await group.evaluate("(node) => node.open") is True
                await expect(group.locator(".steps-body details.tool")).to_have_count(1)
                await group.screenshot(path=str(tmp_path / "steps-open-mid-run.png"))
                await model.release()
                # The second call and its result stream while the group is open, then the run ends.
                await expect(group.locator(".steps-body details.tool")).to_have_count(2)
                assert (await _ended(manager, run.id)).status == "done"
                await expect(page.locator(".timeline .answer")).to_contain_text(_ANSWER)
                assert await group.evaluate("(node) => node.open") is True
                await expect(group.locator(".steps-latest")).to_be_hidden()
                # Each call is one line in line with the head (item 402): the mark of its kind under the group's, the
                # command itself under the group's words, the tool's name for screen readers alone.
                rows: Locator = group.locator(".steps-body .tool-head")
                await expect(rows.locator(".tool-sum")).to_have_text([_FIRST, _SECOND])
                await expect(rows.locator(".tool-name").first).to_have_class(re.compile(r"\bvisually-hidden\b"))
                await expect(rows.locator(".tool-state [data-icon='terminal']")).to_have_count(2)
                await expect(summary.locator(".steps-state [data-icon='terminal']")).to_have_count(1)
                lines: list[list[float]] = await group.evaluate(_LINES)
                assert all(abs(row[0] - lines[0][0]) <= 1 and abs(row[1] - lines[0][1]) <= 1 for row in lines), lines
                await page.screenshot(path=str(tmp_path / "steps-after-the-run-1280.png"))
                # On a phone the summary is still there, the group still open and its calls in sight.
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.wait_for_function("() => window.innerWidth === 390")
                await expect(summary).to_be_visible()
                assert await group.evaluate("(node) => node.open") is True
                await page.screenshot(path=str(tmp_path / "steps-after-the-run-390.png"))
            finally:
                await browser.close()


async def test_calls_whose_mark_is_shared_keep_their_names_and_say_what_they_did() -> None:
    """A call whose mark several tools share (a file's) keeps its tool's name and says what it did once it is done, so a
    write and a read of one file are told apart; only a command is its own words alone (item 402)."""
    model: ScriptedProvider = ScriptedProvider(_PROVIDER, list(_FILE_SCRIPT))
    async with _served(model) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            "Write the note, then read it.", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None,
            "yolo", False, (),
        )  # fmt: skip
        assert (await _ended(manager, run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                group: Locator = page.locator(".timeline details.steps").first
                await expect(group.locator(".steps-title")).to_have_text("Read 1 file and wrote 1 file")
                if not await group.evaluate("(node) => node.open"):
                    await group.locator("summary.steps-head").click()
                rows: Locator = group.locator(".steps-body .tool-head")
                await expect(rows.locator(".tool-name")).to_have_text(["Write file", "Read file"])
                await expect(rows.locator(".tool-name.visually-hidden")).to_have_count(0)
                said: list[str] = await rows.locator(".tool-sum").all_text_contents()
                assert len(said) == 2 and said[0] != said[1] and all(_NOTE in words for words in said), said
            finally:
                await browser.close()
