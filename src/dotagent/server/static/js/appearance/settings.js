// The settings of Settings › Appearance beside the theme and the scheme (item 127), and the choices of Settings ›
// General and › Browser kept beside them. Each is kept in this browser under its key, the default as no key at all.
// Those that paint the page are appearance/rules.js's rules (item 1067), which boot.js paints what is kept with before
// the first paint: one copy of each rule, so that what the page shows once it runs is what it showed at its first paint.

import { readPref, removePref, writePref } from "../prefs.js";
// The rules themselves, which index.html runs before the first paint; imported here too, so that every page that takes
// this module has them, a page of a test among them (the nightly run of 2026-11-09 found four whose modules stopped at
// a missing DOT_APPEARANCE, item 1087). Run twice, the script sets the same rules again.
import "./rules.js";

const ROOT = document.documentElement;
const APPEARANCE = globalThis.DOT_APPEARANCE;

function oneOf(ids) {
  return (stored) => (ids.includes(stored) ? stored : null);
}

/** A setting of the page, from its rule: its default read anew (the answers' size follows the interface's), and painted
 * as kept or not, as boot.js paints it. */
function painted(rule) {
  return {
    key: rule.key,
    get initial() {
      return rule.initial(readPref);
    },
    least: rule.least,
    most: rule.most,
    stacks: rule.stacks,
    parse: rule.parse,
    paint: (value) => rule.paint(value, rule.parse(readPref(rule.key)) !== null, ROOT),
  };
}

/** The contrast in percent, 50 to 200 on steps of 5: below 100 the text and the lines fade, above it they deepen. */
export const CONTRAST = painted(APPEARANCE.rules.contrast);

/** The reading width of the home and the conversation: 768 px, 1152 px or the window's. */
export const CHAT_WIDTH = painted(APPEARANCE.rules.chatWidth);

/** The key that sends what a composer holds (item 230): Enter, Shift+Enter breaking a line, or ⌘Enter (Ctrl+Enter),
 * Enter breaking it. Settings › General chooses it; it puts nothing on the page. On a touch screen Return always
 * breaks the line (item 440). */
export const SEND_KEY = {
  key: "dot.sendKey",
  initial: "enter",
  parse: oneOf(["enter", "mod-enter"]),
  paint: () => undefined,
};

/** What a message to a run at work does unless ⌘⇧Enter asks the other way (item 230): it waits in the chat's queue, or
 * steers the run at once. Settings › General chooses it; it puts nothing on the page. */
export const BUSY_SEND = {
  key: "dot.busySend",
  initial: "queue",
  parse: oneOf(["queue", "steer"]),
  paint: () => undefined,
};

/** Whether a chat shows the line of each action the reviewer approved (item 346). It does not until asked to (item 362).
 * A refusal shows either way, as what it held back did not happen, and a step yolo let through draws none (item 500).
 * Settings › General chooses it; it puts nothing on the page. */
export const AUTO_LINES = {
  key: "dot.autoLines",
  initial: "hidden",
  parse: oneOf(["shown", "hidden"]),
  paint: () => undefined,
};

/** Whether a new chat shows the day: the now line over its composer (item 72) and under it While you were away (item
 * 466) and the Today strip (what waits, works, is due, was learned and ended today, items 124 and 364). It does not
 * until asked to: a new chat is the greeting and the composer alone. Settings › General chooses it; it puts nothing on
 * the page. */
export const HOME_DAY = {
  key: "dot.homeDay",
  initial: "hidden",
  parse: oneOf(["shown", "hidden"]),
  paint: () => undefined,
};

/** The languages the microphone listens in and an answer is read aloud in (item 574): the page's own, or one of these tags. */
export const VOICE_LANGUAGES = [
  { id: "page", label: "The page's language" },
  { id: "en-US", label: "English" },
  { id: "tr-TR", label: "Türkçe" },
  { id: "fi-FI", label: "Suomi" },
  { id: "sv-SE", label: "Svenska" },
  { id: "de-DE", label: "Deutsch" },
  { id: "fr-FR", label: "Français" },
  { id: "es-ES", label: "Español" },
];

