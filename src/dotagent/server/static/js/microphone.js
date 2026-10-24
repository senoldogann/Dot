// The microphone of a composer (item 574): a button where the browser has speech recognition. Pressed, it turns the
// composer's controls into a recorder (item 605): a cross that drops the dictation, a wave that moves while the engine
// hears a sound, the time since it began to listen, and a tick that ends it. The words go into the box when the owner
// presses the tick, or when a pause ends the dictation by itself; until then the box keeps what it held. It also ends when
// the owner types (the dictation is dropped), when a message is sent and when the page goes out of sight. Nothing is sent
// by voice alone: the owner reads what the box holds and sends it as usual. The browser does the recognizing, and Safari
// and Chrome may send the voice to Apple's or Google's servers to do it (docs/safety.md); Dot sends nothing itself.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { voiceLanguage } from "./readaloud.js";
import { toast } from "./ui.js";
import { QUIET_BAR, canListen, clockText, createDictation, nextWave } from "./voice.js";

const START = "Dictate your message";
const STOP = "Stop dictating";
// How many bars the wave holds, and how often a new one comes in: the oldest go out at the left, past the recorder's width.
const BARS = 96;
const WAVE_MS = 90;
// How long the tick waits for the engine to settle what it heard before the words it has go into the box all the same
// (an iPhone's later dictation that heard none then says it is Safari's: item 616).
const SETTLE_MS = 3000;

/**
 * The microphone of the box `textarea`, or null where the browser cannot listen. `setInterim(range)` draws the words that
 * may still change quieter (`[from, to]` in the box's text, or null for none). Returns `button`, to place in the
 * composer's bar, `recorder`, which the bar shows in place of its controls while the microphone listens, `abort()` for a
 * message sent, and `dispose()`.
 */
