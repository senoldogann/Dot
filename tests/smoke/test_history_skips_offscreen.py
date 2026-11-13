"""The earlier turns of a long chat far from what is seen are not laid out again (item 1165): the conversation keeps its
exact height and each message its place, it opens at its foot, a turn far up takes the focus, and the marks at the
left show its words and go to it, in Chromium and in WebKit, on a phone and on a wide window. A real server on a
scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypedDict

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserType, Page, Playwright, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "history-skips-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: int = 30
_ANSWER: str = "\n\n".join(f"Line {line} of a long answer about the sea and its tides." for line in range(24))
_PHONE: dict[str, int] = {"width": 390, "height": 844}
_WIDE: dict[str, int] = {"width": 1280, "height": 900}
_SETTLE_MS: int = 1500
_FOCUS_LOOKS: int = 20
# A link in each answer: what of a turn takes the focus on a phone too, where its buttons are for screen readers only.
_LINK: str = "https://tides.test/table"
# Where every turn stands in the conversation and how tall the conversation is, from the box it scrolls in (the
# conversation's own on a phone, the window's on a wide one).
_PLACES: str = """() => {
  const phone = getComputedStyle(document.querySelector('.run-thread')).overflowY !== 'visible';
  const box = phone ? document.querySelector('.run-thread') : document.scrollingElement;
  const top = phone ? box.getBoundingClientRect().top - box.scrollTop : -window.scrollY;
  const turns = [...document.querySelectorAll('.history > .turn')];
  return { height: box.scrollHeight, at: box.scrollTop, room: box.clientHeight,
    tops: turns.map((turn) => Math.round(turn.getBoundingClientRect().top - top)) };
}"""


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
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chat(runtime: Runtime) -> AsyncIterator[tuple[str, list[RunRecord]]]:
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
    thread: ThreadRecord = await runtime.store.create_thread("A long chat", "test")
    runs: list[RunRecord] = []
    for index in range(_TURNS):
        made: RunRecord = await runtime.store.create_run(
            thread.id,
            f"Question {index} about the tides",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            "high",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        await runtime.store.finish_run(made.id, "done", f"Answer {index}, with [the tide table]({_LINK}).\n\n{_ANSWER}")
        runs.append(made)
    try:
        yield f"http://127.0.0.1:{port}", runs
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _opened(
    browser: Browser, address: str, run_id: str, viewport: dict[str, int], phone: bool, errors: list[str]
) -> Page:
    """The page of the run ``run_id``, signed in, once its earlier turns are drawn and have settled; what it throws
    from the start goes into ``errors``."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    page: Page = await browser.new_page(
        viewport=viewport, is_mobile=phone, has_touch=phone, color_scheme="light", reduced_motion="reduce"
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".history > .turn")).to_have_count(_TURNS - 1, timeout=15000)
    await page.wait_for_timeout(_SETTLE_MS)
    return page


def _engines(playwright: Playwright) -> tuple[BrowserType, ...]:
    return (playwright.chromium, playwright.webkit)


async def _skipping(page: Page) -> None:
    """Far turns skipped again, the conversation at its foot."""
    await page.locator(".history").evaluate("(history) => { history.dataset.sized = ''; }")
    await _at_foot(page)


async def _whole(page: Page) -> None:
    """Every turn laid out, the conversation at its foot."""
    await page.locator(".history").evaluate("(history) => { delete history.dataset.sized; }")
    await _at_foot(page)


async def _at_foot(page: Page) -> None:
    await page.evaluate(
        "() => { for (const box of [document.querySelector('.run-thread'), document.scrollingElement])"
        " box.scrollTop = box.scrollHeight; }"
    )
    await page.wait_for_timeout(300)


async def _focused_in_sight(page: Page) -> bool:
    """Whether the link of the oldest turn's answer is in the window once it takes the focus from the foot."""
    control = page.locator(".history > .turn").first.locator(f"a[href='{_LINK}']")
    await page.evaluate("() => document.activeElement?.blur()")
    await control.focus()
    assert await control.evaluate("(node) => node === document.activeElement")
    # A phone's conversation may scroll there smoothly: it is looked at until it is there, or for at most a while.
    for _ in range(_FOCUS_LOOKS):
        if await control.evaluate(
            "(node) => { const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; }"
        ):
            return True
        await page.wait_for_timeout(100)
    return False


