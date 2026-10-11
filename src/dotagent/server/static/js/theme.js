// The look of the interface: two skins on the same markup, picked with `data-theme` on the page element and remembered in
// this browser, and the colour scheme: the system's, or light or dark held with `data-scheme` on the page element
// (themes.css reads it through color-scheme). Each skin also says how the layout starts out (see layout.js).

import { readPref, writePref } from "./prefs.js";

export const THEMES = [
  {
    id: "neutral",
    label: "Neutral",
    hint: "Warm grey and ink, plain like a chat app.",
    foldSteps: true,
    inspector: false,
  },
  {
    id: "paper",
    label: "Paper",
    hint: "Warm paper, serif answers and one terracotta accent.",
    foldSteps: false,
    inspector: false,
  },
];

export const SCHEMES = [
  { id: "system", label: "System", hint: "Light or dark as your system is." },
  { id: "light", label: "Light", hint: "Light, whatever the system says." },
  { id: "dark", label: "Dark", hint: "Dark, whatever the system says." },
];

const THEME_KEY = "dot.theme";
const SCHEME_KEY = "dot.scheme";
const DEFAULT_THEME = "neutral";
const DEFAULT_SCHEME = "system";

const changeListeners = new Set();

function isTheme(id) {
  return THEMES.some((theme) => theme.id === id);
}

/** The theme that is on the page now. */
export function currentTheme() {
  const id = document.documentElement.dataset.theme;
  return isTheme(id) ? id : DEFAULT_THEME;
}

/** The description of the theme that is on the page now, with the layout it prefers. */
export function themeInUse() {
  return THEMES.find((theme) => theme.id === currentTheme());
}

/** Run `listener(id)` after every switch of theme. Returns the function that stops it. */
export function onThemeChange(listener) {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

/** Put the saved theme on the page. Call it before anything is drawn; a missing or unknown saved value means the default. */
export function applyStoredTheme() {
  const stored = readPref(THEME_KEY);
  document.documentElement.dataset.theme = isTheme(stored) ? stored : DEFAULT_THEME;
}

/** Switch to a theme and remember the choice. */
export function chooseTheme(id) {
  if (!isTheme(id)) {
    throw new Error(`unknown theme: ${id}`);
  }
  document.documentElement.dataset.theme = id;
  writePref(THEME_KEY, id);
  for (const listener of changeListeners) {
    listener(id);
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
  if (id === DEFAULT_SCHEME) {
    delete document.documentElement.dataset.scheme;
  } else {
    document.documentElement.dataset.scheme = id;
  }
}

/** Put the saved colour scheme on the page. Call it before anything is drawn; a missing or unknown saved value means the system's. */
export function applyStoredScheme() {
  const stored = readPref(SCHEME_KEY);
  setScheme(isScheme(stored) ? stored : DEFAULT_SCHEME);
}

/** Hold a colour scheme (or follow the system again) and remember the choice. */
export function chooseScheme(id) {
  if (!isScheme(id)) {
    throw new Error(`unknown colour scheme: ${id}`);
  }
  setScheme(id);
  writePref(SCHEME_KEY, id);
}
