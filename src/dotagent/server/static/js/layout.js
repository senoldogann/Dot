// How the pages are laid out. Folding the tool steps and showing the inspector follow the theme until the user decides
// otherwise; the sidebar remembers whether it was open. Everything is kept per browser (see prefs.js).

import { readPref, writePref } from "./prefs.js";
import { themeInUse } from "./theme.js";

const FOLD_KEY = "dot.foldSteps";
const INSPECTOR_KEY = "dot.inspector";
const SIDEBAR_KEY = "dot.sidebar";

function choice(key) {
  const stored = readPref(key);
  return stored === "on" ? true : stored === "off" ? false : null;
}

function remember(key, value) {
  writePref(key, value ? "on" : "off");
}

/** Whether the tool steps of a run start folded into one "Worked for" line. */
export function foldSteps() {
  const chosen = choice(FOLD_KEY);
  return chosen === null ? themeInUse().foldSteps : chosen;
}

export function setFoldSteps(value) {
  remember(FOLD_KEY, value);
}

/** Whether the run page shows its side panel when there is room for it. */
export function showInspector() {
  const chosen = choice(INSPECTOR_KEY);
  return chosen === null ? themeInUse().inspector : chosen;
}

export function setShowInspector(value) {
  remember(INSPECTOR_KEY, value);
}

/** True once the user decided one of the layout choices themselves, so it no longer follows the theme. */
export function hasOwnLayout() {
  return choice(FOLD_KEY) !== null || choice(INSPECTOR_KEY) !== null;
}

/** Hand the layout choices back to the theme. */
export function followTheme() {
  writePref(FOLD_KEY, "theme");
  writePref(INSPECTOR_KEY, "theme");
}

/** Whether the sidebar is open on a wide screen; open until the user hides it. */
export function sidebarOpen() {
  return readPref(SIDEBAR_KEY) !== "closed";
}

export function setSidebarOpen(value) {
  writePref(SIDEBAR_KEY, value ? "open" : "closed");
}
