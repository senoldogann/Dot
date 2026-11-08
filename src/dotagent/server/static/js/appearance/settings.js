// The settings of Settings › Appearance beside the theme and the scheme (item 127), in part derived code (see LICENSE
// in this folder): the contrast, the chat's width, the slide of the panels and the type. Each is kept in this browser
// under its key, the default as no key at all, and put on the page element as boot.js puts it there before the first
// paint: an attribute where the stylesheets switch on one, a token on the element's style where a value is carried, and
// nothing at the default. A family is one of the stacks app.css names (`--family-<id>`) or families typed, which go
// before the stack they replace, so that no glyph goes missing.

import { readPref, removePref, writePref } from "../prefs.js";

const ROOT = document.documentElement;
// What a family typed may hold: letters, digits, spaces, commas between families, quotes, dots, dashes, underscores.
const TYPED_FAMILIES = /^[\p{L}\p{N} ,'"._-]{1,200}$/u;
// The names CSS reads as keywords in a list of families, quoted when typed as a family.
const KEYWORDS = /^(inherit|initial|unset|revert|revert-layer|default)$/i;

// A whole number between `least` and `most` on steps of `step` from `least`, read from its digits; else null.
function wholeIn(least, most, step) {
  return (stored) => {
    if (stored === null || !/^\d{1,3}$/.test(stored)) {
      return null;
    }
    const value = Number(stored);
    return value >= least && value <= most && (value - least) % step === 0 ? value : null;
  };
}

function oneOf(ids) {
  return (stored) => (ids.includes(stored) ? stored : null);
}

// One family typed, as CSS reads it: a plain name as it is, anything else in double quotes (the typed quotes are
// dropped, so that the quotes written always close).
function quoted(name) {
  const bare = name.replaceAll('"', "").replaceAll("'", "").trim();
  return bare === "" || (/^[a-zA-Z][a-zA-Z0-9-]*$/.test(bare) && !KEYWORDS.test(bare)) ? bare : `"${bare}"`;
}

/** The families typed as a CSS list, or null when nothing is typed. */
function familyList(typed) {
  const families = typed.split(",").map(quoted).filter((name) => name !== "");
  return families.length > 0 ? families.join(", ") : null;
}

function setToken(token, value) {
  ROOT.style.setProperty(token, value);
}

function dropToken(token) {
  ROOT.style.removeProperty(token);
}

function setFlag(name, value) {
  ROOT.setAttribute(name, value);
}

function dropFlag(name) {
  ROOT.removeAttribute(name);
}

// A family setting: "" for the stack app.css starts with, one of `stacks` (an id of `--family-<id>`), or families typed,
// written before `fallback`.
function family(key, token, stacks, fallback) {
  return {
    key,
    initial: "",
    stacks,
    parse: (stored) =>
      stored !== null && (stacks.includes(stored) || (TYPED_FAMILIES.test(stored) && familyList(stored) !== null))
        ? stored
        : null,
    paint(value) {
      if (value === "") {
        dropToken(token);
      } else if (stacks.includes(value)) {
        setToken(token, `var(--family-${value})`);
      } else {
        setToken(token, `${familyList(value)}, ${fallback}`);
      }
    },
  };
}

// A size in pixels on its own token, nothing written at `initial`.
function size(key, token, least, most, initial) {
  return {
    key,
    initial,
    parse: wholeIn(least, most, 1),
    paint: (value) => (value === initial ? dropToken(token) : setToken(token, `${value}px`)),
  };
}

/** The contrast in percent, 50 to 200 on steps of 5: below 100 the text and the lines fade, above it they deepen. */
export const CONTRAST = {
  key: "dot.contrast",
  initial: 100,
  parse: wholeIn(50, 200, 5),
  paint(value) {
    if (value === 100) {
      dropFlag("data-contrast");
      for (const token of ["--contrast-base", "--contrast-boost", "--contrast-border-boost"]) {
        dropToken(token);
      }
      return;
    }
    setFlag("data-contrast", "");
    setToken("--contrast-base", `${Math.min(value, 100)}%`);
    setToken("--contrast-boost", `${Math.max(value - 100, 0)}%`);
    setToken("--contrast-border-boost", `${Math.max(value - 100, 0) / 4}%`);
  },
};

/** The reading width of the home and the conversation: 768 px, 1152 px or the window's. */
export const CHAT_WIDTH = {
  key: "dot.chatWidth",
  initial: "comfortable",
  parse: oneOf(["comfortable", "wide", "full"]),
  paint: (value) => (value === "comfortable" ? dropFlag("data-chat-width") : setFlag("data-chat-width", value)),
};

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
export const PANEL_MOTION = {
  key: "dot.panelMotion",
  initial: 200,
  parse: wholeIn(0, 400, 25),
  paint: (value) => (value === 200 ? dropToken("--motion-panel") : setToken("--motion-panel", `${value}ms`)),
};

/** The families each of the four faces offers (item 1062), the same for all: ids of app.css's `--family-<id>`. The
 * answers and the message box, which follow the interface or the code, offer theirs too (`--family-interface`,
 * `--family-mono`); the interface and the code cannot take each other's, which would take theirs in a circle. */
const FAMILY_STACKS = ["system", "inter", "serif", "geist-mono", "sf-mono", "menlo"];
const FOLLOWING_STACKS = ["interface", "mono", ...FAMILY_STACKS];

/** The weights each of the four faces offers (item 1062), the same for all; 400 is each one's own. */
export const WEIGHTS = [
  { id: 300, label: "Light" },
  { id: 400, label: "Regular" },
  { id: 500, label: "Medium" },
  { id: 600, label: "Semibold" },
];

// A family of one of the four faces, of `stacks`, read with what a browser kept before item 1062 offered every face the
// same families: `legacy` maps such a value to the one it is now ("" for the face's own).
function faceFamily(key, token, stacks, fallback, legacy) {
  const base = family(key, token, stacks, fallback);
  return {
    ...base,
    parse: (stored) => (stored !== null && Object.hasOwn(legacy, stored) ? legacy[stored] : base.parse(stored)),
  };
}

// A weight on its own token, nothing written at 400.
function weight(key, token) {
  return {
    key,
    initial: 400,
    parse: (stored) => WEIGHTS.find((option) => String(option.id) === stored)?.id ?? null,
    paint: (value) => (value === 400 ? dropToken(token) : setToken(token, String(value))),
  };
}

// The interface's face: the system's by default (item 1057).
export const INTERFACE_FONT = faceFamily("dot.fontInterface", "--font", FAMILY_STACKS, "var(--family-system)", {});

// A phone, as app.css draws one (item 329): a touch screen 720 px wide or less, or under 500 px high on its side.
const PHONE_TYPE = window.matchMedia("(pointer: coarse) and (max-width: 720px), (pointer: coarse) and (max-height: 500px)");
// The body's own size on this device and the sizes it may be given: iOS's 16 px on a phone, down to 12 (item 347: the
// owner asked for smaller text there), Dot's 14 elsewhere. A message's own size is app.css's --text-md at 1: 16 on a
// phone, 15 elsewhere.
const BODY = PHONE_TYPE.matches ? { own: 16, least: 12, most: 20, message: 16 } : { own: 14, least: 13, most: 17, message: 15 };

/** The size of the body's text on this device (item 331), which the seven sizes of the type are scaled by over the
 * device's own; kept in this browser, so that each device has its own. */
export const INTERFACE_SIZE = {
  key: "dot.sizeInterface",
  initial: BODY.own,
  least: BODY.least,
  most: BODY.most,
  parse: wholeIn(BODY.least, BODY.most, 1),
  paint: (value) => (value === BODY.own ? dropToken("--type-scale") : setToken("--type-scale", String(value / BODY.own))),
};

export const INTERFACE_WEIGHT = weight("dot.weightInterface", "--weight-text");

/** The answers' face: the theme's (serif in Paper) by default. Before item 1062 "theme" was kept for it. */
export const ANSWER_FONT = faceFamily("dot.fontAnswers", "--voice", FOLLOWING_STACKS, "var(--theme-voice)", { theme: "" });

/** The answers' size (item 1062): a message's size scaled with the interface's until set, then its own, 12 to 24 px;
 * the line keeps the ratio of a message's. Its default is read anew, as the interface's size moves it; so it is painted
 * by whether a size is kept, not by what the default is now (a size kept stays when the default comes to it). Set to
 * the size it shows while it follows, it goes on following. */
export const ANSWER_SIZE = {
  key: "dot.sizeAnswers",
  get initial() {
    return Math.round((BODY.message * settingValue(INTERFACE_SIZE)) / BODY.own);
  },
  least: 12,
  most: 24,
  parse: wholeIn(12, 24, 1),
  paint(value) {
    if (this.parse(readPref(this.key)) === null) {
      dropToken("--answer-size");
    } else {
      setToken("--answer-size", `${value}px`);
    }
  },
};

export const ANSWER_WEIGHT = weight("dot.weightAnswers", "--answer-weight");

/** The message box's face: the interface's by default. */
export const COMPOSER_FONT = faceFamily("dot.fontComposer", "--composer-font", FOLLOWING_STACKS, "var(--font)", {});
export const COMPOSER_SIZE = size("dot.sizeComposer", "--composer-size", 12, 20, 15);
export const COMPOSER_WEIGHT = weight("dot.weightComposer", "--composer-weight");
/** The code's face: Geist Mono by default. */
export const CODE_FONT = faceFamily("dot.fontCode", "--mono", FAMILY_STACKS, "var(--family-geist-mono)", {});
export const CODE_SIZE = size("dot.sizeCode", "--code-size", 10, 18, 13);
export const CODE_WEIGHT = weight("dot.weightCode", "--code-weight");

/** Font smoothing, "on" (antialiased, Dot's) or "off" (the system's heavier strokes on a Mac). */
export const SMOOTHING = {
  key: "dot.smoothing",
  initial: "on",
  parse: oneOf(["on", "off"]),
  paint: (value) => (value === "on" ? dropFlag("data-smoothing") : setFlag("data-smoothing", "off")),
};

/** Long lines of code in an answer, "off" (scrolled sideways, Dot's) or "on" (wrapped). */
export const WRAP_CODE = {
  key: "dot.wrapCode",
  initial: "off",
  parse: oneOf(["on", "off"]),
  paint: (value) => (value === "off" ? dropFlag("data-wrap-code") : setFlag("data-wrap-code", "")),
};

const SETTINGS = [
  CONTRAST,
  CHAT_WIDTH,
  PANEL_MOTION,
  INTERFACE_FONT,
  INTERFACE_SIZE,
  INTERFACE_WEIGHT,
  ANSWER_FONT,
  ANSWER_SIZE,
  ANSWER_WEIGHT,
  COMPOSER_FONT,
  COMPOSER_SIZE,
  COMPOSER_WEIGHT,
  CODE_FONT,
  CODE_SIZE,
  CODE_WEIGHT,
  SMOOTHING,
  WRAP_CODE,
];

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
  for (const setting of SETTINGS) {
    setting.paint(settingValue(setting));
  }
}
