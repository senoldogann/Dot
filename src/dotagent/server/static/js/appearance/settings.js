// The settings of Settings › Appearance beside the theme and the scheme (item 127), after T3 Code's (see LICENSE): the
// contrast, the chat's width, the slide of the panels and the type. Each is kept in this browser under its key, the
// default as no key at all, and put on the page element as boot.js puts it there before the first paint: an attribute
// where the stylesheets switch on one, a token on the element's style where a value is carried, and nothing at the
// default. A family is one of the stacks app.css names (`--family-<id>`) or families typed, which go before the stack
// they replace, as T3 Code puts them, so that no glyph goes missing.

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

// One family typed, as CSS reads it: a plain name as it is, anything else in double quotes (T3 Code's
// quoteFontFamilyName, with the typed quotes dropped so that the quotes written always close).
function quoted(name) {
  const bare = name.replaceAll('"', "").replaceAll("'", "").trim();
  return bare === "" || (/^[a-zA-Z][a-zA-Z0-9-]*$/.test(bare) && !KEYWORDS.test(bare)) ? bare : `"${bare}"`;
}

/** The families typed as a CSS list, or null when nothing is typed (T3 Code's cssFontFamilies). */
export function familyList(typed) {
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

/** How long the sidebar and the details panel slide, 0 to 400 ms on steps of 25. */
export const PANEL_MOTION = {
  key: "dot.panelMotion",
  initial: 200,
  parse: wholeIn(0, 400, 25),
  paint: (value) => (value === 200 ? dropToken("--motion-panel") : setToken("--motion-panel", `${value}ms`)),
};

export const INTERFACE_FONT = family("dot.fontInterface", "--font", ["system"], "var(--family-inter)");

/** The interface's size, 13 to 17 px: the seven sizes of the type scaled by it over Dot's 14. */
export const INTERFACE_SIZE = {
  key: "dot.sizeInterface",
  initial: 14,
  parse: wholeIn(13, 17, 1),
  paint: (value) => (value === 14 ? dropToken("--type-scale") : setToken("--type-scale", String(value / 14))),
};

/** The answers' voice: the theme's (serif in Paper), the interface's font, or serif. */
export const ANSWER_FONT = {
  key: "dot.fontAnswers",
  initial: "theme",
  parse: oneOf(["theme", "interface", "serif"]),
  paint(value) {
    if (value === "theme") {
      dropToken("--voice");
    } else {
      setToken("--voice", value === "interface" ? "var(--font)" : "var(--family-serif)");
    }
  },
};

export const COMPOSER_FONT = family("dot.fontComposer", "--composer-font", ["serif", "mono"], "var(--font)");
export const COMPOSER_SIZE = size("dot.sizeComposer", "--composer-size", 12, 20, 15);
export const CODE_FONT = family("dot.fontCode", "--mono", ["sf-mono", "menlo"], "var(--family-geist-mono)");
export const CODE_SIZE = size("dot.sizeCode", "--code-size", 10, 18, 13);

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
  ANSWER_FONT,
  COMPOSER_FONT,
  COMPOSER_SIZE,
  CODE_FONT,
  CODE_SIZE,
  SMOOTHING,
  WRAP_CODE,
];

/** The value of `setting` kept in this browser, or its default when none is or what is kept is not one of its values. */
export function valueOf(setting) {
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
    setting.paint(valueOf(setting));
  }
}
