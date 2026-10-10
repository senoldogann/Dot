// The look of the interface: two skins on the same markup, picked with `data-theme` on the page element and remembered in
// this browser. Light and dark always follow the system. Each skin also says how the layout starts out (see layout.js).

import { readPref, writePref } from "./prefs.js";

export const THEMES = [
  {
    id: "neutral",
    label: "Neutral",
    hint: "White and graphite with a black accent, plain like a chat app.",
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

const THEME_KEY = "dot.theme";
const DEFAULT_THEME = "neutral";

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
