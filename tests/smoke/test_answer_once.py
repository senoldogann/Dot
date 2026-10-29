"""A task run that answers in a plain reply is told to finish (the loop's note on a reply without a tool), thinks once
more and calls finish with the same words: its chat shows the answer once (item 460), as an earlier turn does; so it
does when the finish rewords a line of the reply (item 567: a run's prompt for another agent came back with its first
line changed, and the chat showed it twice). A real server on a scripted model, the chat read once the run is over."""

import asyncio
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
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply, StreamDelta
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "answer-once-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
WAIT_S: float = 30.0
_GOAL: str = "Are both accounts connected?"
_ANSWER: str = "Yes, both accounts are connected: Gmail and Outlook answered just now."
_SCRIPT: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo checked"}),)),
    # The answer as a plain reply, twice: a task run that used a tool is told to finish instead of ending here, and a
    # weak model may answer the note in words again before it calls finish.
    ScriptedTurn(text=_ANSWER, calls=()),
    ScriptedTurn(text=_ANSWER, calls=()),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _ANSWER, "success": True}),)),
)


class _ThinksBeforeFinish(ScriptedProvider):
    """The script, the step that finishes thinking first, as the model did that answered twice (2026-10-21)."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, _SCRIPT)
        self.calls: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        reply: ModelReply = await super().complete(request, sink)
        if self.calls == len(_SCRIPT):
            return reply.model_copy(update={"thinking": "The note says I replied without a tool; I call finish."})
        return reply


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


async def test_an_answer_repeated_by_finish_after_a_thought_shows_once() -> None:
    """The plain reply twice, a thought and the finish with the same words: the chat holds those words once, in the
    answer, and the thought stays."""
    async with _served(_ThinksBeforeFinish()) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        assert [event.data["text"] for event in await runtime.store.events_of_kind(run.id, "assistant", 10)].count(
            _ANSWER
        ) == 2
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                answer: Locator = page.locator(".timeline .answer")
                await expect(answer).to_contain_text(_ANSWER, timeout=15000)
                await expect(page.locator(".timeline").get_by_text(_ANSWER)).to_have_count(1)
                await expect(page.locator(".timeline").get_by_text("The note says I replied").first).to_be_attached()
            finally:
                await browser.close()


# A long answer whose first line the finish rewords, as DeepSeek V4.1 Flash did on 2026-10-23 (item 567).
_BODY_LINES: tuple[str, ...] = tuple(
    f"{number}. Fix defect {number} in the search service and add a test that fails before the fix."
    for number in range(1, 9)
)
_REPLY: str = "The prompt for the coding agent, to paste as it is:\n" + "\n".join(_BODY_LINES)
_REWORDED: str = "Here is the prompt you can give the coding agent:\n" + "\n".join(_BODY_LINES)


async def test_an_answer_reworded_by_finish_shows_once() -> None:
    """The plain reply, then the finish with its first line reworded: the chat holds the words once, in the answer."""
    script: tuple[ScriptedTurn, ...] = (
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo checked"}),)),
        ScriptedTurn(text=_REPLY, calls=()),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _REWORDED, "success": True}),)),
    )
    async with _served(ScriptedProvider(_PROVIDER, script)) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                answer: Locator = page.locator(".timeline .answer")
                await expect(answer).to_contain_text("Here is the prompt you can give the coding agent", timeout=15000)
                # The lines stand as a list in the answer, their numbers the list's own.
                await expect(page.locator(".timeline").get_by_text("Fix defect 4 in the search service")).to_have_count(
                    1
                )
                await expect(page.locator(".timeline").get_by_text("The prompt for the coding agent")).to_have_count(0)
            finally:
                await browser.close()


# A reply with a line the finish leaves out: what it adds would be lost if it went (the review of item 567).
_WARNING: str = "But the backup step failed: the disk is full."


async def test_a_reply_that_says_more_than_the_answer_stays() -> None:
    """The plain reply, then the finish without the reply's last line: the chat keeps the reply, that line with it."""
    script: tuple[ScriptedTurn, ...] = (
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo checked"}),)),
        ScriptedTurn(text=f"{_REWORDED}\n{_WARNING}", calls=()),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _REWORDED, "success": True}),)),
    )
    async with _served(ScriptedProvider(_PROVIDER, script)) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                answer: Locator = page.locator(".timeline .answer")
                await expect(answer).to_contain_text("Here is the prompt you can give the coding agent", timeout=15000)
                await expect(page.locator(".timeline").get_by_text(_WARNING)).to_have_count(1)
                # The reply stands over the answer, so its lines are there twice.
                await expect(page.locator(".timeline").get_by_text("Fix defect 4 in the search service")).to_have_count(
                    2
                )
            finally:
                await browser.close()


