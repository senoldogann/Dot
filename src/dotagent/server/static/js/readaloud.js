// The speaker button of an answer (item 574): it reads the answer aloud with the browser's own voice, and stops the reading
// when it is pressed again. An answer that comes in while Settings › General › Read answers aloud is on is read as it
// appears. Only one answer is read at a time. Nothing leaves the page: the browser speaks.

import { READ_ALOUD, VOICE_LANGUAGE, settingValue } from "./appearance/settings.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";
import { canSpeak, languageTag, primeVoices, readAloud } from "./voice.js";

const READ = "Read the answer aloud";
const STOP = "Stop reading";

// The voices are asked for as the page loads, so that the first answer read already has the list to choose a local voice from.
primeVoices();

/** The tag the voice listens and speaks in now: Settings › General's choice, the page's language when it says so. */
export function voiceLanguage() {
  return languageTag(settingValue(VOICE_LANGUAGE), document.documentElement.lang, navigator.language);
}

/**
 * The button that reads `text`, an answer's Markdown, aloud and stops it when pressed again (`aria-pressed` says which);
 * null where the browser cannot speak. With `now` (the answer has just come in) it starts reading at once when Settings ›
 * General › Read answers aloud is on.
 */
export function readAloudButton(text, now) {
  if (!canSpeak()) {
    return null;
  }
  let handle = null;
  const button = el("button", { class: "btn ghost sm icon-only read-aloud", type: "button", "aria-pressed": "false" }, icon("volume"));

  function paint(reading) {
    const label = reading ? STOP : READ;
    button.setAttribute("aria-pressed", String(reading));
    button.setAttribute("aria-label", label);
    button.title = label;
    button.replaceChildren(icon(reading ? "stop" : "volume"));
  }

  function press() {
    if (handle !== null) {
      handle.stop();
      return;
    }
    const started = readAloud(
      text,
      voiceLanguage(),
      () => {
        handle = null;
        paint(false);
      },
      (words) => toast("error", words),
    );
    // A reading that ended before it returned (nothing to say, a browser that failed at once) has painted its end.
    handle = started;
    paint(started !== null);
  }

  button.addEventListener("click", press);
  paint(false);
  if (now && settingValue(READ_ALOUD) === "on") {
    press();
  }
  return button;
}
