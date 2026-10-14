// The width of the run page's side panel (item 109), chosen on Dot's computer: compact (the panel's own 320 px), wide
// (half of the page), full (the conversation at its narrowest, the rest for the panel), or any width dragged on the
// handle between the conversation and the panel. Kept in this browser; it applies while the panel sits beside the
// conversation, never to the drawer of a narrow page. The frame carries it as `data-stage` and, for a dragged width,
// `--stage-px`; run.css turns both into the grid's columns.

import { el } from "./dom.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";

const SIZE_KEY = "dot.computer.size";
export const COMPACT_PX = 320;
// What the conversation keeps beside a dragged panel, the column of the handle, and a step of the arrow keys.
const CHAT_MIN_PX = 360;
const HANDLE_PX = 28;
const STEP_PX = 32;
export const STAGE_SIZES = [
  { id: "compact", label: "Compact", hint: "The panel at its own width", icon: "panelRight" },
  { id: "wide", label: "Wide", hint: "Half of the page for the panel", icon: "columns" },
  { id: "full", label: "Full", hint: "The conversation at its narrowest, the rest for the panel", icon: "maximize" },
];

/** The size kept in this browser: `{ size, px }`, `size` one of the three (compact while none was chosen) or "custom"
 * with `px` the width dragged. */
export function savedStageSize() {
  const stored = readPref(SIZE_KEY);
  if (stored === null || STAGE_SIZES.some((option) => option.id === stored)) {
    return { size: stored ?? "compact", px: null };
  }
  const px = Number.parseInt(stored, 10);
  if (!Number.isFinite(px)) {
    throw new Error(`the kept size of the panel is neither a size nor a width: ${stored}`);
  }
  return { size: "custom", px: Math.max(COMPACT_PX, px) };
}

/**
 * The size control of a run page whose frame is `layout`. Returns `chooser` (the three buttons, for Dot's computer),
 * `handle` (the separator between the conversation and the panel), `paint()` (puts the kept size on the frame) and
 * `dispose()`.
 */
export function createStageSize({ layout }) {
  let current = savedStageSize();
  let dragging = false;
  const buttons = STAGE_SIZES.map((option) =>
    el(
      "button",
      {
        class: "btn ghost sm icon-only",
        type: "button",
        title: option.hint,
        "aria-label": option.label,
        "aria-pressed": "false",
        "data-size": option.id,
        onclick: () => choose({ size: option.id, px: null }),
      },
      icon(option.icon),
    ),
  );
  const chooser = el("div", { class: "stage-sizes", role: "group", "aria-label": "Size of the panel" }, ...buttons);
  const handle = el("div", {
    class: "stage-handle",
    role: "separator",
    tabindex: "0",
    "aria-orientation": "vertical",
    "aria-label": "Width of the panel",
    title: "Drag to set the width of the panel; a double click gives it back its own width",
  });

  /** The room the conversation leaves the rail of its messages at its left, when it leaves one (item 228, run.css). */
  function railRoom() {
    const room = Number.parseFloat(getComputedStyle(layout).getPropertyValue("--outline-room"));
    return Number.isNaN(room) ? 0 : room;
  }

  function widest() {
    return Math.max(COMPACT_PX, layout.clientWidth - CHAT_MIN_PX - railRoom() - HANDLE_PX);
  }

  function clamped(px) {
    return Math.min(Math.max(Math.round(px), COMPACT_PX), widest());
  }

  // The panel's width as laid out: from the handle's right edge to the frame's.
  function measured() {
    return layout.getBoundingClientRect().right - handle.getBoundingClientRect().right;
  }

  function paint() {
    layout.dataset.stage = current.size;
    if (current.size === "custom") {
      layout.style.setProperty("--stage-px", `${current.px}px`);
    } else {
      layout.style.removeProperty("--stage-px");
    }
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.size === current.size));
    }
    handle.setAttribute("aria-valuemin", String(COMPACT_PX));
    handle.setAttribute("aria-valuemax", String(widest()));
    if (current.size === "custom") {
      handle.setAttribute("aria-valuenow", String(current.px));
    } else {
      handle.removeAttribute("aria-valuenow");
    }
  }

  function choose(next) {
    current = next;
    writePref(SIZE_KEY, next.size === "custom" ? String(next.px) : next.size);
    paint();
  }

  // The panel's left edge follows the pointer, half a handle to its right.
  function widthAt(clientX) {
    return clamped(layout.getBoundingClientRect().right - clientX - HANDLE_PX / 2);
  }

  function onPointerDown(event) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    dragging = true;
    handle.setPointerCapture(event.pointerId);
    handle.dataset.dragging = "true";
  }

  function onPointerMove(event) {
    if (!dragging) {
      return;
    }
    current = { size: "custom", px: widthAt(event.clientX) };
    paint();
  }

  function onPointerUp(event) {
    if (!dragging) {
      return;
    }
    dragging = false;
    delete handle.dataset.dragging;
    handle.releasePointerCapture(event.pointerId);
    choose(current);
  }

  // The arrows move the edge a step: left widens the panel, right narrows it.
  function onKeydown(event) {
    const step = event.key === "ArrowLeft" ? STEP_PX : event.key === "ArrowRight" ? -STEP_PX : 0;
    if (step === 0) {
      return;
    }
    event.preventDefault();
    choose({ size: "custom", px: clamped(measured() + step) });
  }

  const onDoubleClick = () => choose({ size: "compact", px: null });
  handle.addEventListener("pointerdown", onPointerDown);
  handle.addEventListener("pointermove", onPointerMove);
  handle.addEventListener("pointerup", onPointerUp);
  handle.addEventListener("pointercancel", onPointerUp);
  handle.addEventListener("keydown", onKeydown);
  handle.addEventListener("dblclick", onDoubleClick);
  return {
    chooser,
    handle,
    paint,
    dispose: () => {
      handle.removeEventListener("pointerdown", onPointerDown);
      handle.removeEventListener("pointermove", onPointerMove);
      handle.removeEventListener("pointerup", onPointerUp);
      handle.removeEventListener("pointercancel", onPointerUp);
      handle.removeEventListener("keydown", onKeydown);
      handle.removeEventListener("dblclick", onDoubleClick);
    },
  };
}
