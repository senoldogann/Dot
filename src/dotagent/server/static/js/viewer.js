/**
 * The full-screen viewer of what a run shows (item 1056): a picture (a screenshot, a picture of a file) or a page a run
 * showed (`show_page`). It covers the whole screen, the phone's safe areas kept clear, and it zooms: with two fingers,
 * a double tap, the trackpad's pinch or Ctrl and the wheel, and its −, + and Fit; zoomed, the content is dragged.
 * Close, Escape, a tap beside a picture or a swipe down of a picture at its fitted size close it.
 *
 * A page stays in its sandboxed frame, which this module never reaches into: at its fitted size it is the page's own,
 * links, choices and scrolling; zoomed, a clear layer lies over it, so that the gestures move the view and not the page.
 *
 * Its states are explicit: the view (`scale`, `x`, `y`, kept within the content by `clamped`) and the gesture
 * (`idle`, `drag`, `pinch`), each changed in one place.
 */
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { MAX_SCALE, MIN_SCALE, clamped, pinchedScale, zoomedAbout } from "./viewmath.js";

// The event a page's frame full screen is sent for a pinch on the page (item 1056, pageframe.js): `{ phase, ratio, x, y }`.
export const ZOOM_EVENT = "dot:zoom";

// The scale a double tap zooms to, and the step of − and +.
const TAP_SCALE = 2.5;
const STEP = 1.5;
// Two taps this close in time and place are a double tap.
const DOUBLE_TAP_MS = 300;
const DOUBLE_TAP_PX = 24;
// A press that moved less than this is a tap, not a drag.
const TAP_SLOP_PX = 8;
// A swipe down this far closes a picture at its fitted size.
const SWIPE_CLOSE_PX = 110;

/**
 * Open the viewer over everything. `content` is what it shows: an `img`, or the frame of a page (`page: true`), which
 * fills the stage. `onClose()` runs once it has closed (the focus goes back to what opened it).
 * @param {{ title: string, content: HTMLElement, page: boolean, onClose: () => void }} options
 */
