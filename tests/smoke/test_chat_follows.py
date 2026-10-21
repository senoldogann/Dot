"""A chat open on two screens goes on together (item 451): a message sent on the phone shows at once on the wide
window, and one sent on the wide window at once on the phone, without leaving the chat and coming back; what was typed
and not sent on the other screen goes with it; a page of an earlier run, opened by its address as the sidebar's
Activity opens one, stays where it is; and a phone's page that the browser loads again after it showed the chat opens
where the chat is now. A real server on a scripted model, Chrome wide and WebKit as a phone."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

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
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-follows-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FIRST: str = "Name three harbours of the north."
_FROM_PHONE: str = "And one in the south, from the phone."
_FROM_WIDE: str = "And one in the west, from the wide window."
_DRAFT: str = "half a thought typed on the wide window"
_ASKS: str = "Find the harbour masters, and ask me what you need."
_QUESTIONS: tuple[str, str] = ("Which coast first?", "How many names?")
_RUN_WAIT_S: float = 30.0
# How long a screen may take to show a message sent on the other: the lifecycle event and one read.
_FOLLOW_MS: int = 10_000


def _script() -> list[ScriptedTurn]:
    """The first run, the message sent on the phone and the one sent on the wide window, each ending at once; then a run
    that leaves two questions and ends, and the run each answer starts."""
    done: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    asks: list[ScriptedTurn] = [
        ScriptedTurn(
            text="",
            calls=(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": question, "options": [], "wait": True}),),
        )
        for question in _QUESTIONS
    ]
    return [done, done, done, *asks, done, done, done]


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
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    manager: RunManager = RunManager(runtime)
    try:
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _screen(browser: Browser, address: str, path: str, phone: bool) -> Page:
    """A screen with the chat page at ``path``, opened by its address as the side menu opens it."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 800} if phone else {"width": 1280, "height": 800},
        is_mobile=phone,
        has_touch=phone,
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#{path}")
    await page.wait_for_selector(".run-main", timeout=15000)
    return page


async def _reloaded_screen(browser: Browser, address: str, run: RunRecord) -> Page:
    """A phone's page at ``run`` that the browser loaded again after it had shown it as its chat's newest run: the tab
    kept that it did (``dot.chatShown.<thread>`` in its session storage)."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 800}, is_mobile=True, has_touch=True
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.add_init_script(f"sessionStorage.setItem('dot.chatShown.{run.thread_id}', '{run.id}')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs/{run.id}")
    await page.wait_for_selector(".run-main", timeout=15000)
    return page


async def _send(page: Page, text: str) -> None:
    await page.locator(".dock textarea").fill(text)
    await page.locator(".dock .btn.send").click()


async def _shows(page: Page, run: RunRecord, text: str) -> None:
    """The page went on at ``run``, whose message ``text`` it shows."""
    await page.wait_for_function("hash => location.hash === hash", arg=f"#/runs/{run.id}", timeout=_FOLLOW_MS)
    await expect(page.locator(".run-main .bubble, .run-main .user-message").filter(has_text=text).first).to_be_visible(
        timeout=_FOLLOW_MS
    )


async def _newest(runtime: Runtime, thread_id: str, before: set[str]) -> RunRecord:
    """The run the chat started after ``before``, once it is done: read from the store, since the manager may not hold
    the run yet when the store lists it."""
    async with asyncio.timeout(_RUN_WAIT_S):
        while True:
            runs: list[RunRecord] = await runtime.store.list_runs(10, thread_id)
            fresh: list[RunRecord] = [run for run in runs if run.id not in before and run.parent_run_id is None]
            if fresh and fresh[0].status == "done":
                return fresh[0]
            await asyncio.sleep(0.1)


async def test_a_message_sent_on_one_screen_shows_at_once_on_the_other(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    manager, address = served
    first: RunRecord = await manager.start(
        _FIRST, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(first.id)).status == "done"
    async with async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch()
        webkit: Browser = await playwright.webkit.launch()
        try:
            wide: Page = await _screen(chromium, address, f"/runs/{first.id}", False)
            phone: Page = await _screen(webkit, address, f"/runs/{first.id}", True)
            # Something typed on the wide window and not sent.
            await wide.locator(".dock textarea").fill(_DRAFT)

            known: set[str] = {first.id}
            await _send(phone, _FROM_PHONE)
            south: RunRecord = await _newest(runtime, first.thread_id, known)
            known.add(south.id)
            await _shows(phone, south, _FROM_PHONE)
            await _shows(wide, south, _FROM_PHONE)
            # What was typed on the wide window went with the chat.
            await expect(wide.locator(".dock textarea")).to_have_value(_DRAFT)

            # The first run, an earlier one now, opened by its address as the sidebar's Activity opens a run that left a
            # question: it stays there, and as the chat goes on.
            earlier: Page = await _screen(chromium, address, f"/runs/{first.id}", False)
            await earlier.wait_for_timeout(1000)
            assert await earlier.evaluate("() => location.hash") == f"#/runs/{first.id}"

            await _send(wide, _FROM_WIDE)
            west: RunRecord = await _newest(runtime, first.thread_id, known)
            await _shows(wide, west, _FROM_WIDE)
            await _shows(phone, west, _FROM_WIDE)

            assert await earlier.evaluate("() => location.hash") == f"#/runs/{first.id}"
            # A phone's page put away while it showed the chat at the run before, loaded again by the browser: it opens
            # where the chat is now.
            reloaded: Page = await _reloaded_screen(webkit, address, south)
            await _shows(reloaded, west, _FROM_WIDE)

            # A run that left two questions, the newest of its chat, open on both screens: the answer to the first
            # starts the chat's next run, and both pages stay for the second; the answer to the last, sent on the phone,
            # opens the chat's newest run there and on the wide window.
            asked: RunRecord = await manager.start(
                _ASKS, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
            )
            await _newest(runtime, asked.thread_id, set())
            questions: Page = await _screen(chromium, address, f"/runs/{asked.id}", False)
            other: Page = await _screen(webkit, address, f"/runs/{asked.id}", True)
            cards: Locator = questions.get_by_label("Dot asked you").locator(".approval")
            other_cards: Locator = other.get_by_label("Dot asked you").locator(".approval")
            await expect(cards).to_have_count(2)
            await expect(other_cards).to_have_count(2)
            await cards.first.get_by_label("Your answer").fill("The north coast.")
            await cards.first.get_by_role("button", name="Send answer").click()
            first_answer: RunRecord = await _newest(runtime, asked.thread_id, {asked.id})
            await expect(cards).to_have_count(1)
            await expect(other_cards).to_have_count(1)
            await questions.wait_for_timeout(1500)
            for page in (questions, other):
                assert await page.evaluate("() => location.hash") == f"#/runs/{asked.id}"
            await other_cards.first.get_by_label("Your answer").fill("Ten names.")
            await other_cards.first.get_by_role("button", name="Send answer").click()
            last_answer: RunRecord = await _newest(runtime, asked.thread_id, {asked.id, first_answer.id})
            await other.wait_for_function(
                "hash => location.hash === hash", arg=f"#/runs/{last_answer.id}", timeout=_FOLLOW_MS
            )
            await questions.wait_for_function(
                "hash => location.hash === hash", arg=f"#/runs/{last_answer.id}", timeout=_FOLLOW_MS
            )
        finally:
            await chromium.close()
            await webkit.close()
