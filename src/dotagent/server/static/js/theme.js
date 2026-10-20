// The look of the interface (item 126): a theme picked for the light scheme and one for the dark, and the colour scheme
// itself, the system's or light or dark held with `data-scheme` on the page element (themes.css reads it through
// color-scheme). A theme is a look of themes.css (Neutral, Paper: a block of tokens and, for Paper, a voice and a few
// shapes) or a palette (appearance/palettes.js). The tokens of the two picks are written on the page
// element as `light-dark()` pairs (appearance/tokens.js), nothing when one look is picked for both, and kept in this
// browser with the picks so that boot.js puts them on the page before the first paint. `data-theme` names the theme of
// the scheme on screen, so a look's shapes follow it; the layout a theme starts with (see layout.js) is the light
// pick's. Themes of one's own (item 128, appearance/own.js) are palettes the daemon keeps, some for one scheme only;
// they join Dot's once the page has read them, and until then a pick of one stands as boot.js put it on the page, so
// that the page never shows another theme for a moment. While a theme is being made the page is drawn in it
// (previewTokens) and the picks come back after (endPreview).

import { PALETTES } from "./appearance/palettes.js";
import { MORE_PALETTES, MORE_THEMES } from "./appearance/palettes-more.js";
import { THEME_TOKENS, lookTokens, paletteTokens, pairedTokens } from "./appearance/tokens.js";
import { readPref, removePref, writePref } from "./prefs.js";

// A theme: its id and words, whether it is a look of themes.css and whether one's own, the schemes it has a palette
// for, the layout it starts with, its tokens for a scheme, and the three colours its orb is drawn with
// (appearance/orbs.js): the page, the accent and the action.
const BOTH = ["light", "dark"];
// The palettes of palettes.js and Dot's more of palettes-more.js, by id.
const ALL_PALETTES = { ...PALETTES, ...MORE_PALETTES };

function look(id, label, hint, foldSteps) {
  const orb = (scheme) => {
    const tokens = lookTokens(id, scheme);
    return { canvas: tokens["--bg"], accent: tokens["--accent"], action: tokens["--bubble"] };
  };
  const tokens = (scheme) => lookTokens(id, scheme);
  return { id, label, hint, look: true, own: false, schemes: BOTH, foldSteps, inspector: false, tokens, orb };
}

function palette(id, label, hint) {
  const orb = (scheme) => {
    const roles = ALL_PALETTES[id][scheme];
    return { canvas: roles.canvas, accent: roles.accent, action: roles.messageAction };
  };
  const tokens = (scheme) => paletteTokens(ALL_PALETTES[id][scheme]);
  return { id, label, hint, look: false, own: false, schemes: BOTH, foldSteps: true, inspector: false, tokens, orb };
}