# A chat left and come back to while its run works (item 568). The page keeps what it read of the stream and goes on
# from its last event; that was a piece of a thought, which has no id, so the page read the whole stream again and drew
# every message a second time, a third after a second return.
_FIRST: str = "I looked at both accounts first: the mail of each answered, and now I check their calendars too."
_THOUGHT: str = "Now the calendars, one after the other. "


class _HoldsAThought(ScriptedProvider):
    """The script, its second step thinking aloud once the page follows the run, then held until the test lets it go."""

    def __init__(self, script: tuple[ScriptedTurn, ...]) -> None:
        super().__init__(_PROVIDER, script)
        self.calls: int = 0
        self.followed: asyncio.Event = asyncio.Event()
        self.thinking: asyncio.Event = asyncio.Event()
        self.go: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        reply: ModelReply = await super().complete(request, sink)
        if self.calls == 2:
            # A live piece goes to the pages that follow the run then: none is told it again.
            await self.followed.wait()
            await sink(StreamDelta(kind="thinking", text=_THOUGHT))
            self.thinking.set()
            await self.go.wait()
        return reply


async def test_a_chat_left_and_come_back_to_while_its_run_works_shows_each_message_once() -> None:
    """A message, then a thought streams and the reader goes to another page and back, twice: once the run ends, the
    message and the answer are each in the chat once."""
    model = _HoldsAThought(
        (
            ScriptedTurn(text=_FIRST, calls=(ScriptedCall(tool="shell", arguments={"command": "echo checked"}),)),
            ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _ANSWER, "success": True}),)),
        )
    )
    async with _served(model) as (runtime, manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.add_init_script(
                "localStorage.setItem('dot.work', 'steps')"
            )  # the steps this test reads (item 830)
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            run: RunRecord = await manager.start(
                _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
            )
            await page.goto(f"{address}/#/runs/{run.id}")
            chat: Locator = page.locator(".timeline")
            await expect(chat.get_by_text(_FIRST)).to_have_count(1, timeout=15000)
            model.followed.set()
            async with asyncio.timeout(WAIT_S):
                await model.thinking.wait()
            # The thought's piece is the last event the page read of the stream.
            await expect(page.locator("details.thought .thought-text").last).to_contain_text(_THOUGHT.strip())
            for _ in range(2):
                await page.goto(f"{address}/#/settings/system")
                await expect(page.get_by_role("heading", name="System", level=1)).to_be_visible()
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(chat.get_by_text(_FIRST)).to_have_count(1)
            model.go.set()
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            await expect(page.locator(".timeline .answer")).to_contain_text(_ANSWER, timeout=15000)
            await expect(chat.get_by_text(_FIRST)).to_have_count(1)
            await expect(chat.get_by_text(_ANSWER)).to_have_count(1)
        finally:
            await browser.close()


# The owner's chat of 2026-10-24 (item 620): asked for a screenshot, DeepSeek V4.1 Flash described the screen in a
# reply, was told to finish, and wrote the description again in other words; no comparison of words could tell.
_DESCRIBED: str = (
    "I sent the screenshot above and kept a copy in the screenshots folder.\n\n"
    "On the screen:\n- The code editor is in front, on the web-research conversation.\n- The Dock shows Messages."
)
_REDESCRIBED: str = (
    "The screenshot is above; a copy is saved as a file in the screenshots folder (2.6 MB).\n\n"
    "What the screen shows:\n- In front: the code editor, its web-research conversation open.\n"
    "- Messages sits in the Dock."
)


async def _run_and_read(script: tuple[ScriptedTurn, ...]) -> tuple[list[object], list[str]]:
    """Run ``script`` as a task run and read its page: the steps its end names as restated (``restated`` of its
    ``run_finished`` event, or none), and the texts of the chat's replies and answer as the page shows them."""
    async with _served(ScriptedProvider(_PROVIDER, script)) as (runtime, manager, address):
        run: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        restated: list[object] = [
            event.data["restated"]
            for event in await runtime.store.events_of_kind(run.id, "run_finished", 1)
            if "restated" in event.data
        ]
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".timeline .answer")).to_have_count(1, timeout=15000)
                said: list[str] = await page.locator(".timeline .bubble.say, .timeline .answer").all_inner_texts()
            finally:
                await browser.close()
        return restated, said


