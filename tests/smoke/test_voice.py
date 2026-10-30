"""Voice (item 574) in a real browser, wide and on a phone, against a real server and a scripted model, with the
browser's speech recognition and speech synthesis stood in for by objects the test drives: the page's own code runs
against them.

The microphone shows where the browser can listen. It fills the box as the owner speaks, the words the engine may still
change quieter, and stops on a second press, after a few seconds of silence, when the owner types and when a message is
sent; nothing is sent by voice alone. The language is the page's or the one chosen in Settings › General. Read answers
aloud is off until the switch is on; then an answer that comes in is read, in that language, and the speaker of each
answer reads it again or stops it."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish
from playwright.async_api import Browser, BrowserContext, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "voice-test-token"
GOAL: str = "textarea[aria-label='Goal']"
MESSAGE: str = "textarea[aria-label^='Message for']"
_SILENCE_WAIT_S: float = 6.6
# How far past the screen's edge the page may reach: none, but the rounding of a fraction.
_OVER_PX: int = 1
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
# What the page's code runs against in the place of the browser's speech recognition and speech synthesis; the test
# reads what the page asked of them from `window.__voice` and has them hear, fail and finish.
_STAND_INS: str = """
(() => {
  const state = {
    recognitions: [], starts: 0, stops: 0, aborts: 0, langs: [], spoken: [], chosen: [], cancels: 0, queue: [],
    voices: [],
  };
  window.__voice = state;
  class FakeRecognition extends EventTarget {
    constructor() {
      super();
      this.lang = "";
      this.continuous = false;
      this.interimResults = false;
      this.maxAlternatives = 1;
      state.recognitions.push(this);
    }
    start() {
      state.starts += 1;
      state.langs.push(this.lang);
      // A browser that still asks for the microphone begins later, or never (item 603).
      if (!state.startLater) {
        this.dispatchEvent(new Event("start"));
      }
    }
    stop() {
      state.stops += 1;
      setTimeout(() => this.dispatchEvent(new Event("end")), state.endAfter ?? 20);
    }
    abort() {
      state.aborts += 1;
      setTimeout(() => this.dispatchEvent(new Event("end")), 20);
    }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  // The engine hears `pairs` of [words, settled]: every result of its session so far, as a real one reports them.
  state.hear = (pairs) => {
    const results = pairs.map(([transcript, isFinal]) => Object.assign([{ transcript, confidence: 0.9 }], { isFinal }));
    const event = new Event("result");
    event.resultIndex = 0;
    event.results = results;
    state.recognitions.at(-1).dispatchEvent(event);
  };
  state.fail = (code, message) => {
    const event = new Event("error");
    event.error = code;
    if (message !== undefined) {
      event.message = message;
    }
    state.recognitions.at(-1).dispatchEvent(event);
  };
  state.interimWords = () => [...(CSS.highlights.get("md-interim") ?? [])].map((range) => range.toString());
  class FakeUtterance extends EventTarget {
    constructor(text) {
      super();
      this.text = text;
      this.lang = "";
      this.voice = null;
    }
  }
  window.SpeechSynthesisUtterance = FakeUtterance;
  const synth = {
    speak(utterance) {
      state.spoken.push({ text: utterance.text, lang: utterance.lang });
      state.chosen.push(utterance.voice === null ? null : utterance.voice.name);
      state.queue.push(utterance);
    },
    cancel() {
      state.cancels += 1;
      const waiting = state.queue;
      state.queue = [];
      for (const utterance of waiting) {
        const event = new Event("error");
        event.error = "canceled";
        utterance.dispatchEvent(event);
      }
    },
    getVoices: () => state.voices,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
  // The voice has said everything it was given.
  state.finishSpeech = () => {
    const waiting = state.queue;
    state.queue = [];
    for (const utterance of waiting) {
      utterance.dispatchEvent(new Event("end"));
    }
  };
})();
"""
# A browser with neither: Firefox, the desktop shell.
_NO_VOICE: str = """
delete window.SpeechRecognition;
delete window.webkitSpeechRecognition;
delete window.speechSynthesis;
delete window.SpeechSynthesisUtterance;
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def replies() -> Replies:
    return Replies()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, replies: Replies) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nlessons = "off"\n'
        "playbooks = false\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(PROVIDER, replies)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
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
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


@pytest.fixture(autouse=True)
def _fresh_replies(replies: Replies) -> None:
    replies.clear()


async def _open(
    browser: Browser,
    address: str,
    width: int,
    stand_ins: str = _STAND_INS,
    scheme: Literal["light", "dark"] = "light",
    clock: bool = False,
) -> Page:
    """A page signed in at the home, in a browser of its own, with `stand_ins` in the place of the voice; with `clock`,
    one whose time the test moves (`page.clock.run_for`)."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await context.add_init_script(stand_ins)
    page: Page = await context.new_page()
    if clock:
        await page.clock.install()
    port: int = int(address.rpartition(":")[2])
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await expect(page.locator(GOAL)).to_be_visible()
    if clock:
        # From here the page's time moves only as the test moves it.
        await page.clock.pause_at(await page.evaluate("() => Date.now() + 500"))
    return page


async def _open_app(
    browser: Browser, address: str, allow: dict[str, object] | int | None, width: int = 1280
) -> tuple[Page, list[object]]:
    """A page in Dot.app's shell (the `__TAURI__` global the desktop app gives it), `width` wide, whose ask that the Mac
    show System Settings (`POST /api/voice/allow`, item 636) the test answers and the daemon never sees: with `allow` as
    the body of a 200, with a status for a failure, or with a connection that breaks for None. Also what the page asked,
    in order, as the bodies of its requests."""
    asked: list[object] = []

    async def answer(route: Route) -> None:
        asked.append(route.request.post_data_json)
        if allow is None:
            await route.abort()
        elif isinstance(allow, int):
            await route.fulfill(
                status=allow, json={"message": "Dot failed on an error it did not expect", "context": {}}
            )
        else:
            await route.fulfill(json=allow)

    page: Page = await _open(browser, address, width, f"window.__TAURI__ = {{}};\n{_STAND_INS}")
    await expect(page.locator("html")).to_have_attribute("data-shell", "desktop")
    await page.route("**/api/voice/allow", answer)
    return page, asked


async def _toast_fits(page: Page, width: int, label: str) -> None:
    """The toast stands inside the window, and the page does not reach past its edge for it."""
    box = await page.locator(".toast").first.bounding_box()
    assert box is not None, f"{label}: no toast"
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, f"{label}: the toast reaches outside the window: {box}"
    await _fits(page, label)


async def _watch_allow(page: Page) -> list[object]:
    """What a page that is not Dot.app asks of `POST /api/voice/allow`, which it must not: every request is written down
    and fails."""
    asked: list[object] = []

    async def answer(route: Route) -> None:
        asked.append(route.request.post_data_json)
        await route.fulfill(status=500, json={"message": "this page must not ask", "context": {}})

    await page.route("**/api/voice/allow", answer)
    return asked


async def _voice(page: Page, expression: str) -> Any:
    """What the stand-ins hold or do, by an expression over `window.__voice`."""
    return await page.evaluate(f"() => {{ const voice = window.__voice; return {expression}; }}")


async def _fits(page: Page, label: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    assert over <= _OVER_PX, f"{label}: {over} px past the screen's edge"


async def _run_count(runtime: Runtime) -> int:
    return len(await runtime.store.list_runs(1000, None))


def _microphone(page: Page) -> Locator:
    return page.locator(".composer .mic-button")


def _recorder(page: Page) -> Locator:
    return page.locator(".composer .mic-recorder")


async def test_the_microphone_records_and_puts_the_words_in_the_box_when_the_owner_is_done(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    """Item 605: pressed, the microphone turns the composer's controls into a recorder, as the owner's other apps do: a
    cross, a wave, the time since the engine began to listen and a tick. The words go into the box when the owner
    presses the tick; until then the box keeps what it held, and nothing is sent by voice alone."""
    for width in (1280, 390):
        runs: int = await _run_count(runtime)
        page: Page = await _open(browser, address, width)
        box: Locator = page.locator(GOAL)
        mic: Locator = _microphone(page)
        recorder: Locator = _recorder(page)
        await expect(mic).to_be_visible()
        await expect(recorder).to_be_hidden()
        await expect(mic).to_have_attribute("aria-label", "Dictate your message")
        await expect(mic).to_have_attribute("aria-pressed", "false")
        await mic.click()
        await expect(recorder).to_be_visible()
        # The recorder stands in the place of the controls: the microphone, the send and the choices give it their line.
        await expect(mic).to_be_hidden()
        await expect(page.locator(".composer .send")).to_be_hidden()
        await expect(mic).to_have_attribute("aria-pressed", "true")
        await expect(recorder.get_by_role("button", name="Cancel the dictation")).to_be_visible()
        await expect(recorder.locator(".mic-time")).to_have_text(re.compile(r"^0:0\d$"))
        # The clock counts from the moment the engine began to listen.
        await expect(recorder.locator(".mic-time")).to_have_text("0:01", timeout=4000)
        assert await page.locator(".composer .mic-bar").count() > 20
        await _fits(page, f"{width} while the microphone listens")
        # The page's language, given its region (item 593).
        assert await _voice(page, "[voice.starts, voice.langs[0]]") == [1, "en-US"]
        # The engine is asked for what makes a dictation: the session goes on and the words come as they are heard.
        assert await _voice(page, "[voice.recognitions[0].continuous, voice.recognitions[0].interimResults]") == [
            True,
            True,
        ]
        await page.evaluate("() => window.__voice.hear([['hello world', true], ['how are', false]])")
        await asyncio.sleep(0.2)
        await expect(box).to_have_value("")
        await page.evaluate("() => window.__voice.hear([['hello world', true], ['how are you', true]])")
        await recorder.get_by_role("button", name="Put the words in the box").click()
        # The engine settles what it heard and ends a moment later; the words go into the box then.
        await expect(box).to_have_value("hello world how are you")
        await expect(recorder).to_be_hidden()
        await expect(mic).to_be_visible()
        await expect(mic).to_have_attribute("aria-pressed", "false")
        assert await _voice(page, "[voice.stops, voice.aborts]") == [1, 0]
        # The box is the composer's own: the send is ready, and no run started.
        await expect(page.locator(".composer")).not_to_have_attribute("data-empty", "")
        assert await _run_count(runtime) == runs
        await _fits(page, f"{width} with the microphone")
        await page.context.close()


async def test_a_dictated_message_is_sent_as_the_owner_sends_any(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    replies.push(finish("It is sunny in Oulu."))
    runs: int = await _run_count(runtime)
    page: Page = await _open(browser, address, 1280)
    await _microphone(page).click()
    await page.evaluate("() => window.__voice.hear([['what is the weather in Oulu', true]])")
    await _recorder(page).get_by_role("button", name="Put the words in the box").click()
    await expect(page.locator(GOAL)).to_have_value("what is the weather in Oulu")
    assert await _run_count(runtime) == runs
    await page.locator(GOAL).press("Enter")
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("It is sunny in Oulu.")
    assert await _run_count(runtime) == runs + 1
    newest = (await runtime.store.list_runs(1, None))[0]
    assert newest.goal == "what is the weather in Oulu"
    # The dictation had ended: the message took nothing from the engine, and the run page's box has its own.
    assert await _voice(page, "[voice.stops, voice.aborts]") == [1, 0]
    await expect(_microphone(page)).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(MESSAGE)).to_have_value("")
    await page.context.close()


async def test_the_box_keeps_what_it_held_and_typing_or_the_cross_drops_the_dictation(
    browser: Browser, address: str
) -> None:
    page: Page = await _open(browser, address, 1280)
    box: Locator = page.locator(GOAL)
    mic: Locator = _microphone(page)
    recorder: Locator = _recorder(page)
    await box.fill("Dear Ada,")
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['thanks for the plan', true]])")
    await expect(box).to_have_value("Dear Ada,")
    # The cross drops what was heard: the box keeps what it held.
    await recorder.get_by_role("button", name="Cancel the dictation").click()
    await expect(recorder).to_be_hidden()
    assert await _voice(page, "voice.aborts") == 1
    await asyncio.sleep(0.2)
    await expect(box).to_have_value("Dear Ada,")
    # What the owner types takes over too: the dictation is dropped and what the box holds stays.
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['something else entirely', true]])")
    await box.press_sequentially(" and the")
    await expect(recorder).to_be_hidden()
    assert await _voice(page, "voice.aborts") == 2
    await page.evaluate("() => window.__voice.hear([['more words', true]])")
    await asyncio.sleep(0.2)
    await expect(box).to_have_value("Dear Ada, and the")
    # A dictation the owner ends with the tick goes after what the box held.
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['plan', true]])")
    await recorder.get_by_role("button", name="Put the words in the box").click()
    await expect(box).to_have_value("Dear Ada, and the plan")
    await page.context.close()


async def test_the_microphone_ends_by_itself_after_a_pause_with_what_it_heard(browser: Browser, address: str) -> None:
    """Item 605: once a word is heard, a pause of fifteen seconds ends the dictation, and its words go into the box;
    six seconds stop only a dictation that heard nothing."""
    page: Page = await _open(browser, address, 1280, clock=True)
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['a few words', true]])")
    await page.clock.run_for(6_600)
    await expect(mic).to_have_attribute("aria-pressed", "true")
    await page.clock.run_for(9_000)
    await expect(mic).to_have_attribute("aria-pressed", "false")
    assert await _voice(page, "[voice.stops, voice.aborts]") == [1, 0]
    await expect(page.locator(GOAL)).to_have_value("a few words")
    # It heard words, so it says nothing of a silence (item 593).
    await expect(page.locator(".toast")).to_have_count(0)
    await page.context.close()


async def test_the_tick_waits_for_the_engine_to_settle_what_it_heard(browser: Browser, address: str) -> None:
    """Item 605: the engine settles the words it may still change after its stop. The recorder stays, its buttons off,
    and the settled words go into the box when the engine ends; one that does not end within three seconds is ended
    with the words it has."""
    page: Page = await _open(browser, address, 1280, clock=True)
    mic: Locator = _microphone(page)
    recorder: Locator = _recorder(page)
    done: Locator = recorder.get_by_role("button", name="Put the words in the box")
    await page.evaluate("() => { window.__voice.endAfter = 1000; }")
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['hello wor', false]])")
    # The tick pressed by the keyboard.
    await done.focus()
    await page.keyboard.press("Enter")
    await expect(recorder).to_be_visible()
    await expect(done).to_be_disabled()
    await expect(page.locator(GOAL)).to_have_value("")
    # The engine settles what it may still change, then ends: the settled words go into the box.
    await page.evaluate("() => window.__voice.hear([['hello world', true]])")
    await page.clock.run_for(1_100)
    await expect(page.locator(GOAL)).to_have_value("hello world")
    await expect(recorder).to_be_hidden()
    # The keyboard's focus the tick had goes to the box with the words.
    await expect(page.locator(GOAL)).to_be_focused()
    # An engine that does not end: three seconds after the tick, the dictation ends with what it heard.
    await page.locator(GOAL).fill("")
    await page.locator(GOAL).blur()
    await page.evaluate("() => { window.__voice.endAfter = 60000; }")
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['still here', true]])")
    await done.click()
    await page.clock.run_for(2_900)
    await expect(recorder).to_be_visible()
    await page.clock.run_for(200)
    await expect(recorder).to_be_hidden()
    await expect(page.locator(GOAL)).to_have_value("still here")
    assert await _voice(page, "voice.aborts") == 1
    # A tick pressed by the pointer leaves the focus be: a phone opens no keyboard on the screen for it.
    await expect(page.locator(GOAL)).not_to_be_focused()
    await page.context.close()


async def test_a_tick_before_the_engine_began_leaves_no_wait_that_ends_the_next_dictation(
    browser: Browser, address: str
) -> None:
    """The pre-push review of item 605: a tick pressed while the browser still asks drops the dictation at once, and its
    wait for the engine was armed after that, for nobody: three seconds later it ended the next dictation."""
    page: Page = await _open(browser, address, 1280, clock=True)
    mic: Locator = _microphone(page)
    recorder: Locator = _recorder(page)
    await page.evaluate("() => { window.__voice.startLater = true; }")
    await mic.click()
    await recorder.get_by_role("button", name="Put the words in the box").click()
    await expect(recorder).to_be_hidden()
    assert await _voice(page, "voice.aborts") == 1
    await page.evaluate("() => { window.__voice.startLater = false; }")
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['still speaking', true]])")
    await page.clock.run_for(3_100)
    await expect(mic).to_have_attribute("aria-pressed", "true")
    assert await _voice(page, "voice.aborts") == 1
    await page.context.close()


async def test_a_microphone_pressed_by_the_keyboard_gives_its_focus_to_the_tick(browser: Browser, address: str) -> None:
    """Item 605: the microphone goes out of sight while it listens, so the focus of a keyboard that pressed it goes to
    the tick, and back to the box when the recorder ends."""
    page: Page = await _open(browser, address, 1280)
    mic: Locator = _microphone(page)
    recorder: Locator = _recorder(page)
    await mic.focus()
    await page.keyboard.press("Enter")
    await expect(recorder.get_by_role("button", name="Put the words in the box")).to_be_focused()
    await recorder.get_by_role("button", name="Cancel the dictation").focus()
    await page.keyboard.press("Enter")
    await expect(recorder).to_be_hidden()
    await expect(page.locator(GOAL)).to_be_focused()
    await page.context.close()


async def test_a_microphone_the_browser_refuses_says_so_and_so_does_a_dictation_that_heard_nothing(
    browser: Browser, address: str
) -> None:
    """Item 593: the owner spoke in Chrome and nothing came, without a word of why: a dictation that hears nothing says
    so and what to check; one the owner stops says nothing."""
    page: Page = await _open(browser, address, 1280)
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.fail('no-speech')")
    await expect(mic).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(".toast.error")).to_contain_text("Nothing was heard. Check that the browser may use")
    await page.locator(".toast").first.click()
    await mic.click()
    await _recorder(page).get_by_role("button", name="Put the words in the box").click()
    await expect(mic).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(".toast")).to_have_count(0)
    await mic.click()
    await page.evaluate("() => window.__voice.fail('not-allowed')")
    await expect(mic).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(".toast.error")).to_contain_text("The browser has not allowed the microphone")
    await expect(page.locator(".toast.error")).to_contain_text("macOS Dictation types into the box")
    await page.context.close()


async def test_on_a_phone_a_refused_microphone_names_no_mac_setting(browser: Browser, address: str) -> None:
    """Item 593's review: an iPhone has no System Settings nor Fn key; its words name the browser's and the device's
    settings and the keyboard's own microphone."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await context.add_init_script(_STAND_INS)
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.fail('not-allowed')")
    toast: Locator = page.locator(".toast.error")
    await expect(toast).to_contain_text("in the device's settings")
    await expect(toast).to_contain_text("The keyboard's own microphone types into the box")
    await expect(toast).not_to_contain_text("System Settings")
    await expect(toast).not_to_contain_text("Fn")
    await context.close()


async def test_on_an_iphone_a_later_dictation_that_hears_nothing_says_so_and_reload_brings_the_microphone_back(
    browser: Browser, address: str
) -> None:
    """Item 616, from the owner's iPhone: the first dictation after the page loads hears, and a later one opens the
    microphone and hears nothing (WebKit bugs 317741 and 321436); Safari answers its stop with "aborted", which said
    nothing: the recorder closed on the box as it was. It says so now, and its Reload gives a page that hears again, the
    box's words kept as its draft."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await context.add_init_script(_STAND_INS)
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['first words', true]])")
    await _recorder(page).locator(".mic-done").click()
    await expect(page.locator(GOAL)).to_have_value("first words")
    # The next one opens the microphone and hears nothing; the owner's tick is answered with "aborted".
    await page.evaluate("() => { window.__voice.endAfter = 60000; }")
    await mic.click()
    await page.evaluate("() => window.__voice.recognitions.at(-1).dispatchEvent(new Event('audiostart'))")
    await _recorder(page).locator(".mic-done").click()
    await page.evaluate("() => window.__voice.fail('aborted')")
    toast: Locator = page.locator(".toast.error")
    await expect(toast).to_contain_text("Safari heard nothing this time.")
    await expect(toast).to_contain_text("use the keyboard's microphone")
    await expect(_recorder(page)).to_be_hidden()
    await expect(page.locator(GOAL)).to_have_value("first words")
    reload: Locator = toast.get_by_role("button", name="Reload")
    await expect(reload).to_be_visible()
    starts: int = await _voice(page, "voice.starts")
    assert starts == 2
    await reload.click()
    await expect(page.locator(GOAL)).to_have_value("first words")
    # A page anew: its first dictation is the first again.
    assert await _voice(page, "voice.starts") == 0
    await context.close()


async def test_in_dot_app_a_refused_microphone_names_macos_and_dot_not_a_browser(
    browser: Browser, address: str
) -> None:
    """Item 593, from the owner's screenshot of Dot.app: its toast said "the browser" there. In the desktop shell it
    says that macOS has not given Dot the microphone, where to allow it, and that macOS Dictation types meanwhile. Since
    item 636 these are the words that stand when the Mac cannot be asked to show System Settings (a 500, and a daemon
    that is not there): one toast for a press, and the page asked for the microphone's pane."""
    for allow in (500, None):
        page, asked = await _open_app(browser, address, allow)
        mic: Locator = _microphone(page)
        await mic.click()
        await page.evaluate("() => window.__voice.fail('not-allowed')")
        toast: Locator = page.locator(".toast.error")
        await expect(toast).to_contain_text("macOS has not given Dot the microphone or its speech recognition")
        await expect(toast).to_contain_text("Privacy & Security › Microphone and Speech Recognition")
        await expect(toast).to_contain_text("macOS Dictation types into the box")
        await expect(toast).not_to_contain_text("browser")
        await expect(page.locator(".toast")).to_have_count(1)
        assert asked == [{"pane": "microphone"}]
        await page.context.close()


async def test_in_dot_app_a_refused_microphone_has_system_settings_shown_and_says_so_in_one_calm_toast(
    browser: Browser, address: str
) -> None:
    """Item 636, from the owner's Dot.app, where the toast of item 593 came twice for two presses and the permission
    looked given: the page asks the Mac to show System Settings at the pane, and tells what it did in one toast for a
    press, calm because it is help, not an error."""
    ready: dict[str, object] = {"opened": True, "app": "ready"}
    unknown: dict[str, object] = {"opened": True, "app": "unknown"}
    for width, answer in ((1280, ready), (390, ready), (1280, unknown)):
        page, asked = await _open_app(browser, address, answer, width)
        await _microphone(page).click()
        await page.evaluate("() => window.__voice.fail('not-allowed', 'Microphone permission check has failed')")
        calm: Locator = page.locator(".toast.ok")
        await expect(calm).to_contain_text(
            "System Settings is open at Microphone: switch Dot on there, and under Speech Recognition too."
        )
        await expect(calm).to_contain_text(
            "macOS Dictation types into the box meanwhile: the Dictation key, or Fn twice."
        )
        # Not the words of the refusal as well, and not twice.
        await expect(page.locator(".toast.error")).to_have_count(0)
        await expect(page.locator(".toast")).to_have_count(1)
        assert asked == [{"pane": "microphone"}]
        await _toast_fits(page, width, f"{width} wide, opened")
        # Another press, another refusal: one more ask and one more toast, whose words are the same.
        await calm.click()
        await expect(page.locator(".toast")).to_have_count(0)
        await _microphone(page).click()
        await page.evaluate("() => window.__voice.fail('not-allowed')")
        await expect(page.locator(".toast.ok")).to_contain_text("System Settings is open at Microphone")
        await expect(page.locator(".toast")).to_have_count(1)
        assert asked == [{"pane": "microphone"}, {"pane": "microphone"}]
        await page.context.close()


async def test_in_dot_app_a_refusal_of_speech_recognition_has_its_pane_shown(browser: Browser, address: str) -> None:
    """Item 636: the pane is Speech Recognition when the engine's message names it, and the words name Microphone as the
    other place to switch Dot on."""
    for code in ("service-not-allowed", "not-allowed"):
        page, asked = await _open_app(browser, address, {"opened": True, "app": "ready"})
        await _microphone(page).click()
        await page.evaluate(
            "(code) => window.__voice.fail(code, 'Speech recognition service permission check has failed')", code
        )
        await expect(page.locator(".toast.ok")).to_contain_text(
            "System Settings is open at Speech Recognition: switch Dot on there, and under Microphone too."
        )
        await expect(page.locator(".toast")).to_have_count(1)
        assert asked == [{"pane": "speech"}]
        await page.context.close()


async def test_in_dot_app_built_before_it_asked_macos_the_toast_says_so_and_nothing_is_to_be_switched_on(
    browser: Browser, address: str
) -> None:
    """Item 636: that Dot.app is given no microphone whatever System Settings shows, so the Mac shows nothing, and the
    toast says it is an older Dot.app and that a newer one asks. It is an error, since nothing here helps."""
    for width in (1280, 390):
        page, asked = await _open_app(browser, address, {"opened": False, "app": "old"}, width)
        await _microphone(page).click()
        await page.evaluate("() => window.__voice.fail('not-allowed')")
        toast: Locator = page.locator(".toast.error")
        await expect(toast).to_contain_text("This Dot.app was built before Dot asked macOS for the microphone")
        await expect(toast).to_contain_text("so macOS gives it none, whatever System Settings shows.")
        await expect(toast).to_contain_text("The next Dot.app asks for it, and Dot offers it as soon as it is out.")
        await expect(toast).to_contain_text(
            "macOS Dictation types into the box meanwhile: the Dictation key, or Fn twice."
        )
        await expect(toast).not_to_contain_text("switch Dot on")
        await expect(page.locator(".toast")).to_have_count(1)
        await expect(page.locator(".toast.ok")).to_have_count(0)
        assert asked == [{"pane": "microphone"}]
        await _toast_fits(page, width, f"{width} wide, old")
        await page.context.close()


async def test_a_browser_that_is_not_dot_app_keeps_its_words_and_never_asks_for_system_settings(
    browser: Browser, address: str
) -> None:
    """Item 636: only Dot.app has a System Settings the daemon opens for it. A browser on the Mac names the browser and
    System Settings, a phone its own settings; neither asks the daemon to show anything."""
    mac: Page = await _open(browser, address, 1280)
    mac_asked: list[object] = await _watch_allow(mac)
    await _microphone(mac).click()
    await mac.evaluate("() => window.__voice.fail('not-allowed', 'Microphone permission check has failed')")
    await expect(mac.locator(".toast.error")).to_contain_text("The browser has not allowed the microphone")
    await expect(mac.locator(".toast")).to_have_count(1)
    await mac.context.close()
    phone_context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
    )
    await phone_context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await phone_context.add_init_script(_STAND_INS)
    phone: Page = await phone_context.new_page()
    phone_asked: list[object] = await _watch_allow(phone)
    await phone.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await _microphone(phone).click()
    await phone.evaluate("() => window.__voice.fail('not-allowed', 'Microphone permission check has failed')")
    await expect(phone.locator(".toast.error")).to_contain_text("in the device's settings")
    await expect(phone.locator(".toast")).to_have_count(1)
    # Neither asked, though each was refused the microphone and the daemon had a route for it.
    assert mac_asked == [] and phone_asked == []
    await phone_context.close()