export function createMicrophone({ textarea, setInterim }) {
  if (!canListen()) {
    return null;
  }
  const button = el("button", { class: "btn ghost sm mic-button", type: "button", "aria-pressed": "false" }, icon("mic"));
  const cancel = el(
    "button",
    { class: "btn ghost sm mic-cancel", type: "button", title: "Cancel", "aria-label": "Cancel the dictation" },
    icon("x"),
  );
  const done = el(
    "button",
    { class: "btn primary mic-done", type: "button", title: "Done", "aria-label": "Put the words in the box" },
    icon("check"),
  );
  const bars = Array.from({ length: BARS }, () => el("span", { class: "mic-bar" }));
  const wave = el("div", { class: "mic-wave", "aria-hidden": "true" }, ...bars);
  const clock = el("span", { class: "mic-time" }, clockText(0));
  const recorder = el("div", { class: "mic-recorder", role: "group", "aria-label": "Dictation" }, cancel, wave, clock, done);
  // The words written into the box by the dictation itself: the input they raise is not the owner's typing.
  let writing = false;
  // What the dictation heard so far, on top of what the box held: it goes into the box at the end, unless dropped.
  let heard = null;
  let dropped = false;
  // Whether the engine hears a sound now, the wave's heights, when it began to listen, and the recorder's timers.
  let loud = false;
  let heights = bars.map(() => QUIET_BAR);
  let beganAt = null;
  let ticking = null;
  let settling = null;
  // Whether the tick had a keyboard's focus when it was pressed: turned off while the engine settles, it lets it go.
  let tickHadFocus = false;
  // Which dictation this is, counted at each start. A dictation's end clears the tick's wait before another starts;
  // the count is a second guard that the wait ends only the dictation it was pressed in.
  let dictations = 0;
  const still = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function paintWave() {
    heights.forEach((height, index) => {
      bars[index].style.transform = `scaleY(${height})`;
    });
  }

  function tick() {
    if (!still()) {
      heights = nextWave(heights, loud, Math.random);
      paintWave();
    }
    clock.textContent = clockText(beganAt === null ? 0 : performance.now() - beganAt);
  }

  /** The bar that holds the composer's controls: the recorder stands in their place while the microphone listens. */
  function bar() {
    return button.closest(".composer-bar") ?? recorder.closest(".composer-bar");
  }

  function paint(listening) {
    const label = listening ? STOP : START;
    button.setAttribute("aria-pressed", String(listening));
    button.setAttribute("aria-label", label);
    button.title = label;
    button.classList.toggle("listening", listening);
    bar()?.classList.toggle("recording", listening);
  }

  /** The words heard go into the box, after what it held, as the owner's typing would put them (item 605). */
  function putHeard() {
    if (heard === null || heard === textarea.value) {
      return;
    }
    writing = true;
    textarea.value = heard;
    // The composer hears it as it hears typing: it grows the box, keeps the draft and wakes the send.
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    writing = false;
    textarea.setSelectionRange(heard.length, heard.length);
  }

  function state(listening) {
    // The owner's tick asks the engine to stop: the recorder stays, its buttons off, until the engine has settled what
    // it heard and ended (the dictation then no longer listens), or `SETTLE_MS` passed.
    if (!listening && dictation.listening()) {
      return;
    }
    const focusedOn = document.activeElement;
    paint(listening);
    if (listening) {
      dictations += 1;
      // The engine may say it began before the dictation says it listens (an engine that starts at once): its time stays.
      heard = null;
      dropped = false;
      loud = false;
      heights = bars.map(() => QUIET_BAR);
      paintWave();
      clock.textContent = clockText(0);
      done.disabled = false;
      cancel.disabled = false;
      clearInterval(ticking);
      ticking = setInterval(tick, WAVE_MS);
      // The microphone the owner pressed by the keyboard goes out of sight: the focus goes to the tick.
      if (focusedOn === button) {
        done.focus();
      }
      return;
    }
    clearInterval(ticking);
    clearTimeout(settling);
    ticking = null;
    settling = null;
    if (!dropped) {
      putHeard();
    }
    heard = null;
    beganAt = null;
    // The tick or the cross that had a keyboard's focus goes out of sight with the recorder: the box takes it, the words
    // in it. A tap's focus (Chrome on a phone gives a tapped button one) stays put, so no keyboard opens on the screen.
    if (tickHadFocus || (recorder.contains(focusedOn) && focusedOn.matches(":focus-visible"))) {
      textarea.focus();
    }
    tickHadFocus = false;
  }

  const dictation = createDictation({
    language: voiceLanguage,
    text: () => textarea.value,
    show(value) {
      heard = value;
    },
    settled: () => setInterim(null),
    state,
    // Safari's deafness on an iPhone (item 616) is undone by a new page: the toast offers to reload it. The box's words
    // are its draft, which the reload keeps.
    problem: (words, offer) =>
      toast("error", words, offer?.reload ? { label: "Reload", run: () => window.location.reload() } : null),
    began() {
      beganAt = performance.now();
    },
    sound(isLoud) {
      loud = isLoud;
    },
  });

  /** End the dictation and drop what it heard: the box keeps what it held. */
  function drop() {
    dropped = true;
    dictation.abort();
  }

  button.addEventListener("click", dictation.toggle);
  cancel.addEventListener("click", drop);
  done.addEventListener("click", () => {
    // The engine settles what it heard and ends; the words go into the box then. One that does not end soon is ended
    // with the words it has, and an iPhone's later one that heard none says it is Safari's (item 616).
    tickHadFocus = done.matches(":focus-visible");
    done.disabled = true;
    cancel.disabled = true;
    dictation.stop();
    clearTimeout(settling);
    settling = null;
    // A dictation the stop ended at once (its engine had not begun: the browser still asked) leaves nothing to wait
    // for.
    if (dictation.listening()) {
      const pressedIn = dictations;
      settling = setTimeout(() => {
        if (pressedIn === dictations) {
          dictation.end();
        }
      }, SETTLE_MS);
    }
  });
  // What the owner types takes over: the dictation is dropped and what the box holds stays.
  const typed = () => {
    if (!writing && dictation.listening()) {
      drop();
    }
  };
  textarea.addEventListener("input", typed);
  paint(false);
  return {
    button,
    recorder,
    abort: drop,
    dispose() {
      textarea.removeEventListener("input", typed);
      clearInterval(ticking);
      clearTimeout(settling);
      dropped = true;
      dictation.dispose();
    },
  };
}
