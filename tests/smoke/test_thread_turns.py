"""A chat's turns on one page (item 374): every earlier run shows what it did, one line that says what it ran ("Ran 1
command"), folded as the run's own steps are and open with Fold tool steps off, no "Open run" to hop through, and the
marks at the left go to any message, the last one to the newest prompt and the work under it. A real server on a
scripted model, wide and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

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

TOKEN: str = "thread-turns-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: int = 3


def _work(index: int) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": f"echo step {index}"}),))


def _finish(index: int) -> ScriptedTurn:
    return ScriptedTurn(
        text="",
        calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": f"Answer number {index}.", "success": True}),),
    )


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
    script: list[ScriptedTurn] = []
    for index in range(_TURNS):
        script.extend([_work(index), _finish(index)])
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, script))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def thread(
    runtime: Runtime, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[tuple[str, list[RunRecord]]]:
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
    runs: list[RunRecord] = []
    thread_id: str | None = None
    try:
        for index in range(_TURNS):
            run: RunRecord = await manager.start(
                f"Question {index}: what does the sea do?",
                "task",
                SCRIPTED_MODEL,
                thread_id,
                "test",
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            thread_id = run.thread_id
            runs.append(run)
            async with asyncio.timeout(30):
                assert (await manager.wait(run.id)).status == "done"
        yield f"http://127.0.0.1:{port}", runs
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def test_a_chat_shows_every_turn_open_and_the_marks_go_anywhere(
    thread: tuple[str, list[RunRecord]], tmp_path: Path
) -> None:
    address, runs = thread
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{address}/#/runs/{runs[-1].id}")
            await page.wait_for_selector(".run-main", timeout=15000)
            # Folded, as Neutral folds a run's steps: each earlier turn says in one line what it ran.
            folded = page.locator(".turn-ops details.steps")
            await expect(folded).to_have_count(_TURNS - 1, timeout=15000)
            await expect(folded.locator(".steps-title")).to_have_text(["Ran 1 command"] * (_TURNS - 1))
            assert await page.locator(".turn-ops details.steps[open]").count() == 0
            await expect(page.locator(".timeline .steps-title").first).to_have_text("Ran 1 command")
            await page.screenshot(path=str(tmp_path / "chat-folded.png"))
            # Open, with Fold tool steps off: every call reads without a click.
            await page.evaluate("localStorage.setItem('dot.foldSteps', 'off')")
            await page.reload()
            await page.wait_for_selector(".turn-ops .tool", timeout=15000)
            await page.wait_for_timeout(600)

            assert await page.locator(".history .turn").count() == _TURNS - 1
            assert await page.locator(".turn-ops .tool").count() == _TURNS - 1, "every earlier turn shows what it did"
            assert await page.locator(".turn-ops .tool").first.is_visible()
            assert await page.locator(".answer-foot a", has_text="Open run").count() == 0, "no hop to a run's page"
            assert await page.get_by_text("Open run").count() == 0
            # The run's own steps are open, so its calls read without a click.
            assert await page.locator("details.steps[open]").count() >= 1
            assert await page.locator(".timeline .tool").first.is_visible()

            marks = page.locator(".outline-mark")
            assert await marks.count() == _TURNS
            rail = await page.locator(".outline-rail").bounding_box()
            assert rail is not None
            await page.screenshot(path=str(tmp_path / "chat-bottom.png"))

            first = await marks.first.bounding_box()
            assert first is not None
            await page.mouse.click(rail["x"] + rail["width"] / 2, first["y"] + first["height"] / 2)
            await page.wait_for_timeout(700)
            asked = page.locator(".bubble.user").first
            box = await asked.bounding_box()
            assert box is not None and box["y"] >= 0, "the first mark goes to the first message"

            last = await marks.last.bounding_box()
            assert last is not None
            await page.mouse.click(rail["x"] + rail["width"] / 2, last["y"] + last["height"] / 2)
            await page.wait_for_timeout(700)
            newest = page.locator(".bubble.user").last
            newest_box = await newest.bounding_box()
            assert newest_box is not None and 0 <= newest_box["y"] < 900, "the last mark goes to the newest prompt"
            assert await page.locator(".timeline .tool").first.is_visible(), "and its work stands under it"
            await page.screenshot(path=str(tmp_path / "chat-newest.png"))
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()


# More events than one read of `GET /runs/{id}/events` gives (2000), before the earlier run's last stretch of work.
_FILLER_EVENTS: int = 2100


async def test_an_earlier_turn_reads_all_of_its_events_past_one_page_of_them(
    runtime: Runtime, thread: tuple[str, list[RunRecord]], tmp_path: Path
) -> None:
    """A run with more events than one read gives shows what it did after them too (item 374): its last words, and its
    last call finished, not cut off. Its events are seeded: the run's own, then fillers, then a copy of its words and
    of its call."""
    address, runs = thread
    first: RunRecord = runs[0]
    events = await runtime.store.list_events(first.id, 0, 2000)
    call = next(event for event in events if event.kind == "tool_call" and event.data["tool"] == "shell")
    result = next(
        event for event in events if event.kind == "tool_result" and event.data["call_id"] == call.data["call_id"]
    )
    said = next(event for event in events if event.kind == "assistant")
    for number in range(_FILLER_EVENTS):
        await runtime.store.add_event(first.id, "model_call", {"purpose": "filler", "number": number})
    await runtime.store.add_event(first.id, "assistant", {**said.data, "text": "One more look at the shore."})
    await runtime.store.add_event(first.id, "tool_call", {**call.data, "call_id": "late-call"})
    await runtime.store.add_event(first.id, "tool_result", {**result.data, "call_id": "late-call"})
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
            await page.add_init_script(
                "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.foldSteps', 'off')"
            )
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{address}/#/runs/{runs[-1].id}")
            turn = page.locator(".history .turn").first
            await expect(turn.locator(".turn-say")).to_contain_text("One more look at the shore.", timeout=15000)
            await expect(turn.locator(".turn-ops details.steps")).to_have_count(2)
            await expect(turn.locator(".tool-state.ok")).to_have_count(2)
            await expect(turn.get_by_text("cut off")).to_have_count(0)
            await page.screenshot(path=str(tmp_path / "chat-long-turn.png"))
            await page.context.close()
        finally:
            await browser.close()


# A browser without scroll anchoring, as Safari is up to 26.4 (caniuse.com/css-overflow-anchor): the page keeps the
# reader's place itself there.
_NO_ANCHORING: str = """(() => {
  const supports = CSS.supports.bind(CSS);
  CSS.supports = (property, value) =>
    property === 'overflow-anchor' ? false : value === undefined ? supports(property) : supports(property, value);
  const sheet = new CSSStyleSheet();
  sheet.replaceSync('* { overflow-anchor: none !important; }');
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
})()"""


async def test_the_answer_stays_in_its_place_while_the_earlier_turns_fill_above_it(
    thread: tuple[str, list[RunRecord]],
) -> None:
    """The earlier turns' events come after the page landed on the answer (item 374): what they add above it moves it
    nowhere, with the browser's scroll anchoring and without it. On a phone, where the conversation scrolls in its
    own box."""
    address, runs = thread
    host, port = "127.0.0.1", int(address.rpartition(":")[2])

    async def late(route: Route) -> None:
        await asyncio.sleep(1.0)
        await route.continue_()

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for anchoring in (True, False):
                context = await browser.new_context(
                    viewport={"width": 390, "height": 420}, color_scheme="light", reduced_motion="reduce"
                )
                page: Page = await context.new_page()
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                if not anchoring:
                    await page.add_init_script(_NO_ANCHORING)
                for past in runs[:-1]:
                    await page.route(f"**/api/runs/{past.id}/events*", late)
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{runs[-1].id}")
                answer: Locator = page.locator(".timeline .answer").last
                await expect(answer).to_be_visible()
                await page.wait_for_timeout(300)
                landed = await answer.bounding_box()
                assert landed is not None
                await expect(page.locator(".history .turn-ops:not([hidden])")).to_have_count(_TURNS - 1, timeout=15000)
                await page.wait_for_timeout(300)
                after = await answer.bounding_box()
                assert after is not None
                assert abs(after["y"] - landed["y"]) <= 2, (anchoring, landed, after)
                await context.close()
        finally:
            await browser.close()


async def test_an_earlier_turn_says_what_failed_and_shows_a_finish_that_was_refused(
    runtime: Runtime, thread: tuple[str, list[RunRecord]], tmp_path: Path
) -> None:
    """An earlier turn's stretch of work says how many of its calls failed, as the run's own page does, and shows a
    finish that was refused, which the run's own page shows too (item 374). Its events are seeded."""
    address, runs = thread
    second: RunRecord = runs[1]
    events = await runtime.store.list_events(second.id, 0, 2000)
    call = next(event for event in events if event.kind == "tool_call" and event.data["tool"] == "shell")
    result = next(
        event for event in events if event.kind == "tool_result" and event.data["call_id"] == call.data["call_id"]
    )
    said = next(event for event in events if event.kind == "assistant")
    await runtime.store.add_event(second.id, "assistant", {**said.data, "text": "Trying the tide tables."})
    await runtime.store.add_event(second.id, "tool_call", {**call.data, "call_id": "failing-call"})
    await runtime.store.add_event(
        second.id, "tool_result", {**result.data, "call_id": "failing-call", "ok": False, "output": "no such table"}
    )
    finish: dict[str, object] = {"answer": "Too soon.", "success": True}
    await runtime.store.add_event(
        second.id, "tool_call", {**call.data, "tool": FINISH_TOOL, "call_id": "refused-finish", "args": finish}
    )
    await runtime.store.add_event(
        second.id,
        "tool_result",
        {
            **result.data,
            "tool": FINISH_TOOL,
            "call_id": "refused-finish",
            "ok": False,
            "output": "The goal is not met.",
        },
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
            await page.add_init_script(
                "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.foldSteps', 'off')"
            )
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{address}/#/runs/{runs[-1].id}")
            turn: Locator = page.locator(".history .turn").nth(1)
            await expect(turn.locator(".turn-say")).to_contain_text("Trying the tide tables.", timeout=15000)
            stretch: Locator = turn.locator(".turn-ops details.steps").last
            await expect(stretch.locator(".steps-head .badge")).to_have_text("2 failed")
            await expect(stretch.locator(".tool-name")).to_have_text(["shell", FINISH_TOOL])
            await expect(stretch.locator(".tool-state.bad")).to_have_count(2)
            # A failed call keeps the mark of its kind, in the failure's colour: no cross (item 403).
            await expect(stretch.locator(".tool-state [data-icon='x']")).to_have_count(0)
            await expect(stretch.locator(".tool-state [data-icon='terminal']")).to_have_count(1)
            await page.screenshot(path=str(tmp_path / "chat-failed-turn.png"))
            await page.context.close()
        finally:
            await browser.close()
