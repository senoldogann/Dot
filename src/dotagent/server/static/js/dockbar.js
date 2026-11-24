// The one row over a phone's composer (item 1383, after the mobile app of a developer tool): a capsule of glass whose
// segments are divided by hairlines, with a round way down beside it. Its segments are the run's status with its Stop, its
// plan, its helpers, the chat's loops, its queued messages and the commands it has at work, each there only while it has
// something to say; with none, nothing is drawn. Where the panes above the composer once stood one upon the other, a tap on
// a segment opens its detail in a sheet (docksheet.js), and what Dot asks stays its own card above the row.
// The words are in dockwords.js; the row fits the room it has by saying fewer of them (see `fit`), and never scrolls
// sideways. A computer's window keeps the line over the composer that capsule.js draws; this row is not drawn there.

import { setText, el } from "./dom.js";
import { dockSegments } from "./dockwords.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";

// How much the row says, from the most to the least: every segment in words; the counts of the secondary ones only; the
// Stop without its word; the status's time alone; and, when even that does not fit, a second line rather than a cut.
const FITS = ["roomy", "counts", "compact", "tight", "wrapped"];
// The segments that open a sheet of their own, in the order of the capsule.
const SHEETED = ["plan", "helpers", "loops", "queue", "running"];

/**
 * @typedef {import("./dockwords.js").DockState} DockState
 * @typedef {{ button: HTMLButtonElement, long: HTMLElement, short: HTMLElement, mark: HTMLElement | null }} Part
 */

/**
 * The row. `onWork()` is a tap on the status, `onStop()` on its Stop and `onEnd()` on the way down; `sheets` is the page's
 * (docksheet.js), whose sheets the other segments open. Returns `element`, `segment(id)` (the button of a segment, for
 * the sheet it opens), `paint(state)` (a `DockState`: dockwords.js), `setEnd(shown, words)` (offer the way down, or
 * not), `setWorkOpen(open)` (the status's `aria-expanded`) and `dispose()`.
 */
export function createDockBar({ onWork, onStop, onEnd, sheets }) {
  /** @type {Map<string, Part>} */
  const parts = new Map();

  /** A segment: its button with a mark, its words at the roomiest width and its few words for a tight row. */
  function part(/** @type {string} */ id, /** @type {string} */ markName, /** @type {() => void} */ onclick) {
    const long = el("span", { class: "dock-long" });
    const short = el("span", { class: "dock-short" });
    const mark = markName === "" ? null : el("span", { class: "dock-mark", "aria-hidden": "true" }, icon(markName));
    const button = /** @type {HTMLButtonElement} */ (
      el("button", { class: `dock-seg dock-${id}`, type: "button", "data-segment": id, hidden: true, onclick }, mark, long, short)
    );
    parts.set(id, { button, long, short, mark });
    return button;
  }

  const status = part("status", "circleDashed", () => onWork());
  const stop = part("stop", "stop", () => onStop());
  const plan = part("plan", "squareCheck", () => undefined);
  const helpers = part("helpers", "users", () => undefined);
  const loops = part("loops", "refresh", () => undefined);
  const queue = part("queue", "listOrdered", () => undefined);
  const running = part("running", "terminal", () => undefined);
  status.setAttribute("aria-haspopup", "dialog");
  status.setAttribute("aria-expanded", "false");

  // A group, not a live region: its time changes every second, which a screen reader would read out each time.
  // The secondary segments stand in one box that draws none of its own (`display: contents`), so that a row too narrow for
  // them takes it as a second line of its own.
  const chips = el("div", { class: "dockbar-chips" }, plan, helpers, loops, queue, running);
  const capsule = el("div", { class: "dockbar-capsule", role: "group", "aria-label": t("capsule.label"), "data-fit": FITS[0], hidden: true }, status, stop, chips);
  const end = el("button", { class: "dock-end", type: "button", hidden: true, onclick: () => onEnd() }, icon("arrowDown"));
  const element = el("div", { class: "dockbar", hidden: true }, capsule, end);

  let endShown = false;
  // The mark the status wears: a dashed circle that turns while the run works, a bell while it waits, an alert for a lost stream.
  let markTone = "";

  /** Say less, step by step, until what the row holds fits the room it has. */
  function fit() {
    if (capsule.hidden) {
      return;
    }
    for (const level of FITS) {
      capsule.dataset.fit = level;
      if (level === FITS[FITS.length - 1] || capsule.scrollWidth <= capsule.clientWidth + 1) {
        return;
      }
    }
  }

  /** @param {DockState} state */
  function paint(state) {
    const segments = dockSegments(state);
    const found = new Map(segments.map((segment) => [segment.id, segment]));
    for (const [id, each] of parts) {
      const segment = found.get(/** @type {import("./dockwords.js").DockSegment["id"]} */ (id));
      each.button.hidden = segment === undefined;
      if (segment === undefined) {
        continue;
      }
      setText(each.long, segment.label);
      setText(each.short, segment.short);
      each.button.dataset.tone = segment.tone;
      // A name stays the long one whatever words the row has room for: a time alone ("6.4 s") is no name.
      each.button.setAttribute("aria-label", segment.name);
      if (id === "status" && each.mark !== null && markTone !== segment.tone) {
        markTone = segment.tone;
        each.mark.replaceChildren(icon(segment.tone === "waiting" ? "bell" : segment.tone === "warn" ? "alert" : "circleDashed"));
      }
    }
    capsule.hidden = segments.length === 0;
    element.hidden = capsule.hidden && !endShown;
    // A sheet whose segment went with what it told of (the last queued message started) has nothing to show.
    for (const id of SHEETED) {
      if (!found.has(/** @type {import("./dockwords.js").DockSegment["id"]} */ (id))) {
        sheets.close(id);
      }
    }
    fit();
  }

  // The row is fitted again when its width changes (a phone turned, a window resized), not when its own height does: a
  // second line would otherwise ask for it again.
  let lastWidth = 0;
  const width = new ResizeObserver((entries) => {
    if (entries.some((entry) => entry.contentRect.width !== lastWidth)) {
      lastWidth = element.clientWidth;
      fit();
    }
  });
  width.observe(element);

  return {
    element,
    segment: (/** @type {string} */ id) => /** @type {Part} */ (parts.get(id)).button,
    paint,
    setEnd: (/** @type {boolean} */ shown, /** @type {string} */ words) => {
      endShown = shown;
      end.hidden = !shown;
      end.title = words;
      end.setAttribute("aria-label", words);
      element.hidden = capsule.hidden && !shown;
      fit();
    },
    setWorkOpen: (/** @type {boolean} */ open) => status.setAttribute("aria-expanded", String(open)),
    dispose: () => width.disconnect(),
  };
}