async def test_the_language_is_the_pages_or_the_one_chosen_in_settings(browser: Browser, address: str) -> None:
    page: Page = await _open(browser, address, 1280)
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    language: Locator = voice.get_by_label("Language of the voice")
    await expect(language).to_have_value("page")
    await expect(language.locator("option")).to_have_text(
        ["The page's language", "English", "Türkçe", "Suomi", "Svenska", "Deutsch", "Français", "Español"]
    )
    await language.select_option("tr-TR")
    assert await page.evaluate("() => localStorage.getItem('dot.voiceLanguage')") == "tr-TR"
    await page.goto(f"{address}/#/runs")
    await _microphone(page).click()
    assert await _voice(page, "voice.langs") == ["tr-TR"]
    # Back to the page's own language, which the page says: the first dictation was the page's, in English.
    await page.goto(f"{address}/#/settings/general")
    await voice.get_by_label("Language of the voice").select_option("page")
    assert await page.evaluate("() => localStorage.getItem('dot.voiceLanguage')") is None
    await page.context.close()


async def test_the_voice_card_says_what_the_browser_does_and_has(browser: Browser, address: str) -> None:
    for width in (1280, 390):
        page: Page = await _open(browser, address, width)
        await page.goto(f"{address}/#/settings/general")
        voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
        await expect(voice.get_by_role("switch", name="Read answers aloud")).to_have_attribute("aria-checked", "false")
        await expect(voice).to_contain_text("Safari and Chrome may send your voice to Apple's or Google's servers")
        await expect(voice).to_contain_text("Dot sends nothing itself")
        await _fits(page, f"{width} Settings › General")
        await page.context.close()


