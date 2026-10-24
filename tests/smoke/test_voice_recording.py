"""The composer's microphone when the page records the voice itself (item 626), in a real browser, on a phone-sized
window and in WebKit, Safari's engine, against a real server whose Mac writes recordings down with a program of the
test's own (``voice_stand_ins``).

On an iPhone Safari's speech recognition goes deaf after the first dictation of a page, so the page there records the
microphone (getUserMedia, Web Audio and an audio worklet), sends the recording to the Mac and puts the words it answers
in the box, every time. A browser with no speech recognition (Firefox) does it too, and everywhere else the browser's
own recognition stays; where the Mac cannot write down, an iPhone keeps the recognition and its toast says what the Mac
lacks.

No microphone is opened, here or by the browser: Chrome's own fake microphone makes macOS ask for the microphone for the
program that runs the tests, so `getUserMedia` itself is stood in for by a function that gives a stream Web Audio makes,
a tone that the test turns on and off. Everything after it is the page's own: its audio context, its worklet, its
downsampling, its WAV file, its request, and the daemon's."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
import pytest_asyncio
from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    async_playwright,
    expect,
)
from voice_stand_ins import StandIn, make_stand_in

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.voice.toolchain import NO_COMPILER
from dotagent.voice.wav import RECORDING_RATE, TAIL_SECONDS

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "voice-recording-test-token"
GOAL: str = "textarea[aria-label='Goal']"
IPHONE: str = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
_WORDS: str = "hello from the mac"
_TAIL_SAMPLES: int = round(TAIL_SECONDS * RECORDING_RATE)
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
_OVER_PX: int = 1
_WAIT_MS: int = 15_000
# The microphone the page is given in place of the browser's (see the module's docstring): a tone of 220 hertz made
# by Web Audio that `speak(seconds)` turns on, and bookkeeping of what the page did with it: what it asked for, the
# tracks it was given and which are live, the audio contexts it made and the blocks its worklet sent.
_MICROPHONE: str = """
(() => {
  const state = { asked: [], tracks: [], contexts: [], blocks: 0, refuse: null };
  window.__mic = state;
  const Context = window.AudioContext;
  window.AudioContext = class extends Context {
    constructor(...options) {
      super(...options);
      state.contexts.push(this);
    }
  };
  const Node = window.AudioWorkletNode;
  window.AudioWorkletNode = class extends Node {
    constructor(...options) {
      super(...options);
      this.port.addEventListener("message", () => {
        state.blocks += 1;
      });
    }
  };
  let sourceContext = null;
  // WebKit lets go of the wrapper of `navigator.mediaDevices`, and what was put on it with it, once nothing holds it.
  state.devices = navigator.mediaDevices;
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    state.asked.push(constraints);
    if (state.refuse !== null) {
      throw Object.assign(new Error("the browser would not give the microphone"), { name: state.refuse });
    }
    sourceContext ??= new Context();
    await sourceContext.resume();
    const destination = sourceContext.createMediaStreamDestination();
    const gain = sourceContext.createGain();
    gain.gain.value = 0;
    const tone = sourceContext.createOscillator();
    tone.frequency.value = 220;
    tone.connect(gain);
    gain.connect(destination);
    tone.start();
    state.gain = gain;
    for (const track of destination.stream.getTracks()) {
      state.tracks.push(track);
    }
    return destination.stream;
  };
  // The tone sounds for `seconds` from now.
  state.speak = (seconds, level = 0.3) => {
    const now = state.gain.context.currentTime;
    state.gain.gain.setValueAtTime(level, now);
    state.gain.gain.setValueAtTime(0, now + seconds);
  };
  state.live = () => state.tracks.filter((track) => track.readyState === "live").length;
  state.states = () => state.contexts.map((context) => context.state);
})();
"""
# The browser's speech recognition, which an iPhone has: it counts how often it was started and hears what the test
# says.
_RECOGNITION: str = """
(() => {
  const state = { recognitions: [], starts: 0, stops: 0, aborts: 0 };
  window.__voice = state;
  class FakeRecognition extends EventTarget {
    constructor() {
      super();
      state.recognitions.push(this);
    }
    start() {
      state.starts += 1;
      this.dispatchEvent(new Event("start"));
    }
    stop() {
      state.stops += 1;
      setTimeout(() => this.dispatchEvent(new Event("end")), 20);
    }
    abort() {
      state.aborts += 1;
      setTimeout(() => this.dispatchEvent(new Event("end")), 20);
    }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  state.hear = (transcript) => {
    const event = new Event("result");
    event.resultIndex = 0;
    event.results = [Object.assign([{ transcript, confidence: 0.9 }], { isFinal: true })];
    state.recognitions.at(-1).dispatchEvent(event);
  };
  state.fail = (code) => {
    const event = new Event("error");
    event.error = code;
    state.recognitions.at(-1).dispatchEvent(event);
  };
})();
"""
# A browser with no speech recognition: Firefox.
_NO_RECOGNITION: str = """
delete window.SpeechRecognition;
delete window.webkitSpeechRecognition;
"""
Engine = Literal["chromium", "webkit"]


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("home")


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory: pytest.TempPathFactory) -> StandIn:
    return make_stand_in(tmp_path_factory.mktemp("stand-in"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, home: Path) -> AsyncIterator[Runtime]:
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nlessons = "off"\n'
        "playbooks = false\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@asynccontextmanager
async def _serving(runtime: Runtime) -> AsyncIterator[str]:
    """A daemon over `runtime`, with a memory of its own of what the Mac has to build with (item 626): a compiler it
    found stays found for its life."""
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
async def address(runtime: Runtime) -> AsyncIterator[str]:
    async with _serving(runtime) as served:
        yield served


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address_without_tools(runtime: Runtime) -> AsyncIterator[str]:
    """A second daemon, for the tests of a Mac that cannot write down: the first remembers a compiler it found for its
    life, and this one never finds one, as no test that uses it gives the Mac a compiler."""
    async with _serving(runtime) as served:
        yield served


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def playwright_of_the_module() -> AsyncIterator[Playwright]:
    async with async_playwright() as started:
        yield started


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chromium(playwright_of_the_module: Playwright, runtime: Runtime) -> AsyncIterator[Browser]:
    """Chrome as the other browser tests start it: no flag of a fake microphone (see the module's docstring)."""
    launched: Browser = await playwright_of_the_module.chromium.launch(
        channel=runtime.config.browser.channel, headless=True
    )
    try:
        yield launched
    finally:
        await launched.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def webkit(playwright_of_the_module: Playwright) -> AsyncIterator[Browser]:
    """WebKit, the engine of Safari on the owner's iPhone."""
    launched: Browser = await playwright_of_the_module.webkit.launch(headless=True)
    try:
        yield launched
    finally:
        await launched.close()


@pytest.fixture
def mac_can(stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    """This Mac as one that can write down: the compiler the daemon builds with is the stand-in."""
    monkeypatch.setenv("DOT_SWIFTC", str(stand_in.compiler))
    stand_in.says(text=_WORDS)
    return stand_in


async def _open(
    browser: Browser,
    address: str,
    scripts: tuple[str, ...] = (_MICROPHONE, _RECOGNITION),
    phone: bool = True,
    width: int = 390,
    clock: bool = False,
    engine: Engine = "chromium",
    playwright: Playwright | None = None,
    answered: bool = True,
    routes: Callable[[Page], Awaitable[None]] | None = None,
) -> Page:
    """A page signed in at the home, on an iPhone (its user agent, a touch screen, a phone's window) or on a Mac's
    Chrome, with `scripts` run before the page's own: the microphone and the recognition it is given. With `clock`, a
    page whose time the test moves. The page does not wait for the Mac's answer to `GET /api/voice` to start (item 626),
    so this waits for it, and for the page to have taken it, unless `answered` is false (a test that holds the answer or
    refuses it); `routes` sets what the page's requests meet, before it loads."""
    options: dict[str, Any]
    if phone and engine == "webkit":
        assert playwright is not None
        options = dict(playwright.devices["iPhone 17 Pro Max"])
    elif phone:
        options = {
            "viewport": {"width": width, "height": 844},
            "has_touch": True,
            "is_mobile": True,
            "user_agent": IPHONE,
        }
    else:
        options = {"viewport": {"width": width, "height": 900}}
    context: BrowserContext = await browser.new_context(**options, reduced_motion="reduce")
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    for script in scripts:
        await context.add_init_script(script)
    page: Page = await context.new_page()
    if routes is not None:
        await routes(page)
    if clock:
        await page.clock.install()
    page.on("pageerror", lambda error: pytest.fail(f"the page failed: {error}"))
    home: str = ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN)
    if answered:
        async with page.expect_response(_is_the_macs_answer) as the_answer:
            await page.goto(home)
        await (await the_answer.value).finished()
        await _settle(page)
    else:
        await page.goto(home)
    await expect(page.locator(GOAL)).to_be_visible()
    if clock:
        await page.clock.pause_at(await page.evaluate("() => Date.now() + 500"))
    return page


def _is_the_macs_answer(response: Any) -> bool:
    """Whether `response` is the daemon's answer to `GET /api/voice`."""
    return bool(response.url.endswith("/api/voice"))


async def _settle(page: Page) -> None:
    """Let the tasks the page has queued run: what it does with an answer that has come in. A message of a channel,
    not a timer, so that a page whose clock the test holds is not waited on."""
    await page.evaluate(
        "() => new Promise((resolve) => { const channel = new MessageChannel();"
        " channel.port1.onmessage = () => resolve(); channel.port2.postMessage(0); })"
    )


def _microphone(page: Page) -> Locator:
    return page.locator(".composer .mic-button")


def _recorder(page: Page) -> Locator:
    return page.locator(".composer .mic-recorder")


def _tick(page: Page) -> Locator:
    return _recorder(page).get_by_role("button", name="Put the words in the box")


def _cross(page: Page) -> Locator:
    return _recorder(page).get_by_role("button", name="Cancel the dictation")


async def _mic(page: Page, expression: str) -> Any:
    """What the stand-in microphone holds or does, by an expression over `window.__mic`."""
    return await page.evaluate(f"() => {{ const mic = window.__mic; return {expression}; }}")


async def _listening(page: Page) -> None:
    """Press the microphone and wait until the page's worklet is sending blocks of its sound."""
    await _microphone(page).click()
    await expect(_recorder(page)).to_be_visible()
    await page.wait_for_function("() => window.__mic.blocks >= 3", timeout=_WAIT_MS)


async def _fits(page: Page, label: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    assert over <= _OVER_PX, f"{label}: {over} px past the screen's edge"


async def _released(page: Page) -> None:
    """The microphone is let go: no track of it is live, and every audio context the page made is closed."""
    await page.wait_for_function(
        "() => window.__mic.live() === 0 && window.__mic.states().every((state) => state === 'closed')",
        timeout=_WAIT_MS,
    )


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
async def test_on_an_iphone_the_microphone_records_and_the_mac_writes_it_down_every_time(
    chromium: Browser,
    webkit: Browser,
    playwright_of_the_module: Playwright,
    address: str,
    mac_can: StandIn,
    engine: Engine,
) -> None:
    """The owner's trouble: on their iPhone the first dictation of a page heard and the next did not. The page records
    the microphone itself instead: each press opens it, the tick lets it go, and the words the Mac wrote down go into
    the box, as often as the owner presses."""
    browser: Browser = webkit if engine == "webkit" else chromium
    page: Page = await _open(browser, address, engine=engine, playwright=playwright_of_the_module)
    box: Locator = page.locator(GOAL)
    mic: Locator = _microphone(page)
    await expect(mic).to_be_visible()
    for round_number in (1, 2, 3):
        await _listening(page)
        # The microphone was asked for as the page asks: with the browser's own echo cancelling and noise suppression.
        assert (await _mic(page, "mic.asked"))[-1] == {"audio": {"echoCancellation": True, "noiseSuppression": True}}
        assert await _mic(page, "mic.live()") == 1
        await page.evaluate("() => window.__mic.speak(1.0)")
        await asyncio.sleep(1.5)
        await _fits(page, f"{engine} while the microphone records")
        await _tick(page).click()
        expected: str = " ".join([_WORDS] * round_number)
        await expect(box).to_have_value(expected, timeout=_WAIT_MS)
        await expect(_recorder(page)).to_be_hidden()
        await expect(mic).to_be_visible()
        # The microphone is let go at every end: no track is live and the page's audio contexts are closed.
        await _released(page)
    # The browser's own recognition was never started: Safari's goes deaf, and the page does not ask it.
    assert await page.evaluate("() => window.__voice.starts") == 0
    runs: list[dict[str, Any]] = mac_can.runs_made()[-3:]
    assert len(runs) == 3
    for given in runs:
        # What reached the Mac's program: a file of Dot's own making, 16 kHz mono 16-bit, in the page's language, with
        # a second of a tone of 220 hertz in it (the times its wave rises through zero count its pitch: 220) and the
        # silence the daemon adds.
        assert given["language"] == "en-US" and given["argc"] == 3
        assert given["head"] == ["RIFF", "WAVEfmt ", "data"]
        assert given["format"] == [1, 1, RECORDING_RATE, RECORDING_RATE * 2, 2, 16]
        assert given["samples"] > RECORDING_RATE + _TAIL_SAMPLES
        assert given["peak"] > 0.2 * 32767
        assert 0.9 * 220 <= given["crossings"] <= 1.1 * 220, given["crossings"]
        assert given["mode"] == "0o600"
    await page.context.close()


async def test_the_cross_sends_nothing_and_lets_the_microphone_go(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address)
    box: Locator = page.locator(GOAL)
    await box.fill("Dear Ada,")
    runs: int = len(mac_can.runs_made())
    await _listening(page)
    await page.evaluate("() => window.__mic.speak(1.0)")
    await asyncio.sleep(0.5)
    await _cross(page).click()
    await expect(_recorder(page)).to_be_hidden()
    await _released(page)
    await asyncio.sleep(0.3)
    assert len(mac_can.runs_made()) == runs
    await expect(box).to_have_value("Dear Ada,")
    # Typing into the box while it records takes over: it is dropped as the cross drops it.
    await _listening(page)
    await box.press_sequentially(" and")
    await expect(_recorder(page)).to_be_hidden()
    await _released(page)
    assert len(mac_can.runs_made()) == runs
    await page.context.close()


async def test_while_the_mac_writes_it_down_the_recorder_says_so_quietly_and_the_cross_gives_up_waiting(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    mac_can.says(mode="slow", seconds=2)
    page: Page = await _open(chromium, address)
    box: Locator = page.locator(GOAL)
    await _listening(page)
    await page.evaluate("() => window.__mic.speak(0.5)")
    await asyncio.sleep(0.8)
    await _tick(page).click()
    writing: Locator = _recorder(page).locator(".mic-writing")
    # A live region: its words are put in as the line is shown, and taken out after.
    await expect(writing).to_have_attribute("role", "status")
    await expect(writing).to_have_text("Writing down what you said…")
    await expect(writing).to_be_visible()
    await expect(_recorder(page).locator(".mic-wave")).to_be_hidden()
    await expect(_tick(page)).to_be_disabled()
    await expect(_cross(page)).to_be_enabled()
    # The microphone went as the tick was pressed, not when the Mac answered.
    await _released(page)
    await _fits(page, "while the Mac writes")
    await _cross(page).click()
    await expect(_recorder(page)).to_be_hidden()
    await expect(box).to_have_value("")
    # The Mac finishes after that: what it wrote goes nowhere.
    await asyncio.sleep(2.5)
    await expect(box).to_have_value("")
    await expect(page.locator(".toast")).to_have_count(0)
    await page.context.close()


async def test_what_goes_wrong_is_said_in_plain_words_and_the_box_keeps_its_words(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address)
    box: Locator = page.locator(GOAL)
    await box.fill("Dear Ada,")
    toast: Locator = page.locator(".toast.error")

    async def record_and_tick() -> None:
        await _listening(page)
        await page.evaluate("() => window.__mic.speak(0.5)")
        await asyncio.sleep(0.7)
        await _tick(page).click()
        await expect(_recorder(page)).to_be_hidden(timeout=_WAIT_MS)

    # A language the Mac cannot write down.
    mac_can.says(mode="error", code="unsupported-language")
    await record_and_tick()
    await expect(toast).to_contain_text(
        "The Mac cannot write down this language. Choose another in Settings › General › Voice."
    )
    await toast.click()
    # A program that fails.
    mac_can.says(mode="crash")
    await record_and_tick()
    await expect(toast).to_contain_text("The Mac could not write the recording down")
    await expect(toast).not_to_contain_text("Traceback")
    await toast.click()
    # Nothing heard in it.
    mac_can.says(text="")
    await record_and_tick()
    await expect(toast).to_contain_text("Nothing was heard.")
    await toast.click()
    # A Mac that cannot be reached.
    mac_can.says(text=_WORDS)
    await page.route("**/api/voice/transcribe", lambda route: route.abort())
    await record_and_tick()
    await expect(toast).to_contain_text("The Mac could not be reached, so what you said was not written down.")
    await page.unroute("**/api/voice/transcribe")
    await toast.click()
    await expect(box).to_have_value("Dear Ada,")
    # And it works again after all that.
    await record_and_tick()
    await expect(box).to_have_value(f"Dear Ada, {_WORDS}")
    await page.context.close()


async def test_a_microphone_the_browser_refuses_says_where_to_allow_it(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address)
    await page.evaluate("() => { window.__mic.refuse = 'NotAllowedError'; }")
    await _microphone(page).click()
    toast: Locator = page.locator(".toast.error")
    await expect(toast).to_contain_text("The browser has not allowed the microphone on this page.")
    await expect(toast).to_contain_text("in the device's settings")
    await expect(toast).to_contain_text("The keyboard's own microphone types into the box meanwhile.")
    await expect(toast).not_to_contain_text("speech recognition")
    await expect(_recorder(page)).to_be_hidden()
    await expect(_microphone(page)).to_have_attribute("aria-pressed", "false")
    await _released(page)
    await page.context.close()


async def test_a_page_put_out_of_sight_lets_the_microphone_go_and_sends_nothing(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address)
    runs: int = len(mac_can.runs_made())
    await _listening(page)
    await page.evaluate("() => window.__mic.speak(1.0)")
    await page.evaluate(
        "() => { Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });"
        " document.dispatchEvent(new Event('visibilitychange')); }"
    )
    await expect(_recorder(page)).to_be_hidden()
    await _released(page)
    await asyncio.sleep(0.3)
    assert len(mac_can.runs_made()) == runs
    await page.context.close()


async def test_six_seconds_of_silence_say_nothing_was_heard_and_send_nothing(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address, clock=True)
    runs: int = len(mac_can.runs_made())
    await _listening(page)
    await page.clock.run_for(5_900)
    await expect(_recorder(page)).to_be_visible()
    await page.clock.run_for(200)
    await expect(_recorder(page)).to_be_hidden()
    await expect(page.locator(".toast.error")).to_contain_text("Nothing was heard.")
    await _released(page)
    assert len(mac_can.runs_made()) == runs
    await page.context.close()


async def test_a_pause_of_fifteen_seconds_after_a_sound_ends_the_recording_and_sends_it(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address, clock=True)
    await _listening(page)
    await page.evaluate("() => window.__mic.speak(0.6)")
    # The sound is heard once its blocks reach the page; the clock is the page's own and stands still meanwhile.
    await asyncio.sleep(1.2)
    await page.clock.run_for(14_000)
    await expect(_recorder(page)).to_be_visible()
    await page.clock.run_for(1_200)
    await expect(page.locator(GOAL)).to_have_value(_WORDS, timeout=_WAIT_MS)
    await _released(page)
    await expect(page.locator(".toast")).to_have_count(0)
    await page.context.close()


async def test_where_the_mac_cannot_an_iphone_keeps_safaris_recognition_and_the_toast_says_what_the_mac_lacks(
    chromium: Browser, address_without_tools: str
) -> None:
    """The tests run with a compiler that does not exist (``MISSING_SWIFTC``): the Mac cannot. The page then dictates
    as it did before item 626, and the toast of a later dictation Safari did not hear (item 616) says what the Mac
    lacks."""
    page: Page = await _open(chromium, address_without_tools)
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.hear('first words')")
    await _recorder(page).locator(".mic-done").click()
    await expect(page.locator(GOAL)).to_have_value("first words")
    assert await page.evaluate("() => window.__voice.starts") == 1
    assert await _mic(page, "mic.asked") == []
    # The next one opens the microphone and hears nothing: Safari's deafness, and the owner's tick answered "aborted".
    await mic.click()
    await page.evaluate("() => window.__voice.recognitions.at(-1).dispatchEvent(new Event('audiostart'))")
    await _recorder(page).locator(".mic-done").click()
    await page.evaluate("() => window.__voice.fail('aborted')")
    toast: Locator = page.locator(".toast.error")
    await expect(toast).to_contain_text("Safari heard nothing this time.")
    await expect(toast).to_contain_text("A Mac that writes down what you say hears every time.")
    await expect(toast).to_contain_text(NO_COMPILER)
    await expect(toast.get_by_role("button", name="Reload")).to_be_visible()
    assert await _mic(page, "mic.asked") == []
    await page.context.close()


async def test_a_computers_browser_keeps_its_own_recognition_even_when_the_mac_can_write_down(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address, phone=False, width=1280)
    await _microphone(page).click()
    await page.evaluate("() => window.__voice.hear('spoken to the browser')")
    await _tick(page).click()
    await expect(page.locator(GOAL)).to_have_value("spoken to the browser")
    assert await page.evaluate("() => window.__voice.starts") == 1
    assert await _mic(page, "mic.asked") == []
    await page.context.close()


async def test_a_browser_with_no_speech_recognition_shows_the_microphone_when_the_mac_can_and_records(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    page: Page = await _open(chromium, address, scripts=(_MICROPHONE, _NO_RECOGNITION), phone=False, width=1280)
    # The composer of the home was made as the page started, which does not wait for the Mac's answer: the one made by
    # the next visit to the home is made after it, and has the microphone this browser never had.
    await page.goto(f"{address}/#/settings/general")
    await page.goto(f"{address}/#/runs")
    await expect(_microphone(page)).to_be_visible()
    await _listening(page)
    await page.evaluate("() => window.__mic.speak(0.6)")
    await asyncio.sleep(1.0)
    await _tick(page).click()
    await expect(page.locator(GOAL)).to_have_value(_WORDS, timeout=_WAIT_MS)
    await _released(page)
    await page.context.close()


async def test_the_voice_card_of_an_iphone_whose_mac_cannot_write_down_says_what_the_mac_lacks(
    chromium: Browser, address_without_tools: str
) -> None:
    # An iPhone whose Mac cannot write down keeps Safari's recognition, and the card says what the Mac lacks.
    page: Page = await _open(chromium, address_without_tools)
    await page.goto(f"{address_without_tools}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    await expect(voice).to_contain_text("Safari and Chrome may send your voice to Apple's or Google's servers")
    await expect(voice).to_contain_text("Safari on an iPhone or an iPad at times hears only the first dictation")
    await expect(voice).to_contain_text("A Mac that writes down what you say hears every time. Apple's developer tools")
    await _fits(page, "Settings › General › Voice on a phone, the Mac without the tools")
    await page.context.close()


async def test_the_voice_card_of_an_iphone_whose_mac_can_write_down_says_where_the_voice_goes(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    # With the Mac able, the card says the voice goes to the owner's Mac and nowhere else.
    page: Page = await _open(chromium, address)
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    await expect(voice).to_contain_text("The microphone records your voice and sends it to your Mac")
    await expect(voice).to_contain_text("it goes to no one else, and the Mac keeps nothing")
    await expect(voice).to_contain_text("downloads its model from Apple")
    await expect(voice).not_to_contain_text("Safari and Chrome may send your voice")
    await _fits(page, "Settings › General › Voice on a phone")
    await page.context.close()


async def test_the_page_starts_without_waiting_for_the_macs_answer_and_a_press_after_it_records(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    """The review of item 626: `GET /api/voice` was read with the rest of what the page needs to start, and a slow Mac
    (the daemon asks its developer tools, up to three programs) held up every page, a Mac browser's too. The page starts
    as the answer is held; a composer made before it listens the browser's way; and, the answer in, the next press
    records."""
    release: asyncio.Event = asyncio.Event()
    held: list[str] = []

    async def hold_the_answer(page: Page) -> None:
        async def slow(route: Route) -> None:
            held.append(route.request.url)
            await release.wait()
            await route.continue_()

        await page.route("**/api/voice", slow)

    page: Page = await _open(chromium, address, answered=False, routes=hold_the_answer)
    box: Locator = page.locator(GOAL)
    # The page started, composer and all, and the Mac has not been heard from.
    await expect(_microphone(page)).to_be_visible()
    assert len(held) == 1 and not release.is_set()
    # The composer was made before the answer: the press asks the browser's recognition, as an iPhone did before.
    await _microphone(page).click()
    await page.evaluate("() => window.__voice.hear('browser words')")
    await _tick(page).click()
    await expect(box).to_have_value("browser words")
    assert await page.evaluate("() => window.__voice.starts") == 1
    assert await _mic(page, "mic.asked") == []
    # The answer comes: the same composer, not made again, takes the way it gives at its next press.
    async with page.expect_response(_is_the_macs_answer) as the_answer:
        release.set()
    await (await the_answer.value).finished()
    await _settle(page)
    await box.fill("")
    await _listening(page)
    assert len(await _mic(page, "mic.asked")) == 1
    assert await page.evaluate("() => window.__voice.starts") == 1, "the browser's recognition was asked again"
    await page.evaluate("() => window.__mic.speak(0.6)")
    await asyncio.sleep(1.0)
    await _tick(page).click()
    await expect(box).to_have_value(_WORDS, timeout=_WAIT_MS)
    await _released(page)
    await page.context.close()


async def test_the_voice_card_is_written_again_when_the_macs_answer_comes_in_after_it(
    chromium: Browser, address: str, mac_can: StandIn
) -> None:
    release: asyncio.Event = asyncio.Event()

    async def hold_the_answer(page: Page) -> None:
        async def slow(route: Route) -> None:
            await release.wait()
            await route.continue_()

        await page.route("**/api/voice", slow)

    page: Page = await _open(chromium, address, answered=False, routes=hold_the_answer)
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    # The Mac has not answered: the card says how the browser listens, as it did before.
    await expect(voice).to_contain_text("The browser does the listening")
    await expect(voice).not_to_contain_text("The microphone records your voice")
    async with page.expect_response(_is_the_macs_answer) as the_answer:
        release.set()
    await (await the_answer.value).finished()
    # The same card, written again: it says where the voice goes now.
    await expect(voice).to_contain_text("The microphone records your voice and sends it to your Mac")
    await expect(voice).not_to_contain_text("The browser does the listening")
    await expect(voice).to_contain_text("downloads its model from Apple")
    await _fits(page, "Settings › General › Voice once the Mac has answered")
    await page.context.close()


async def test_a_mac_that_could_not_be_asked_leaves_the_page_as_it_was_and_the_voice_card_says_so(
    chromium: Browser, address: str
) -> None:
    async def refuse_the_answer(page: Page) -> None:
        async def failing(route: Route) -> None:
            await route.fulfill(
                status=500,
                content_type="application/json",
                body='{"message": "Dot failed on an error it did not expect", "context": {}}',
            )

        await page.route("**/api/voice", failing)

    page: Page = await _open(chromium, address, answered=False, routes=refuse_the_answer)
    box: Locator = page.locator(GOAL)
    # The page starts: no toast, no failure screen, and the microphone listens the browser's way, as before.
    await expect(_microphone(page)).to_be_visible()
    await expect(page.locator(".toast")).to_have_count(0)
    await _microphone(page).click()
    await page.evaluate("() => window.__voice.hear('browser words')")
    await _tick(page).click()
    await expect(box).to_have_value("browser words")
    assert await _mic(page, "mic.asked") == []
    # The Voice card says that the Mac could not be asked, with the daemon's own words.
    await page.goto(f"{address}/#/settings/general")
    voice: Locator = page.locator("section.card", has=page.get_by_text("Voice", exact=True))
    await expect(voice).to_contain_text(
        "The Mac could not be asked whether it can write down what you say: Dot failed on an error it did not expect"
    )
    await page.context.close()
