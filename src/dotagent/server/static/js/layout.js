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

const TOUCH_FIRST = window.matchMedia("(hover: none) and (pointer: coarse)");
// A phone held upright, which gets the layout after T3 Code's phone app (items 294 to 298); the stylesheets share the
// width.
export const PHONE = window.matchMedia("(max-width: 720px)");

/** Whether the page is used by touch first, on a phone or a tablet: a box that takes the focus there opens the screen's
 * keyboard over half the page, so a page leaves the caret for a tap as it opens (item 293). */
export function touchFirst() {
  return TOUCH_FIRST.matches;
}

// iOS does not always tell of the last step of the keyboard's slide (iOS 26.5): after a change, the seen part is read
// again on every frame until it has held still for this many.
const STILL_FRAMES = 30;

/** Follow the part of the page that is seen (items 275 and 293). iOS's keyboard pans the page without resizing it, and
 * iOS 26 can leave it panned once the keyboard has gone: what is fixed to the window or sticks to its edges would go
 * out of sight at the top and leave a band at the bottom. `--seen-top` on the page element is how far the seen part
 * starts below the window's top, `--seen-bottom` how far it ends above the window's bottom (below it, negative); the
 * top bar, the dock, the drawers, the scrims and the dialogs stand on them (app.css, shell.css, run.css). The window's
 * bottom is that of the box what is fixed stands in, measured with an element fixed to its top and bottom
 * (`.seen-box`): with the keyboard up, iOS 26.5 shrinks `innerHeight` (to 636 px) and not that box (796 px). Returns the
 * function that stops it. */
export function followSeen() {
  const seen = window.visualViewport;
  const root = document.documentElement.style;
  const box = document.createElement("div");
  box.className = "seen-box";
  box.setAttribute("aria-hidden", "true");
  document.body.append(box);
  let top = null;
  let bottom = null;
  let still = 0;
  let frame = 0;

  /** Write where the seen part is; true when it moved. */
  function place() {
    const nextTop = Math.max(0, Math.round(seen.offsetTop));
    const nextBottom = Math.round(box.getBoundingClientRect().height - seen.offsetTop - seen.height);
    if (nextTop === top && nextBottom === bottom) {
      return false;
    }
    top = nextTop;
    bottom = nextBottom;
    root.setProperty("--seen-top", `${top}px`);
    root.setProperty("--seen-bottom", `${bottom}px`);
    return true;
  }

  function watch() {
    still = place() ? 0 : still + 1;
    frame = still < STILL_FRAMES ? requestAnimationFrame(watch) : 0;
  }

  function changed() {
    place();
    still = 0;
    if (frame === 0) {
      frame = requestAnimationFrame(watch);
    }
  }

  const sources = [
    [seen, "resize"],
    [seen, "scroll"],
    [window, "resize"],
    [document, "focusin"],
    [document, "focusout"],
  ];
  for (const [source, kind] of sources) {
    source.addEventListener(kind, changed);
  }
  place();
  return () => {
    for (const [source, kind] of sources) {
      source.removeEventListener(kind, changed);
    }
    cancelAnimationFrame(frame);
    box.remove();
  };
}