async def test_an_answer_the_finish_writes_again_in_other_words_shows_once() -> None:
    """Item 620: the reply, the note, and the finish that says it again in other words: the page shows the answer
    alone, as the run's end names the reply it restates."""
    restated, said = await _run_and_read(
        (
            ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo shot"}),)),
            ScriptedTurn(text=_DESCRIBED, calls=()),
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _REDESCRIBED, "success": True}),)
            ),
        )
    )
    assert restated == [[2]]
    assert len(said) == 1 and "What the screen shows" in said[0], said


async def test_a_reply_the_answer_only_points_at_or_that_more_work_followed_stays() -> None:
    """A finish that only points at the reply ("see above") leaves the reply to be read; and a reply after which the run
    went on working was a step announced, not the answer."""
    pointed, said = await _run_and_read(
        (
            ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo shot"}),)),
            ScriptedTurn(text=_DESCRIBED, calls=()),
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "See above.", "success": True}),)
            ),
        )
    )
    assert pointed == [] and any("The Dock shows Messages" in text for text in said), said
    worked, said = await _run_and_read(
        (
            ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo shot"}),)),
            ScriptedTurn(text=_DESCRIBED, calls=()),
            ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo again"}),)),
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _REDESCRIBED, "success": True}),)
            ),
        )
    )
    assert worked == [] and any("The Dock shows Messages" in text for text in said), said


async def test_an_earlier_turn_leaves_out_the_reply_its_answer_restated() -> None:
    """Item 620, in the chat's earlier turn (history.js): once a follow-up was asked, the turn of the run whose finish
    wrote its reply again in other words holds the answer, not the reply."""
    script: tuple[ScriptedTurn, ...] = (
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo shot"}),)),
        ScriptedTurn(text=_DESCRIBED, calls=()),
        ScriptedTurn(
            text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _REDESCRIBED, "success": True}),)
        ),
        ScriptedTurn(text="Done.", calls=()),
    )
    async with _served(ScriptedProvider(_PROVIDER, script)) as (runtime, manager, address):
        first: RunRecord = await manager.start(
            _GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(first.id)).status == "done"
        second: RunRecord = await manager.start(
            "Thanks",
            "task",
            SCRIPTED_MODEL,
            first.thread_id,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(second.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{second.id}")
                earlier: Locator = page.locator("section.turn").first
                await expect(earlier.locator(".answer")).to_contain_text("What the screen shows", timeout=15000)
                await expect(earlier.locator(".turn-ops")).to_be_attached()
                # The flow is drawn (its call is there), and the reply the answer restated is not in it.
                await expect(earlier.locator(".turn-ops > *").first).to_be_attached(timeout=15000)
                assert await earlier.locator(".turn-say").count() == 0
            finally:
                await browser.close()
