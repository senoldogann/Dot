"""The composer stands at the foot of what is seen while the conversation scrolls under it (item 374): on a wide window
the dock sticks to the window's foot and the page scrolls; on a phone the conversation alone scrolls between the top bar
and the dock; neither scrolls sideways. A real server on a scripted model, wide, dark and on a phone, light."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

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

TOKEN: str = "chat-scroll-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Write a long answer about the sea."
_ANSWER: str = "\n\n".join(
    f"Paragraph {index}: the sea keeps its own time, and the shore answers a little later every century."
    for index in range(40)
)
_RUN_WAIT_S: float = 30.0


def _script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _ANSWER, "success": True}),))
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
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script() * 8))
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


async def _page(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await page.wait_for_selector(".run-main", timeout=15000)
    await page.wait_for_timeout(600)
    return page, errors


_FACTS: str = """() => {
  const main = document.querySelector('.run-main');
  const thread = document.querySelector('.run-thread');
  const dock = document.querySelector('.dock');
  const style = (node) => node ? getComputedStyle(node) : null;
  const box = dock ? dock.getBoundingClientRect() : null;
  return {
    mainOverflowY: style(main)?.overflowY,
    threadOverflowY: style(thread)?.overflowY,
    dockPosition: style(dock)?.position,
    dockTop: box ? Math.round(box.top) : null,
    dockBottom: box ? Math.round(box.bottom) : null,
    threadScrollable: thread ? thread.scrollHeight - thread.clientHeight : 0,
    threadScrollTop: thread ? Math.round(thread.scrollTop) : 0,
    scrollY: Math.round(window.scrollY),
    sideways: document.documentElement.scrollWidth - window.innerWidth,
    threadSideways: thread ? thread.scrollWidth - thread.clientWidth : 0,
    dockBackground: style(dock)?.backgroundImage,
    glass: style(document.querySelector('.composer'))?.backdropFilter,
    threadPaddingBottom: thread ? Math.round(parseFloat(getComputedStyle(thread).paddingBottom)) : 0,
    dockHeight: box ? Math.round(box.height) : 0,
    answerBottom: Math.round(document.querySelector('.answer')?.getBoundingClientRect().bottom ?? 0),
  };
}"""


async def test_the_composer_stands_at_the_foot_of_what_is_seen_while_the_conversation_scrolls(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            wide, wide_errors = await _page(browser, address, run.id, 1280, "dark")
            facts = await wide.evaluate(_FACTS)
            assert facts["dockPosition"] == "sticky"
            assert facts["dockBottom"] <= 900 <= facts["dockBottom"] + 1
            assert facts["dockTop"] > 600
            assert facts["sideways"] <= 0
            assert facts["threadSideways"] <= 0
            await wide.screenshot(path=str(tmp_path / "wide-top.png"))
            await wide.evaluate("window.scrollTo(0, 700)")
            await wide.wait_for_timeout(300)
            scrolled = await wide.evaluate(_FACTS)
            assert scrolled["scrollY"] > 0
            assert scrolled["dockTop"] == facts["dockTop"], "the dock stays at the window's foot"
            await wide.screenshot(path=str(tmp_path / "wide-scrolled.png"))
            await wide.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            await wide.wait_for_timeout(300)
            assert (await wide.evaluate(_FACTS))["dockTop"] == facts["dockTop"]
            await wide.screenshot(path=str(tmp_path / "wide-bottom.png"))
            assert wide_errors == []
            await wide.context.close()

            phone, phone_errors = await _page(browser, address, run.id, 390, "light")
            facts = await phone.evaluate(_FACTS)
            assert facts["threadOverflowY"] == "auto"
            assert facts["threadScrollable"] > 0
            assert facts["dockBottom"] <= 900 <= facts["dockBottom"] + 1
            assert facts["sideways"] <= 0
            assert facts["dockPosition"] == "absolute", "the composer floats over the conversation"
            assert facts["dockBackground"] == "none", "no band of the page's colour behind the composer"
            assert "blur" in str(facts["glass"]), "the composer is a pane of glass"
            assert facts["threadPaddingBottom"] >= facts["dockHeight"], "the last message clears the composer"
            await phone.screenshot(path=str(tmp_path / "phone-top.png"))
            await phone.evaluate("document.querySelector('.run-thread').scrollTop = 700")
            await phone.wait_for_timeout(300)
            scrolled = await phone.evaluate(_FACTS)
            assert scrolled["threadScrollTop"] > 0
            assert scrolled["dockTop"] == facts["dockTop"], "the dock stays at the foot while the conversation scrolls"
            await phone.screenshot(path=str(tmp_path / "phone-scrolled.png"))
            await phone.evaluate(
                "const thread = document.querySelector('.run-thread'); thread.scrollTop = thread.scrollHeight"
            )
            await phone.wait_for_timeout(300)
            assert (await phone.evaluate(_FACTS))["dockTop"] == facts["dockTop"]
            await phone.screenshot(path=str(tmp_path / "phone-bottom.png"))
            # A draft of several lines makes the dock taller: the conversation's end still clears it.
            await phone.locator(".dock textarea").fill(
                "\n".join(f"Line {number} of a long draft" for number in range(1, 7))
            )
            await phone.wait_for_timeout(300)
            await phone.evaluate(
                "const thread = document.querySelector('.run-thread'); thread.scrollTop = thread.scrollHeight"
            )
            await phone.wait_for_timeout(300)
            grown = await phone.evaluate(_FACTS)
            assert grown["dockHeight"] > facts["dockHeight"] + 40, (grown, facts)
            assert grown["threadPaddingBottom"] >= grown["dockHeight"], grown
            assert grown["answerBottom"] <= grown["dockTop"], grown
            await phone.screenshot(path=str(tmp_path / "phone-draft.png"))
            assert phone_errors == []
            await phone.context.close()
        finally:
            await browser.close()


_AT_THE_FOOT: str = """() => {
  const thread = document.querySelector('.run-thread');
  const box = thread !== null && thread.scrollHeight > thread.clientHeight + 4 ? thread : document.scrollingElement;
  return { top: Math.round(box.scrollTop), max: Math.round(box.scrollHeight - box.clientHeight) };
}"""


async def test_a_chat_that_is_over_opens_at_its_foot_and_return_breaks_the_line_on_a_phone(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    """A finished chat opens on its newest message, wide and on a phone, not at the top of its last answer (item 440);
    and on a touch screen Return breaks the line, where the round send button sends, while a wide window keeps Enter
    for sending."""
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            shots: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "dark"), (390, "light"))
            for width, scheme in shots:
                page, errors = await _page(browser, address, run.id, width, scheme)
                await page.wait_for_timeout(900)
                where = await page.evaluate(_AT_THE_FOOT)
                assert where["max"] > 0, where
                assert where["max"] - where["top"] <= 2, (width, where)
                assert errors == []
                await page.context.close()
            # On a phone Return breaks the line and sends nothing; on a wide window Enter sends (item 440).
            for width, sends in ((390, False), (1280, True)):
                context: BrowserContext = await browser.new_context(
                    viewport={"width": width, "height": 900},
                    is_mobile=width < 500,
                    has_touch=width < 500,
                    reduced_motion="reduce",
                )
                page = await context.new_page()
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await page.wait_for_selector(".dock textarea", timeout=15000)
                box = page.locator(".dock textarea")
                await box.fill("first line")
                await box.press("Enter")
                await page.wait_for_timeout(500)
                value: str = await box.input_value()
                if sends:
                    assert value == "", (width, value)
                else:
                    assert value == "first line\n", (width, value)
                await context.close()
        finally:
            await browser.close()


# Where the conversation stands at every frame, from the moment it is sampled: the box it scrolls in on a phone, the
# window on a wider one (run.js's `scroller`).
_SAMPLE_SCROLL: str = """() => {
  window.dotTops = [];
  const sample = () => {
    const phone = window.matchMedia('(max-width: 720px)').matches;
    const box = phone ? document.querySelector('.run-thread') : document.scrollingElement;
    window.dotTops.push(box === null ? -1 : box.scrollTop);
    window.dotSampling = requestAnimationFrame(sample);
  };
  sample();
}"""


async def test_a_message_sent_from_a_chat_that_is_over_goes_in_at_its_foot_without_a_jump(
    served: tuple[RunManager, str],
) -> None:
    """A message sent from a chat that is over starts its next run (item 454): the chat stays where it stood, at its
    foot, until the next run's page is drawn in its place, and is never lifted to its top and dropped back on the way,
    wide and on a phone."""
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme in ((1280, "dark"), (390, "light")):
                page, errors = await _page(browser, address, run.id, width, scheme)
                await page.wait_for_timeout(600)
                assert (await page.evaluate(_AT_THE_FOOT))["max"] > 0
                await page.evaluate(_SAMPLE_SCROLL)
                box = page.locator(".dock textarea")
                await box.fill(f"One more look at the sea, {width}.")
                await page.locator(".dock .send").click()
                await page.wait_for_url(lambda url: run.id not in url, timeout=15000)
                sent = page.locator(".para", has_text=f"One more look at the sea, {width}.")
                await expect(sent).to_be_visible(timeout=15000)
                await page.wait_for_timeout(600)
                tops: list[int] = await page.evaluate("cancelAnimationFrame(window.dotSampling), window.dotTops")
                assert min(tops) >= tops[0] - 2, (width, tops[0], min(tops))
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()


# Whether the run's answer was in the conversation when the run's page first stood in the window.
_DRAWN_WHOLE: str = """(() => {
  const watch = new MutationObserver(() => {
    if (document.querySelector('.run-main') !== null) {
      window.dotDrawnWhole = document.querySelector('.timeline .answer') !== null;
      watch.disconnect();
    }
  });
  watch.observe(document, { childList: true, subtree: true });
})()"""


async def test_a_chat_that_is_over_opens_drawn_whole(served: tuple[RunManager, str]) -> None:
    """A chat whose run is over opens as it ended (item 454): its events are read before the page is drawn, so its
    answer is there with the page, not filled in as its stream comes."""
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page: Page = await (await browser.new_context(viewport={"width": 1280, "height": 900})).new_page()
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))
            await page.add_init_script(_DRAWN_WHOLE)
            await page.goto(f"{address}/#/runs/{run.id}")
            await page.reload()
            await page.wait_for_selector(".timeline .answer", timeout=15000)
            assert await page.evaluate("window.dotDrawnWhole") is True
            await page.context.close()
        finally:
            await browser.close()