export function openViewer({ title, content, page, onClose }) {
  const level = el("span", { class: "viewer-level", "aria-live": "polite" }, "100%");
  const out = el("button", { class: "viewer-tool", type: "button", "aria-label": "Zoom out", title: "Zoom out" }, icon("minus"));
  const into = el("button", { class: "viewer-tool", type: "button", "aria-label": "Zoom in", title: "Zoom in" }, icon("plus"));
  const fit = el("button", { class: "viewer-tool viewer-fit", type: "button", "aria-label": "Fit", title: "Fit" }, "Fit");
  const close = el("button", { class: "viewer-tool viewer-close", type: "button", "aria-label": "Close", title: "Close" }, icon("x"));
  // What a zoomed page is covered with, so that the gestures reach the viewer and not the page.
  // A picture has none: a tap on it must reach it, and only one beside it closes the viewer.
  const glass = page ? el("div", { class: "viewer-glass", hidden: true }) : null;
  const layer = el("div", { class: page ? "viewer-layer viewer-layer-page" : "viewer-layer" }, content);
  const stage = el("div", { class: "viewer-stage" }, layer, glass);
  const dialog = el(
    "dialog",
    { class: page ? "viewer viewer-page" : "viewer viewer-picture", "aria-label": title },
    el("div", { class: "viewer-head" }, el("span", { class: "viewer-title" }, title), el("div", { class: "viewer-tools" }, out, level, into, fit, close)),
    stage,
  );

  let view = { scale: 1, x: 0, y: 0 };
  /** @type {"idle" | "drag" | "pinch"} */
  let gesture = "idle";
  const pointers = new Map();
  // Where the gesture began: the view, the pointers' middle and their distance, and the first pointer's place.
  // The element a press began on: with the pointer captured, the events after it name the stage.
  let pressed = /** @type {EventTarget | null} */ (null);
  let start = { view, mx: 0, my: 0, distance: 1, px: 0, py: 0, moved: false };
  let lastTap = { at: 0, x: 0, y: 0 };

  function paint() {
    layer.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
    level.textContent = `${Math.round(view.scale * 100)}%`;
    out.disabled = view.scale <= MIN_SCALE;
    into.disabled = view.scale >= MAX_SCALE;
    fit.disabled = view.scale === MIN_SCALE;
    // A page at its fitted size is the page's own; zoomed, the glass takes the gestures.
    if (glass !== null) {
      glass.hidden = view.scale === MIN_SCALE;
    }
  }

  function show(next) {
    view = clamped(next, stage.clientWidth, stage.clientHeight);
    paint();
  }

  function zoomTo(scale, px, py) {
    show(zoomedAbout(view, Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale)), px, py));
  }

  function middle() {
    const box = stage.getBoundingClientRect();
    return { x: box.width / 2, y: box.height / 2 };
  }

  function local(event) {
    const box = stage.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function begin() {
    const points = [...pointers.values()];
    if (points.length >= 2) {
      gesture = "pinch";
      const [a, b] = points;
      start = { ...start, view, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), moved: true };
    } else if (points.length === 1) {
      // The finger left of a pinch drags on, and is no tap when it lifts.
      const fromPinch = gesture === "pinch";
      gesture = "drag";
      start = { ...start, view, px: points[0].x, py: points[0].y, moved: fromPinch };
    } else {
      gesture = "idle";
    }
  }

  function onDown(event) {
    if (event.button !== 0 && event.pointerType === "mouse") {
      return;
    }
    if (pointers.size === 0) {
      pressed = event.target;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, local(event));
    begin();
  }

  function onMove(event) {
    if (!pointers.has(event.pointerId)) {
      return;
    }
    pointers.set(event.pointerId, local(event));
    const points = [...pointers.values()];
    if (gesture === "pinch" && points.length >= 2) {
      const [a, b] = points;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, (start.view.scale * Math.hypot(a.x - b.x, a.y - b.y)) / start.distance));
      // The point under the fingers' middle at the start follows the middle as it moves.
      const about = zoomedAbout(start.view, scale, start.mx, start.my);
      show({ scale, x: about.x + (mx - start.mx), y: about.y + (my - start.my) });
    } else if (gesture === "drag" && points.length === 1) {
      const dx = points[0].x - start.px;
      const dy = points[0].y - start.py;
      if (Math.hypot(dx, dy) > TAP_SLOP_PX) {
        start.moved = true;
      }
      if (view.scale > MIN_SCALE) {
        show({ scale: view.scale, x: start.view.x + dx, y: start.view.y + dy });
      } else if (!page && dy > 0) {
        // A picture at its fitted size follows a swipe down, and goes when it is let go far enough.
        layer.style.transform = `translate(0px, ${dy}px)`;
        layer.style.opacity = String(Math.max(0.3, 1 - dy / (SWIPE_CLOSE_PX * 3)));
      }
    }
  }

  function onUp(event) {
    if (!pointers.has(event.pointerId)) {
      return;
    }
    const point = local(event);
    pointers.delete(event.pointerId);
    const was = gesture;
    const moved = start.moved;
    const dy = point.y - start.py;
    begin();
    if (was !== "drag" || pointers.size > 0) {
      return;
    }
    if (!page && view.scale === MIN_SCALE && moved && dy > SWIPE_CLOSE_PX) {
      dialog.close();
      return;
    }
    layer.style.opacity = "";
    paint();
    if (moved) {
      return;
    }
    const now = Date.now();
    if (now - lastTap.at < DOUBLE_TAP_MS && Math.hypot(point.x - lastTap.x, point.y - lastTap.y) < DOUBLE_TAP_PX) {
      lastTap = { at: 0, x: 0, y: 0 };
      if (view.scale > MIN_SCALE) {
        show({ scale: MIN_SCALE, x: 0, y: 0 });
      } else {
        zoomTo(TAP_SCALE, point.x, point.y);
      }
      return;
    }
    lastTap = { at: now, x: point.x, y: point.y };
    // A tap beside a picture at its fitted size closes it, once it is sure to be no double tap.
    if (!page && view.scale === MIN_SCALE && pressed !== content) {
      const tapped = lastTap.at;
      setTimeout(() => {
        if (lastTap.at === tapped && dialog.open) {
          dialog.close();
        }
      }, DOUBLE_TAP_MS);
    }
  }

  function onCancel(event) {
    pointers.delete(event.pointerId);
    layer.style.opacity = "";
    begin();
    paint();
  }

  function onWheel(event) {
    // The trackpad's pinch comes as the wheel with Ctrl; the wheel itself moves a zoomed view.
    if (event.ctrlKey) {
      event.preventDefault();
      const point = local(event);
      zoomTo(view.scale * Math.exp(-event.deltaY / 200), point.x, point.y);
    } else if (view.scale > MIN_SCALE) {
      event.preventDefault();
      show({ scale: view.scale, x: view.x - event.deltaX, y: view.y - event.deltaY });
    }
  }

  // A pinch on a page at its fitted size reaches the page, which tells it here (its frame fills the stage, so its
  // coordinates are the stage's); zoomed, the glass takes the gestures.
  let pinchedFrom = view;
  if (page) {
    content.addEventListener(ZOOM_EVENT, (event) => {
      const { phase, ratio, x, y } = /** @type {CustomEvent} */ (event).detail;
      if (phase === "start") {
        pinchedFrom = view;
      } else if (phase === "move") {
        // The frame measures the fingers in its own coordinates, which shrink as the view grows (`pinchedScale`); the
        // point between them at the start is the stage's, the frame having filled it then.
        show(zoomedAbout(pinchedFrom, pinchedScale(ratio, view.scale), x, y));
      } else if (phase === "step") {
        zoomTo(view.scale * ratio, x, y);
      }
    });
  }

  // The gestures are taken where they can be: on a picture's whole stage, and on a zoomed page's glass.
  const surface = glass ?? stage;
  surface.addEventListener("pointerdown", onDown);
  surface.addEventListener("pointermove", onMove);
  surface.addEventListener("pointerup", onUp);
  surface.addEventListener("pointercancel", onCancel);
  stage.addEventListener("wheel", onWheel, { passive: false });
  out.addEventListener("click", () => {
    const at = middle();
    zoomTo(view.scale / STEP, at.x, at.y);
  });
  into.addEventListener("click", () => {
    const at = middle();
    zoomTo(view.scale * STEP, at.x, at.y);
  });
  fit.addEventListener("click", () => show({ scale: MIN_SCALE, x: 0, y: 0 }));
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "+" || event.key === "=") {
      into.click();
    } else if (event.key === "-") {
      out.click();
    } else if (event.key === "0") {
      fit.click();
    }
  });
  // A turn of the phone or a resize keeps the view within the content.
  const resized = () => show(view);
  window.addEventListener("resize", resized);
  // Escape closes the viewer before what is behind it: a panel a picture was opened from answers Escape too, and would
  // take the key first, leaving the viewer over what it showed.
  const onEscape = (event) => {
    if (event.key === "Escape" && dialog.open) {
      event.preventDefault();
      event.stopPropagation();
      dialog.close();
    }
  };
  window.addEventListener("keydown", onEscape, true);
  dialog.addEventListener("close", () => {
    window.removeEventListener("resize", resized);
    window.removeEventListener("keydown", onEscape, true);
    dialog.remove();
    onClose();
  });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
  paint();
  return dialog;
}
