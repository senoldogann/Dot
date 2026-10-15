// How the pages are laid out. Folding the tool steps and showing the inspector follow the theme until the user decides
// otherwise; the sidebar remembers whether it was open. Everything is kept per browser (see prefs.js).

import { readPref, writePref } from "./prefs.js";
import { themeInUse } from "./theme.js";

const FOLD_KEY = "dot.foldSteps";
const INSPECTOR_KEY = "dot.inspector";
const SIDEBAR_KEY = "dot.sidebar";
const DENSITY_KEY = "dot.density";
// How close the rows, cards and stacks sit (the paddings are tokens in app.css, switched by `data-density` on the page).
export const DENSITIES = [
  { id: "comfortable", label: "Comfortable", hint: "Room around every row and card." },
  { id: "compact", label: "Compact", hint: "Rows, cards and lists sit closer, for more on one screen." },
];
const DEFAULT_DENSITY = "comfortable";

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

function isDensity(id) {
  return DENSITIES.some((density) => density.id === id);
}

/** The density on the page now. */
export function currentDensity() {
  const id = document.documentElement.dataset.density;
  return isDensity(id) ? id : DEFAULT_DENSITY;
}

/** Put the saved density on the page; call it before anything is drawn. */
export function applyStoredDensity() {
  const stored = readPref(DENSITY_KEY);
  document.documentElement.dataset.density = isDensity(stored) ? stored : DEFAULT_DENSITY;
}

/** Switch the density and remember the choice. */
export function chooseDensity(id) {
  if (!isDensity(id)) {
    throw new Error(`unknown density: ${id}`);
  }
  document.documentElement.dataset.density = id;
  writePref(DENSITY_KEY, id);
}

/** Whether the sidebar is open on a wide screen; open until the user hides it. */
export function sidebarOpen() {
  return readPref(SIDEBAR_KEY) !== "closed";
}

export function setSidebarOpen(value) {
  writePref(SIDEBAR_KEY, value ? "open" : "closed");
}

const STAGEBAR_PROPERTY = "--stagebar-h";

/** Keep `element`'s height in the CSS property `property` on `holder` as it changes. Returns the function that stops it. */
export function trackHeight(element, holder, property) {
  function measure() {
    holder.style.setProperty(property, `${element.offsetHeight}px`);
  }
  const observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
  return () => observer.disconnect();
}

/** Keep the stage bar's height in `--stagebar-h` on the page element, so the run's head and side panel can stay under
 * it: the banner, the density and the window's width all change that height. Returns the function that stops it. */
export function trackStagebar(stagebar) {
  return trackHeight(stagebar, document.documentElement, STAGEBAR_PROPERTY);
}

/** Hold the stage bar at the top of what is seen (item 275): iOS's keyboard pans the page without resizing it, which
 * takes a bar that sticks to the page's top out of sight, so the bar goes down as far as the seen part is panned.
 * Returns the function that stops it. */
export function holdBarInSight(stagebar) {
  const seen = window.visualViewport;
  function place() {
    const down = Math.max(0, Math.round(seen.offsetTop));
    stagebar.style.translate = down === 0 ? "" : `0 ${down}px`;
  }
  seen.addEventListener("resize", place);
  seen.addEventListener("scroll", place);
  place();
  return () => {
    seen.removeEventListener("resize", place);
    seen.removeEventListener("scroll", place);
  };
}