async def test_a_browser_that_cannot_listen_or_speak_shows_no_microphone_and_no_speaker(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    replies.push(finish("Nothing to hear."))
    page: Page = await _open(browser, address, 1280, _NO_VOICE)
    await expect(_microphone(page)).to_have_count(0)
    await expect(page.locator(".composer-bar")).not_to_have_class("composer-bar has-mic")
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    await expect(voice).to_contain_text("This browser has no speech recognition, so the composer shows no microphone.")
    await expect(voice).to_contain_text("This browser cannot speak, so no answer is read aloud.")
    await expect(voice.get_by_role("switch", name="Read answers aloud")).to_be_disabled()
    await page.goto(f"{address}/#/runs")
    await page.locator(GOAL).fill("say something")
    await page.locator(GOAL).press("Enter")
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("Nothing to hear.")
    await expect(page.get_by_role("button", name="Read the answer aloud")).to_have_count(0)
    await page.context.close()


async def test_the_speaker_of_an_answer_reads_it_again_or_stops_it_and_an_answer_is_not_read_unasked(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    replies.push(finish("**Done.** The report is in [the folder](https://example.com/x).\n\n```sh\nls\n```"))
    page: Page = await _open(browser, address, 1280)
    await page.locator(GOAL).fill("make the report")
    await page.locator(GOAL).press("Enter")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text("The report is in the folder")
    # Read answers aloud is off: nothing was said as the answer came in.
    assert await _voice(page, "voice.spoken") == []
    speaker: Locator = answer.get_by_role("button", name="Read the answer aloud")
    await expect(speaker).to_have_attribute("aria-pressed", "false")
    await speaker.click()
    spoken = await _voice(page, "voice.spoken")
    assert spoken == [{"text": "Done. The report is in the folder.", "lang": "en-US"}]
    stop: Locator = answer.get_by_role("button", name="Stop reading")
    await expect(stop).to_have_attribute("aria-pressed", "true")
    await stop.click()
    assert await _voice(page, "voice.cancels") == 1
    await expect(answer.get_by_role("button", name="Read the answer aloud")).to_have_attribute("aria-pressed", "false")
    # Read again, to its end: the voice finishes and the speaker is ready again.
    await answer.get_by_role("button", name="Read the answer aloud").click()
    await expect(answer.get_by_role("button", name="Stop reading")).to_be_visible()
    await page.evaluate("() => window.__voice.finishSpeech()")
    await expect(answer.get_by_role("button", name="Read the answer aloud")).to_be_visible()
    assert len(await _voice(page, "voice.spoken")) == 2
    await page.context.close()


async def test_an_answer_being_read_stops_when_the_owner_leaves_the_chat(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    """Item 612 (the final review): a long answer kept talking over whatever the owner opened next, with no button on
    the screen that stopped it, and the speaker found again said it was not reading and started the reading anew."""
    replies.push(finish("Done. The report is in the folder."))
    page: Page = await _open(browser, address, 1280)
    await page.locator(GOAL).fill("make the report")
    await page.locator(GOAL).press("Enter")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text("The report is in the folder")
    await answer.get_by_role("button", name="Read the answer aloud").click()
    await expect(answer.get_by_role("button", name="Stop reading")).to_be_visible()
    assert await _voice(page, "[voice.queue.length, voice.cancels]") == [1, 0]
    chat: str = page.url
    # Leaving the chat ends the reading: the voice has nothing left to say, and said so to the browser.
    await page.goto(f"{address}/#/settings/general")
    await expect(page.get_by_role("switch", name="Read answers aloud")).to_be_visible()
    assert await _voice(page, "[voice.queue.length, voice.cancels]") == [0, 1]
    # The speaker of the chat, found again, is quiet, and one press reads the answer again.
    await page.goto(chat)
    speaker: Locator = page.get_by_role("article", name="Answer").get_by_role("button", name="Read the answer aloud")
    await expect(speaker).to_have_attribute("aria-pressed", "false")
    assert await _voice(page, "[voice.queue.length, voice.cancels]") == [0, 1]
    await speaker.click()
    assert await _voice(page, "[voice.queue.length, voice.spoken.length]") == [1, 2]
    await expect(page.get_by_role("article", name="Answer").get_by_role("button", name="Stop reading")).to_be_visible()
    # A message sent in the chat meanwhile takes the chat to the page of its next run, which ends the reading too.
    replies.push(finish("A second answer."))
    await page.locator(MESSAGE).fill("and once more")
    await page.locator(MESSAGE).press("Enter")
    await expect(page.get_by_role("article", name="Answer").last).to_contain_text("A second answer.")
    assert await _voice(page, "[voice.queue.length, voice.cancels]") == [0, 2]
    await page.context.close()


_VOICES: str = """() => {
  window.__voice.voices = [
    { name: "Google English", lang: "en-US", localService: false },
    { name: "Samantha", lang: "en-US", localService: true },
    { name: "Google Türkçe", lang: "tr-TR", localService: false },
    { name: "Yelda", lang: "tr-TR", localService: true },
    { name: "Google Suomi", lang: "fi-FI", localService: false },
  ];
}"""


async def test_an_answer_is_read_with_a_voice_kept_on_this_mac_and_not_a_network_one_when_there_is_one(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    """A network voice (Chrome's "Google ..." voices) sends the text it reads to a server; the Mac's own does not."""
    replies.push(finish("Done. The report is in the folder."))
    page: Page = await _open(browser, address, 1280)
    await page.evaluate(_VOICES)
    await page.locator(GOAL).fill("make the report")
    await page.locator(GOAL).press("Enter")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text("The report is in the folder")
    await answer.get_by_role("button", name="Read the answer aloud").click()
    assert await _voice(page, "voice.chosen") == ["Samantha"]
    await page.evaluate("() => window.__voice.finishSpeech()")
    # The language the owner chose has its own local voice; one that has only a network voice is left to the browser.
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    await expect(voice.get_by_text("sends the text it reads to a server")).to_be_visible()
    await voice.get_by_label("Language of the voice").select_option("tr-TR")
    await voice.get_by_role("switch", name="Read answers aloud").click()
    assert (await _voice(page, "voice.spoken"))[-1]["lang"] == "tr-TR"
    assert (await _voice(page, "voice.chosen"))[-1] == "Yelda"
    await page.evaluate("() => window.__voice.finishSpeech()")
    await voice.get_by_role("switch", name="Read answers aloud").click()
    await voice.get_by_label("Language of the voice").select_option("fi-FI")
    await voice.get_by_role("switch", name="Read answers aloud").click()
    assert (await _voice(page, "voice.spoken"))[-1]["lang"] == "fi-FI"
    assert (await _voice(page, "voice.chosen"))[-1] is None
    await page.context.close()


async def test_an_answer_that_comes_in_is_read_aloud_in_the_chosen_language_when_the_switch_is_on(
    browser: Browser, address: str, replies: Replies, runtime: Runtime
) -> None:
    for width in (1280, 390):
        replies.clear()
        replies.push(finish("It is sunny in Oulu."))
        page: Page = await _open(browser, address, width)
        await page.goto(f"{address}/#/settings/general")
        voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
        await voice.get_by_label("Language of the voice").select_option("tr-TR")
        switch: Locator = voice.get_by_role("switch", name="Read answers aloud")
        await switch.click()
        await expect(switch).to_have_attribute("aria-checked", "true")
        assert await page.evaluate("() => localStorage.getItem('dot.readAloud')") == "on"
        # Turning it on says so with the voice, which is also the touch a browser that speaks only after one asks for.
        assert await _voice(page, "voice.spoken") == [{"text": "Dot reads answers aloud now.", "lang": "tr-TR"}]
        await page.evaluate("() => window.__voice.finishSpeech()")
        await page.goto(f"{address}/#/runs")
        # The run is held at its first model call, so that its answer comes in under the reader's eyes.
        replies.gate = asyncio.Event()
        await page.locator(GOAL).fill("what is the weather in Oulu?")
        await page.locator(GOAL).press("Enter")
        await expect(page.locator(MESSAGE)).to_be_visible()
        await expect(page.locator(".bubble.user")).to_contain_text("what is the weather in Oulu?")
        replies.gate.set()
        replies.gate = None
        answer: Locator = page.get_by_role("article", name="Answer")
        await expect(answer).to_contain_text("It is sunny in Oulu.")
        await expect(answer.get_by_role("button", name="Stop reading")).to_have_attribute("aria-pressed", "true")
        spoken = await _voice(page, "voice.spoken")
        assert spoken[-1] == {"text": "It is sunny in Oulu.", "lang": "tr-TR"}
        assert [item["text"] for item in spoken] == ["Dot reads answers aloud now.", "It is sunny in Oulu."]
        await page.evaluate("() => window.__voice.finishSpeech()")
        await expect(answer.get_by_role("button", name="Read the answer aloud")).to_be_visible()
        # A chat opened again does not read what it already said.
        await page.reload()
        await expect(page.get_by_role("article", name="Answer")).to_contain_text("It is sunny in Oulu.")
        await asyncio.sleep(0.3)
        assert await _voice(page, "voice.spoken") == []
        await _fits(page, f"{width} with the speaker")
        await page.context.close()


async def test_the_microphone_stands_by_the_send_on_a_desk_and_under_the_box_on_a_phone_and_a_run_page_has_one(
    browser: Browser, address: str, runtime: Runtime, replies: Replies
) -> None:
    for width in (1280, 390):
        page: Page = await _open(browser, address, width)
        for place in ("the new chat", "a run's page"):
            if place == "a run's page":
                replies.push(finish("A run to open."))
                await page.locator(GOAL).fill("open a run")
                await page.locator(GOAL).press("Enter")
                await expect(page.locator(MESSAGE)).to_be_visible()
                await expect(page.get_by_role("article", name="Answer")).to_be_visible()
            composer: Locator = page.locator(".composer").last
            bar: Locator = composer.locator(".composer-bar")
            await expect(bar).to_have_class("composer-bar has-mic")
            send: Locator = composer.locator(".btn.send")
            words: Locator = composer.locator("textarea")
            # The microphone is a finger's size on a phone.
            expected: float = 36 if width < 720 else 32
            # As one line (item 833), on a phone too: the microphone stands right of the words, alone until words are
            # typed, and the send then comes right of it. All three are read in one frame.
            await expect(composer).to_have_attribute("data-quiet", "")
            await expect(send).to_be_hidden()
            await words.fill("a word")
            await expect(send).to_be_visible()
            box, sent, field = await _boxes(composer)
            assert abs(box["width"] - expected) < 1.5, f"{place} {width}: the microphone is {box['width']} px wide"
            assert box["x"] >= field["x"] + field["width"] - 1, f"{place} {width}: it is not right of the words"
            assert box["x"] + box["width"] <= sent["x"] + 1, f"{place} {width}: it is not left of the send"
            assert abs((box["y"] + box["height"] / 2) - (field["y"] + field["height"] / 2)) < 8, (
                f"{place} {width}: it is not on the line of the words"
            )
            await words.fill("")
            # With the run's choices shown, the box takes a line of its own. On a desk the microphone stands left of
            # the send, on the line of the choices; on a phone the box keeps its line with the "+" and the send, and
            # the microphone is under it, at the right.
            await composer.locator(".plus-button").click()
            await page.get_by_role("menuitem", name="Model and options").click()
            await expect(composer).not_to_have_attribute("data-quiet", "")
            box, sent, field = await _boxes(composer)
            assert abs(box["width"] - expected) < 1.5, f"{place} {width}: the microphone is {box['width']} px wide"
            if width < 720:
                assert box["y"] >= field["y"] + field["height"] - 1, (
                    f"{place} {width}: the microphone is not under the box"
                )
                assert box["x"] + box["width"] <= sent["x"] + sent["width"] + 1, (
                    f"{place} {width}: it runs past the send"
                )
                assert box["x"] > field["x"] + field["width"] / 2, f"{place} {width}: it is not at the right"
            else:
                assert box["x"] + box["width"] <= sent["x"] + 1, (
                    f"{place} {width}: the microphone is not left of the send"
                )
                assert abs((box["y"] + box["height"]) - (sent["y"] + sent["height"])) < 2
            await _fits(page, f"{width} {place}")
            # The choices away again, for the next place and the tests after this one.
            await composer.locator(".plus-button").click()
            await page.get_by_role("menuitem", name="Hide the model and options").click()
            await expect(composer).to_have_attribute("data-quiet", "")
            await _fits(page, f"{width} {place} as one line")
        await page.context.close()


async def _boxes(composer: Locator) -> list[dict[str, float]]:
    """The microphone's, the send's and the box's rectangles, read in one frame."""
    boxes: list[dict[str, float]] = await composer.evaluate(
        "composer => ['.mic-button', '.btn.send', '.composer-field'].map((part) => {"
        " const rect = composer.querySelector(part).getBoundingClientRect();"
        " return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }; })"
    )
    return boxes