/** The language of the voice (item 574): the page's own, or one of `VOICE_LANGUAGES`. Settings › General chooses it; it puts
 * nothing on the page. */
export const VOICE_LANGUAGE = {
  key: "dot.voiceLanguage",
  initial: "page",
  parse: oneOf(VOICE_LANGUAGES.map((language) => language.id)),
  paint: () => undefined,
};

/** Whether an answer that comes in is read aloud (item 574). Off until asked to. Settings › General chooses it; it puts nothing
 * on the page. */
export const READ_ALOUD = {
  key: "dot.readAloud",
  initial: "off",
  parse: oneOf(["on", "off"]),
  paint: () => undefined,
};

/** Whether the details panel opens on Dot's computer when a run that is going on first works in its browser (item 354),
 * beside the chat on a wide window; a phone keeps the button of its bar. Settings › Browser chooses it; it puts
 * nothing on the page. */
export const AUTO_COMPUTER = {
  key: "dot.autoComputer",
  initial: "shown",
  parse: oneOf(["shown", "hidden"]),
  paint: () => undefined,
};

/** How long the sidebar and the details panel slide, 0 to 400 ms on steps of 25. */
export const PANEL_MOTION = painted(APPEARANCE.rules.panelMotion);

/** The weights each of the four faces offers (item 1062), the same for all; 400 is each one's own. */
export const WEIGHTS = APPEARANCE.weights;

// The four faces of the type (item 1062), each with its family, its size and its weight (appearance/rules.js).
export const INTERFACE_FONT = painted(APPEARANCE.rules.interfaceFont);
export const INTERFACE_SIZE = painted(APPEARANCE.rules.interfaceSize);
export const INTERFACE_WEIGHT = painted(APPEARANCE.rules.interfaceWeight);
export const ANSWER_FONT = painted(APPEARANCE.rules.answerFont);
export const ANSWER_SIZE = painted(APPEARANCE.rules.answerSize);
export const ANSWER_WEIGHT = painted(APPEARANCE.rules.answerWeight);
export const COMPOSER_FONT = painted(APPEARANCE.rules.composerFont);
export const COMPOSER_SIZE = painted(APPEARANCE.rules.composerSize);
export const COMPOSER_WEIGHT = painted(APPEARANCE.rules.composerWeight);
export const CODE_FONT = painted(APPEARANCE.rules.codeFont);
export const CODE_SIZE = painted(APPEARANCE.rules.codeSize);
export const CODE_WEIGHT = painted(APPEARANCE.rules.codeWeight);

/** Font smoothing, "on" (antialiased, Dot's) or "off" (the system's heavier strokes on a Mac). */
export const SMOOTHING = painted(APPEARANCE.rules.smoothing);

/** Long lines of code in an answer, "off" (scrolled sideways, Dot's) or "on" (wrapped). */
export const WRAP_CODE = painted(APPEARANCE.rules.wrapCode);

/** The value of `setting` kept in this browser, or its default when none is or what is kept is not one of its values. */
export function settingValue(setting) {
  return setting.parse(readPref(setting.key)) ?? setting.initial;
}

/** Whether `value` is one `setting` can take. */
export function accepts(setting, value) {
  return value === setting.initial || setting.parse(String(value)) !== null;
}

/** Keep `value` for `setting` in this browser (the default as no key) and put it on the page. */
export function setValue(setting, value) {
  if (!accepts(setting, value)) {
    throw new Error(`${setting.key} cannot be ${value}`);
  }
  if (value === setting.initial) {
    removePref(setting.key);
  } else {
    writePref(setting.key, String(value));
  }
  setting.paint(value);
}

/** Put every setting kept in this browser on the page, as boot.js did before the first paint. */
export function applyStoredSettings() {
  APPEARANCE.paintAll(readPref, ROOT);
}