async def _jumped_at(page: Page) -> float:
    """Where the oldest message stands once its mark, at the top of the column, is clicked from the foot; its words
    are in the preview first."""
    rail = page.locator(".outline-rail")
    await expect(rail).to_be_visible()
    box = await rail.bounding_box()
    assert box is not None
    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 1)
    await expect(page.locator(".outline-preview")).to_contain_text("Question 0 about the tides")
    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + 1)
    await page.wait_for_timeout(800)
    oldest = page.locator(".history > .turn").first.locator(".bubble.user")
    await expect(oldest).to_be_in_viewport()
    return await oldest.evaluate("(node) => node.getBoundingClientRect().top")


@pytest.mark.parametrize("phone", [True, False], ids=["phone", "wide"])
async def test_far_turns_are_skipped_and_the_chat_keeps_its_height_its_places_its_focus_and_its_words(
    chat: tuple[str, list[RunRecord]], phone: bool
) -> None:
    address, runs = chat
    errors: list[str] = []
    async with async_playwright() as playwright:
        for engine in _engines(playwright):
            browser: Browser = await engine.launch()
            try:
                page: Page = await _opened(browser, address, runs[-1].id, _PHONE if phone else _WIDE, phone, errors)
                history = page.locator(".history")
                await expect(history).to_have_attribute("data-sized", "", timeout=5000)
                first = page.locator(".history > .turn").first
                assert await first.evaluate("(turn) => getComputedStyle(turn).contentVisibility") == "auto", engine.name
                skipped: dict[str, object] = await page.evaluate(_PLACES)
                assert skipped["height"] - skipped["at"] - skipped["room"] < 4, (engine.name, skipped)
                # Every turn laid out again: the conversation is exactly as tall and each turn where it was.
                await history.evaluate("(history) => { delete history.dataset.sized; }")
                await page.wait_for_timeout(300)
                whole: dict[str, object] = await page.evaluate(_PLACES)
                assert whole["height"] == skipped["height"], (engine.name, whole["height"], skipped["height"])
                assert whole["tops"] == skipped["tops"], engine.name
                # The oldest turn takes the focus from the foot and comes into sight, as when every turn is laid out.
                await _at_foot(page)
                assert await _focused_in_sight(page), (engine.name, "every turn laid out")
                await _skipping(page)
                assert await _focused_in_sight(page), (engine.name, "far turns skipped")
                await page.context.close()
            finally:
                await browser.close()
    assert errors == []


async def test_the_marks_go_to_a_message_of_a_far_turn(chat: tuple[str, list[RunRecord]]) -> None:
    address, runs = chat
    errors: list[str] = []
    async with async_playwright() as playwright:
        for engine in _engines(playwright):
            browser: Browser = await engine.launch()
            try:
                page: Page = await _opened(browser, address, runs[-1].id, _WIDE, False, errors)
                await expect(page.locator(".history")).to_have_attribute("data-sized", "", timeout=5000)
                # The mark of the oldest message shows its words and goes to it as when every turn is laid out.
                await _whole(page)
                whole_top: float = await _jumped_at(page)
                await _skipping(page)
                skipped_top: float = await _jumped_at(page)
                assert abs(skipped_top - whole_top) <= 2, (engine.name, skipped_top, whole_top)
                await page.context.close()
            finally:
                await browser.close()
    assert errors == []


# A chat whose earlier turns each said something long on the way and ran a command: their work, read only as each comes
# near what is seen (history.js, item 708), makes them grow while the reader scrolls up.
_FILLING_TURNS: int = 24
_SAID: str = "\n\n".join(f"Paragraph {line} of what the run said on its way to the tides." for line in range(8))
_SCROLL_STEP: int = 500
_FILL_WAIT_MS: int = 900
# What the page reads before it is drawn (history.js READ_FIRST); a turn above them reads its work as it comes near.
_READ_FIRST: int = 6
# The message read: the one whose top is nearest the middle of what is seen, in the box the conversation scrolls in.
_READ_MESSAGE: str = """() => {
  const phone = getComputedStyle(document.querySelector('.run-thread')).overflowY !== 'visible';
  const box = phone ? document.querySelector('.run-thread') : document.scrollingElement;
  const middle = innerHeight / 2;
  const messages = [...document.querySelectorAll('.run-thread .bubble.user')];
  let best = -1;
  messages.forEach((node, index) => { const top = node.getBoundingClientRect().top;
    const off = (at) => Math.abs(at - middle);
    const nearer = best === -1 || off(top) < off(messages[best].getBoundingClientRect().top);
    if (top > 0 && top < innerHeight && nearer) best = index; });
  return { index: best, top: best === -1 ? null : messages[best].getBoundingClientRect().top, at: box.scrollTop };
}"""
_SCROLL_UP: str = """(step) => {
  const phone = getComputedStyle(document.querySelector('.run-thread')).overflowY !== 'visible';
  const box = phone ? document.querySelector('.run-thread') : document.scrollingElement;
  box.scrollTop = box.scrollTop - step;
}"""


