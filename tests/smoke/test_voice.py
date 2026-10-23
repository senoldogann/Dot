"""Voice (item 574) in a real browser, wide and on a phone, against a real server and a scripted model, with the
browser's speech recognition and speech synthesis stood in for by objects the test drives: the page's own code runs
against them.

The microphone shows where the browser can listen. It fills the box as the owner speaks, the words the engine may still
change quieter, and stops on a second press, after a few seconds of silence, when the owner types and when a message is
sent; nothing is sent by voice alone. The language is the page's or the one chosen in Settings › General. Read answers
aloud is off until the switch is on; then an answer that comes in is read, in that language, and the speaker of each
answer reads it again or stops it."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

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
  // The engine hears `pairs` of [words, settled]: every result of its session so far, as a real one reports them.
  state.hear = (pairs) => {
    const results = pairs.map(([transcript, isFinal]) => Object.assign([{ transcript, confidence: 0.9 }], { isFinal }));
    const event = new Event("result");
    event.resultIndex = 0;
    event.results = results;
    state.recognitions.at(-1).dispatchEvent(event);
  };
  state.fail = (code) => {
    const event = new Event("error");
    event.error = code;
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
    browser: Browser, address: str, width: int, stand_ins: str = _STAND_INS, scheme: Literal["light", "dark"] = "light"
) -> Page:
    """A page signed in at the home, in a browser of its own, with `stand_ins` in the place of the voice."""
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await context.add_init_script(stand_ins)
    page: Page = await context.new_page()
    port: int = int(address.rpartition(":")[2])
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await expect(page.locator(GOAL)).to_be_visible()
    return page


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


async def test_the_microphone_fills_the_box_as_the_owner_speaks_and_nothing_is_sent_by_voice_alone(
    browser: Browser, address: str, runtime: Runtime
) -> None:
    for width in (1280, 390):
        runs: int = await _run_count(runtime)
        page: Page = await _open(browser, address, width)
        box: Locator = page.locator(GOAL)
        mic: Locator = _microphone(page)
        await expect(mic).to_be_visible()
        await expect(mic).to_have_attribute("aria-label", "Dictate your message")
        await expect(mic).to_have_attribute("aria-pressed", "false")
        await mic.click()
        await expect(mic).to_have_attribute("aria-pressed", "true")
        await expect(mic).to_have_attribute("aria-label", "Stop dictating")
        # The page's language, given its region (item 593).
        assert await _voice(page, "[voice.starts, voice.langs[0]]") == [1, "en-US"]
        # The engine is asked for what makes a dictation: the session goes on and the words come as they are heard.
        assert await _voice(page, "[voice.recognitions[0].continuous, voice.recognitions[0].interimResults]") == [
            True,
            True,
        ]
        await page.evaluate("() => window.__voice.hear([['hello wor', false]])")
        await expect(box).to_have_value("hello wor")
        assert await _voice(page, "voice.interimWords()") == ["hello wor"]
        await page.evaluate("() => window.__voice.hear([['hello world', true], ['how are', false]])")
        await expect(box).to_have_value("hello world how are")
        assert await _voice(page, "voice.interimWords()") == ["how are"]
        await page.evaluate("() => window.__voice.hear([['hello world', true], ['how are you', true]])")
        await expect(box).to_have_value("hello world how are you")
        assert await _voice(page, "voice.interimWords()") == []
        # The box is the composer's own: the send is ready, and no run started.
        await expect(page.locator(".composer")).not_to_have_attribute("data-empty", "")
        assert await _run_count(runtime) == runs
        # A second press stops it, and the engine ends a moment later.
        await mic.click()
        await expect(mic).to_have_attribute("aria-pressed", "false")
        assert await _voice(page, "[voice.stops, voice.aborts]") == [1, 0]
        await expect(box).to_have_value("hello world how are you")
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
    await expect(page.locator(GOAL)).to_have_value("what is the weather in Oulu")
    assert await _run_count(runtime) == runs
    await page.locator(GOAL).press("Enter")
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("It is sunny in Oulu.")
    assert await _run_count(runtime) == runs + 1
    newest = (await runtime.store.list_runs(1, None))[0]
    assert newest.goal == "what is the weather in Oulu"
    # The message went: the microphone was let go of, and the dock's box on the run's page has its own.
    assert await _voice(page, "voice.aborts") == 1
    await expect(_microphone(page)).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(MESSAGE)).to_have_value("")
    await page.context.close()


async def test_the_box_keeps_what_it_held_and_typing_takes_over_from_the_microphone(
    browser: Browser, address: str
) -> None:
    page: Page = await _open(browser, address, 1280)
    box: Locator = page.locator(GOAL)
    mic: Locator = _microphone(page)
    await box.fill("Dear Ada,")
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['thanks', false]])")
    await expect(box).to_have_value("Dear Ada, thanks")
    await page.evaluate("() => window.__voice.hear([['thanks for the plan', true]])")
    await expect(box).to_have_value("Dear Ada, thanks for the plan")
    # What the owner types takes over: the microphone goes off, what the box holds stays, and what is heard after it
    # changes nothing.
    await box.press_sequentially(" and the")
    await expect(mic).to_have_attribute("aria-pressed", "false")
    assert await _voice(page, "voice.aborts") == 1
    await page.evaluate("() => window.__voice.hear([['something else entirely', true]])")
    await asyncio.sleep(0.2)
    await expect(box).to_have_value("Dear Ada, thanks for the plan and the")
    await page.context.close()


async def test_the_microphone_stops_by_itself_after_a_silence(browser: Browser, address: str) -> None:
    page: Page = await _open(browser, address, 1280)
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.hear([['a few words', true]])")
    await expect(mic).to_have_attribute("aria-pressed", "true")
    await asyncio.sleep(_SILENCE_WAIT_S)
    await expect(mic).to_have_attribute("aria-pressed", "false")
    assert await _voice(page, "[voice.stops, voice.aborts]") == [1, 0]
    await expect(page.locator(GOAL)).to_have_value("a few words")
    # It heard words, so it says nothing of a silence (item 593).
    await expect(page.locator(".toast")).to_have_count(0)
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
    await mic.click()
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


async def test_in_dot_app_a_refused_microphone_names_macos_and_dot_not_a_browser(
    browser: Browser, address: str
) -> None:
    """Item 593, from the owner's screenshot of Dot.app: its toast said "the browser" there. In the desktop shell it
    says that macOS has not given Dot the microphone, where to allow it, and that macOS Dictation types meanwhile."""
    page: Page = await _open(browser, address, 1280, f"window.__TAURI__ = {{}};\n{_STAND_INS}")
    await expect(page.locator("html")).to_have_attribute("data-shell", "desktop")
    mic: Locator = _microphone(page)
    await mic.click()
    await page.evaluate("() => window.__voice.fail('not-allowed')")
    toast: Locator = page.locator(".toast.error")
    await expect(toast).to_contain_text("macOS has not given Dot the microphone or its speech recognition")
    await expect(toast).to_contain_text("Privacy & Security › Microphone and Speech Recognition")
    await expect(toast).to_contain_text("macOS Dictation types into the box")
    await expect(toast).not_to_contain_text("browser")
    await page.context.close()


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
            mic: Locator = composer.locator(".mic-button")
            send: Locator = composer.locator(".btn.send")
            box = await mic.bounding_box()
            sent = await send.bounding_box()
            field = await composer.locator(".composer-field").bounding_box()
            assert box is not None and sent is not None and field is not None
            # The microphone is a finger's size on a phone. On a desk it stands left of the send, on the line of the
            # choices; on a phone the box keeps its line with the "+" and the send, and the microphone is under it, at
            # the right.
            expected: float = 36 if width < 720 else 32
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
        await page.context.close()
