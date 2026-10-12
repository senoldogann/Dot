// The look of the interface (item 126): a theme picked for the light scheme and one for the dark, and the colour scheme
// itself, the system's or light or dark held with `data-scheme` on the page element (themes.css reads it through
// color-scheme). A theme is a look of themes.css (Neutral, Paper: a block of tokens and, for Paper, a voice and a few
// shapes) or one of T3 Code's palettes (appearance/palettes.js). The tokens of the two picks are written on the page
// element as `light-dark()` pairs (appearance/tokens.js), nothing when one look is picked for both, and kept in this
// browser with the picks so that boot.js puts them on the page before the first paint. `data-theme` names the theme of
// the scheme on screen, so a look's shapes follow it; the layout a theme starts with (see layout.js) is the light
// pick's.

import { PALETTES } from "./appearance/palettes.js";
import { THEME_TOKENS, lookTokens, paletteTokens, pairedTokens } from "./appearance/tokens.js";
import { readPref, removePref, writePref } from "./prefs.js";

// A theme: its id and words, whether it is a look of themes.css, the layout it starts with, its tokens for a scheme,
// and the three colours its orb is drawn with (appearance/orbs.js): the page, the accent and the action.
function look(id, label, hint, foldSteps) {
  const orb = (scheme) => {
    const tokens = lookTokens(id, scheme);
    return { canvas: tokens["--bg"], accent: tokens["--accent"], action: tokens["--bubble"] };
  };
  const tokens = (scheme) => lookTokens(id, scheme);
  return { id, label, hint, look: true, foldSteps, inspector: false, tokens, orb };
}

function palette(id, label, hint) {
  const orb = (scheme) => {
    const roles = PALETTES[id][scheme];
    return { canvas: roles.canvas, accent: roles.accent, action: roles.messageAction };
  };
  const tokens = (scheme) => paletteTokens(PALETTES[id][scheme]);
  return { id, label, hint, look: false, foldSteps: true, inspector: false, tokens, orb };
}

export const THEMES = [
  look("neutral", "Neutral", "Dot's own: warm grey and ink, plain like a chat app.", true),
  look("paper", "Paper", "Warm paper, serif answers and one terracotta accent.", false),
  palette("clean", "Clean", "T3 Code's own look: near-white, grey and a blue accent."),
  palette("rose", "Rose", "After T3 Chat: blush whites and a raspberry accent."),
  palette("grove", "Grove", "After T3 Code's Grove: sage greys and an ochre accent."),
  palette("ocean", "Ocean", "After T3 Code's Ocean: cool greys and a teal accent."),
  palette("ember", "Ember", "After T3 Code's Ember: warm stone and a rust accent."),
  palette("iris", "Iris", "After T3 Code's Iris: lavender greys and a magenta accent."),
];

export const SCHEMES = [
  { id: "system", label: "System", hint: "Light or dark as your system is." },
  { id: "light", label: "Light", hint: "Light, whatever the system says." },
  { id: "dark", label: "Dark", hint: "Dark, whatever the system says." },
];

// The theme picked for light, which is the pick for dark too unless THEME_DARK_KEY holds another.
const THEME_KEY = "dot.theme";
const THEME_DARK_KEY = "dot.themeDark";
// The tokens written for the picks, as `{ "--bg": "light-dark(…)", … }`, for boot.js.
const TOKENS_KEY = "dot.themeTokens";
const SCHEME_KEY = "dot.scheme";
const DEFAULT_THEME = "neutral";
const DEFAULT_SCHEME = "system";
const SYSTEM_DARK = window.matchMedia("(prefers-color-scheme: dark)");

const changeListeners = new Set();
const schemeListeners = new Set();

function themeOf(id) {
  return THEMES.find((theme) => theme.id === id);
}

function storedPick(key) {
  const stored = readPref(key);
  return stored !== null && themeOf(stored) !== undefined ? stored : null;
}

/** The themes picked now: `{ light, dark }`, two ids, the same one when a theme is picked for both. */
export function themePicks() {
  const light = storedPick(THEME_KEY) ?? DEFAULT_THEME;
  return { light, dark: storedPick(THEME_DARK_KEY) ?? light };
}