class _Seen(TypedDict):
    """The message read (its index among the conversation's, -1 for none in sight), its top, and the box's scroll."""

    index: int
    top: float | None
    at: float


_TOP_OF: str = "(index) => document.querySelectorAll('.run-thread .bubble.user')[index].getBoundingClientRect().top"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def filling(runtime: Runtime, chat: tuple[str, list[RunRecord]]) -> tuple[str, list[RunRecord]]:
    address, _ = chat
    thread: ThreadRecord = await runtime.store.create_thread("A chat that fills in", "test")
    runs: list[RunRecord] = []
    for index in range(_FILLING_TURNS):
        made: RunRecord = await runtime.store.create_run(
            thread.id,
            f"Filling question {index}",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            "high",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        await runtime.store.add_event(made.id, "assistant", {"text": _SAID, "step": 1, "tool_calls": []})
        await runtime.store.add_event(
            made.id, "tool_call", {"call_id": "call_1", "tool": "shell", "args": {"command": f"echo tide {index}"}}
        )
        await runtime.store.add_event(
            made.id, "tool_result", {"call_id": "call_1", "tool": "shell", "ok": True, "seconds": 0.1}
        )
        await runtime.store.finish_run(made.id, "done", f"Filling answer {index}.")
        runs.append(made)
    return address, runs


@pytest.mark.parametrize("phone", [True, False], ids=["phone", "wide"])
async def test_scrolling_up_a_chat_whose_turns_fill_in_keeps_the_message_read_where_it_is(
    filling: tuple[str, list[RunRecord]], phone: bool
) -> None:
    """In Chromium. WebKit moves the message read by 24 to 284 px as the turns above it fill in, on `main` as with this
    item, measured alike: it says it anchors scrolling (`overflow-anchor`) and does not here, so `keepPlace` (item 708)
    leaves it to it. That is the next item's."""
    address, runs = filling
    read: list[str] = []
    async with async_playwright() as playwright:
        for engine in (playwright.chromium,):
            browser: Browser = await engine.launch()
            try:
                page: Page = await browser.new_page(
                    viewport=_PHONE if phone else _WIDE, is_mobile=phone, has_touch=phone, reduced_motion="reduce"
                )
                read.clear()
                page.on("request", lambda request: read.append(request.url) if "/events" in request.url else None)
                host, port = "127.0.0.1", int(address.rpartition(":")[2])
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{runs[-1].id}")
                await expect(page.locator(".history > .turn")).to_have_count(_FILLING_TURNS - 1, timeout=15000)
                await expect(page.locator(".history")).to_have_attribute("data-sized", "", timeout=5000)
                await page.wait_for_timeout(_SETTLE_MS)
                opened: int = len(read)
                moves: list[tuple[int, float, float]] = []
                for _ in range(40):
                    seen: _Seen = await page.evaluate(_READ_MESSAGE)
                    if seen["at"] < _SCROLL_STEP:
                        break
                    await page.evaluate(_SCROLL_UP, _SCROLL_STEP)
                    await page.wait_for_timeout(_FILL_WAIT_MS)
                    if seen["top"] is None:
                        continue
                    now: float = await page.evaluate(_TOP_OF, seen["index"])
                    moves.append((seen["index"], seen["top"] + _SCROLL_STEP, now))
                # The turns above those read first filled in as the reader came up to them.
                assert len(read) > opened, (engine.name, opened, len(read))
                assert moves != [], engine.name
                jumped = [move for move in moves if abs(move[2] - move[1]) > 2]
                assert jumped == [], (engine.name, jumped)
                await page.context.close()
            finally:
                await browser.close()
