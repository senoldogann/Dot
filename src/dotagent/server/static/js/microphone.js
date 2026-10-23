// The microphone of a composer (item 574): a button where the browser has speech recognition. It fills the box as the owner
// speaks, the words the engine may still change quieter until it settles them, and stops on a second press, after a few
// seconds of silence, when the owner types, when a message is sent and when the page goes out of sight. Nothing is sent by
// voice alone: the owner reads what the box holds and sends it as usual. The browser does the recognizing, and Safari and
// Chrome may send the voice to Apple's or Google's servers to do it (docs/safety.md); Dot sends nothing itself.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { voiceLanguage } from "./readaloud.js";
import { toast } from "./ui.js";
import { canListen, createDictation } from "./voice.js";

const START = "Dictate your message";
const STOP = "Stop dictating";

/**
 * The microphone of the box `textarea`, or null where the browser cannot listen. `setInterim(range)` draws the words that
 * may still change quieter (`[from, to]` in the box's text, or null for none). Returns `button`, to place in the
 * composer's bar, `abort()` for a message sent, and `dispose()`.
 */
export function createMicrophone({ textarea, setInterim }) {
  if (!canListen()) {
    return null;
  }
  const button = el("button", { class: "btn ghost sm mic-button", type: "button", "aria-pressed": "false" }, icon("mic"));
  // The words written into the box by the dictation itself: the input they raise is not the owner's typing.
  let writing = false;

  function paint(listening) {
    const label = listening ? STOP : START;
    button.setAttribute("aria-pressed", String(listening));
    button.setAttribute("aria-label", label);
    button.title = label;
    button.classList.toggle("listening", listening);
  }

  const dictation = createDictation({
    language: voiceLanguage,
    text: () => textarea.value,
    show(value, interimFrom) {
      writing = true;
      textarea.value = value;
      setInterim(interimFrom === null ? null : [interimFrom, value.length]);
      // The composer hears it as it hears typing: it grows the box, keeps the draft and wakes the send.
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      writing = false;
    },
    settled: () => setInterim(null),
    state: paint,
    problem: (words) => toast("error", words),
  });
  button.addEventListener("click", dictation.toggle);
  // What the owner types or deletes takes over: the microphone goes off and what the box holds stays.
  const typed = () => {
    if (!writing) {
      dictation.abort();
    }
  };
  textarea.addEventListener("input", typed);
  paint(false);
  return {
    button,
    abort: dictation.abort,
    dispose() {
      textarea.removeEventListener("input", typed);
      dictation.dispose();
    },
  };
}