/** The scheme on screen: the one held, or the system's. */
export function shownScheme() {
  const held = document.documentElement.dataset.scheme;
  if (held === "light" || held === "dark") {
    return held;
  }
  return SYSTEM_DARK.matches ? "dark" : "light";
}

/** The description of the theme picked for light, whose layout the page starts with (layout.js). */
export function themeInUse() {
  return themeOf(themePicks().light);
}

/** Run `listener(picks)` after every change of a pick. Returns the function that stops it. */
export function onThemeChange(listener) {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

function paintShown() {
  const picks = themePicks();
  document.documentElement.dataset.theme = picks[shownScheme()];
}

function paint() {
  const picks = themePicks();
  const tokens = pairedTokens(themeOf(picks.light), themeOf(picks.dark));
  const style = document.documentElement.style;
  for (const token of THEME_TOKENS) {
    style.removeProperty(token);
  }
  for (const [token, value] of Object.entries(tokens)) {
    style.setProperty(token, value);
  }
  if (Object.keys(tokens).length === 0) {
    removePref(TOKENS_KEY);
  } else {
    writePref(TOKENS_KEY, JSON.stringify(tokens));
  }
  paintShown();
}

/**
 * Put the picks on the page: their tokens worked out again from the stylesheets and the palettes (boot.js has put
 * the kept ones on already), and `data-theme` kept on the theme of the scheme on screen as the system's changes.
 */
export function applyStoredTheme() {
  paint();
  SYSTEM_DARK.addEventListener("change", paintShown);
}

function picked() {
  paint();
  const picks = themePicks();
  for (const listener of changeListeners) {
    listener(picks);
  }
}

function known(id) {
  if (themeOf(id) === undefined) {
    throw new Error(`unknown theme: ${id}`);
  }
}

/** Pick a theme for both schemes and remember it. */
export function chooseTheme(id) {
  known(id);
  writePref(THEME_KEY, id);
  removePref(THEME_DARK_KEY);
  picked();
}

/** Pick a theme for one scheme ("light" or "dark") and remember it; the other keeps its pick. */
export function chooseThemeFor(scheme, id) {
  known(id);
  const picks = { ...themePicks(), [scheme]: id };
  writePref(THEME_KEY, picks.light);
  if (picks.dark === picks.light) {
    removePref(THEME_DARK_KEY);
  } else {
    writePref(THEME_DARK_KEY, picks.dark);
  }
  picked();
}

function isScheme(id) {
  return SCHEMES.some((scheme) => scheme.id === id);
}

/** The colour scheme chosen on the page now: "system" when it follows the system. */
export function currentScheme() {
  const id = document.documentElement.dataset.scheme;
  return isScheme(id) ? id : DEFAULT_SCHEME;
}

function setScheme(id) {
  if (id === DEFAULT_SCHEME) {
    delete document.documentElement.dataset.scheme;
  } else {
    document.documentElement.dataset.scheme = id;
  }
  paintShown();
}

/** Put the saved colour scheme on the page. Call it before anything is drawn; a missing or unknown saved value means the system's. */
export function applyStoredScheme() {
  const stored = readPref(SCHEME_KEY);
  setScheme(isScheme(stored) ? stored : DEFAULT_SCHEME);
}

/** Run `listener(id)` after every choice of a colour scheme. Returns the function that stops it. */
export function onSchemeChange(listener) {
  schemeListeners.add(listener);
  return () => schemeListeners.delete(listener);
}

/** Hold a colour scheme (or follow the system again) and remember the choice. */
export function chooseScheme(id) {
  if (!isScheme(id)) {
    throw new Error(`unknown colour scheme: ${id}`);
  }
  setScheme(id);
  writePref(SCHEME_KEY, id);
  for (const listener of schemeListeners) {
    listener(id);
  }
}

/** The next scheme after the one chosen now: System, Light, Dark and round again. */
export function nextScheme() {
  const index = SCHEMES.findIndex((scheme) => scheme.id === currentScheme());
  return SCHEMES[(index + 1) % SCHEMES.length].id;
}