/** Dot's own themes. */
export const THEMES = [
  look("neutral", "Neutral", "Dot's own: warm grey and ink, plain like a chat app.", true),
  look("paper", "Paper", "Warm paper, serif answers and one terracotta accent.", false),
  palette("clean", "Clean", "Near-white, grey and a blue accent."),
  palette("rose", "Rose", "Blush whites and a raspberry accent."),
  palette("grove", "Grove", "Sage greys and an ochre accent."),
  palette("ocean", "Ocean", "Cool greys and a teal accent."),
  palette("ember", "Ember", "Warm stone and a rust accent."),
  palette("iris", "Iris", "Lavender greys and a magenta accent."),
  ...MORE_THEMES.map(([id, label, hint]) => palette(id, label, hint)),
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
// What `data-theme` says while a theme being made is on the page: no look's shapes.
const PREVIEW_THEME = "draft";

const changeListeners = new Set();
const schemeListeners = new Set();
const listListeners = new Set();
// The themes of one's own, in the order of their files; null until the page has read them.
let ownThemes = null;
let previewing = false;

/** Every theme: Dot's, then one's own. */
export function allThemes() {
  return [...THEMES, ...(ownThemes ?? [])];
}

/** The theme of `id`, or undefined when there is none. */
export function findTheme(id) {
  return allThemes().find((theme) => theme.id === id);
}

function offers(id, scheme) {
  const theme = findTheme(id);
  return theme?.schemes.includes(scheme) === true;
}

function storedPick(key, scheme) {
  const stored = readPref(key);
  return stored !== null && offers(stored, scheme) ? stored : null;
}

// Whether a pick names a theme the page has not read yet: one of one's own, before the daemon's list has come.
function pending() {
  return (
    ownThemes === null &&
    [THEME_KEY, THEME_DARK_KEY].some((key) => {
      const stored = readPref(key);
      return stored !== null && !THEMES.some((theme) => theme.id === stored);
    })
  );
}

/**
 * The themes picked now: `{ light, dark }`, two ids, the same one when a theme is picked for both. A pick of a theme
 * that is gone, or that has no palette for its scheme, is Neutral.
 */
export function themePicks() {
  const light = storedPick(THEME_KEY, "light") ?? DEFAULT_THEME;
  return { light, dark: storedPick(THEME_DARK_KEY, "dark") ?? (offers(light, "dark") ? light : DEFAULT_THEME) };
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
  return findTheme(themePicks().light);
}

/** Run `listener(picks)` after every change of a pick. Returns the function that stops it. */
export function onThemeChange(listener) {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

function paintShown() {
  if (pending() || previewing) {
    return;
  }
  const picks = themePicks();
  document.documentElement.dataset.theme = picks[shownScheme()];
}

function paint() {
  const picks = themePicks();
  const tokens = pairedTokens(findTheme(picks.light), findTheme(picks.dark));
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
 * the kept ones on already), and `data-theme` kept on the theme of the scheme on screen as the system's changes. A
 * pick of a theme of one's own waits for the daemon's list (setOwnThemes).
 */
export function applyStoredTheme() {
  if (!pending()) {
    paint();
  }
  SYSTEM_DARK.addEventListener("change", paintShown);
}

function picked() {
  if (!previewing) {
    paint();
  }
  const picks = themePicks();
  for (const listener of changeListeners) {
    listener(picks);
  }
}

/** Take the themes of one's own the daemon keeps, in place of the ones read before, and put the picks on the page. */
export function setOwnThemes(themes) {
  ownThemes = themes;
  for (const listener of listListeners) {
    listener();
  }
  picked();
}

/** Run `listener()` after every change of the themes of one's own. Returns the function that stops it. */
export function onThemesChange(listener) {
  listListeners.add(listener);
  return () => listListeners.delete(listener);
}

function offered(id, schemes) {
  if (!schemes.every((scheme) => offers(id, scheme))) {
    throw new Error(`no theme ${id} for ${schemes.join(" and ")}`);
  }
}

/** Pick a theme for both schemes and remember it. */
export function chooseTheme(id) {
  offered(id, BOTH);
  writePref(THEME_KEY, id);
  removePref(THEME_DARK_KEY);
  picked();
}

function keepPicks(picks) {
  writePref(THEME_KEY, picks.light);
  if (picks.dark === picks.light) {
    removePref(THEME_DARK_KEY);
  } else {
    writePref(THEME_DARK_KEY, picks.dark);
  }
  picked();
}

/** Pick a theme for one scheme ("light" or "dark") and remember it; the other keeps its pick. */
export function chooseThemeFor(scheme, id) {
  offered(id, [scheme]);
  keepPicks({ ...themePicks(), [scheme]: id });
}

/** Let go of a theme that is gone: a scheme it was picked for goes back to Neutral. */
export function forgetTheme(id) {
  const picks = themePicks();
  if (picks.light === id || picks.dark === id) {
    keepPicks({
      light: picks.light === id ? DEFAULT_THEME : picks.light,
      dark: picks.dark === id ? DEFAULT_THEME : picks.dark,
    });
  }
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
  if (previewing) {
    return;
  }
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

/**
 * Keep the browser's bar (`theme-color`: a phone's status bar, Safari's tab bar) the colour of the page's ground, so
 * that it follows the theme and the scheme on screen (item 328). index.html names the default theme's light and dark
 * grounds for the first paint; from here one meta follows the page.
 */
export function followBarColour() {
  for (const first of document.querySelectorAll('meta[name="theme-color"]')) {
    first.remove();
  }
  const meta = document.createElement("meta");
  meta.name = "theme-color";
  document.head.append(meta);
  const paintBar = () => {
    meta.content = getComputedStyle(document.body).backgroundColor;
  };
  paintBar();
  new MutationObserver(paintBar).observe(document.documentElement, { attributeFilter: ["data-scheme", "data-theme", "data-contrast", "style"] });
  SYSTEM_DARK.addEventListener("change", paintBar);
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

/**
 * Draw the page in `tokens` (plain colours, as appearance/tokens.js paletteTokens gives them) in `scheme`, while a
 * theme is being made; the picks and the scheme held are kept and come back with endPreview().
 */
export function previewTokens(tokens, scheme) {
  previewing = true;
  const root = document.documentElement;
  for (const token of THEME_TOKENS) {
    root.style.removeProperty(token);
  }
  for (const [token, value] of Object.entries(tokens)) {
    root.style.setProperty(token, value);
  }
  root.dataset.scheme = scheme;
  root.dataset.theme = PREVIEW_THEME;
}

/** Put the picks and the scheme held back on the page after previewTokens(). */
export function endPreview() {
  previewing = false;
  applyStoredScheme();
  if (!pending()) {
    paint();
  }
}
