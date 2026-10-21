// Using a held run's page from the screen that shows it (item 475): a layer over the picture of the page that turns a
// finger's taps, drags and presses, a mouse's clicks and wheel, and the keyboard into input for the page
// (`POST /runs/{id}/input`, item 470), sent in order, one batch at a time. Two fingers zoom the picture itself, so that
// a page drawn 1280 pixels wide can be read and tapped on a phone. What is typed goes to the page only.

import { api } from "./api.js";
import { el } from "./dom.js";
import { LONG_PRESS_MS, drawnOf, fractionOf, gestureOf, isDoubleTap, keyName, pinched, scrollFor } from "./pointer.js";

// How long input waits to go with the next (moves and scrolls; a press, a release and a key go at once), how many
// events a batch carries at most (the daemon takes 64), and how much text one event carries (it takes 500).
const FLUSH_MS = 50;
const BATCH_EVENTS = 64;
const TEXT_CHARS = 500;
// How often a mouse's moves without a button go, at most.
const HOVER_MS = 50;
const BUTTONS = { 0: "left", 2: "right" };

/**
 * The control of the page shown by `image` inside `screen`, for the run `runId` the reader holds. `onRefused(error)` is
 * told when the daemon refused the input (the run was handed back elsewhere, a step of it is still going): what was
 * waiting to go is dropped then. Returns `keyboard`, the button that brings the screen's keyboard up for the page,
 * and `stop()`, which ends the control and puts the picture back as it was.
 */
export function createControl({ runId, screen, image, onRefused }) {
  const queue = [];
  let sending = false;
  let timer = null;
  let stopped = false;
  // The page's own size in CSS pixels, from the daemon's last answer; the picture's own until then.
  let page = null;
  let zoom = { scale: 1, x: 0, y: 0 };
  // The fingers on the picture: where each began and last was, when it began, and what its touch has come to.
  const touches = new Map();
  let pinch = null;
  let lastTap = null;
  let lastHover = 0;

  const natural = () => ({ width: image.naturalWidth, height: image.naturalHeight });
  const pointOf = (event) => ({ x: event.clientX, y: event.clientY });
  const fractionAt = (point) => fractionOf(point, image.getBoundingClientRect(), natural());

  function send(events, now) {
    if (stopped || events.length === 0) {
      return;
    }
    queue.push(...events);
    if (now) {
      flush();
    } else if (timer === null) {
      timer = setTimeout(() => {
        timer = null;
        flush();
      }, FLUSH_MS);
    }
  }

  async function flush() {
    if (sending || stopped || queue.length === 0) {
      return;
    }
    sending = true;
    const batch = queue.splice(0, BATCH_EVENTS);
    try {
      const taken = await api.post(`/runs/${encodeURIComponent(runId)}/input`, { events: batch });
      page = { width: taken.width, height: taken.height };
    } catch (error) {
      queue.length = 0;
      onRefused(error);
    } finally {
      sending = false;
    }
    flush();
  }

  /** The events of a button at `at` (fractions): a press and a release, or one of them. */
  const press = (kind, at, button, clicks) => ({ kind, x: at.x, y: at.y, button, clicks });
  const move = (at) => ({ kind: "move", x: at.x, y: at.y });

  function applyZoom() {
    image.style.setProperty("--zoom-scale", String(zoom.scale));
    image.style.setProperty("--zoom-x", `${zoom.x}px`);
    image.style.setProperty("--zoom-y", `${zoom.y}px`);
  }

  // A mouse or a trackpad: its buttons and moves go as they are, its moves without a button now and then.
  function mouseDown(event) {
    const at = fractionAt(pointOf(event));
    const button = BUTTONS[event.button];
    if (at === null || button === undefined) {
      return;
    }
    send([move(at), press("down", at, button, Math.min(Math.max(event.detail, 1), 3))], true);
  }

  function mouseMove(event) {
    const at = fractionAt(pointOf(event));
    if (at === null) {
      return;
    }
    if (event.buttons === 0 && event.timeStamp - lastHover < HOVER_MS) {
      return;
    }
    lastHover = event.timeStamp;
    send([move(at)], false);
  }

  function mouseUp(event) {
    const at = fractionAt(pointOf(event));
    const button = BUTTONS[event.button];
    if (at === null || button === undefined) {
      return;
    }
    send([press("up", at, button, Math.min(Math.max(event.detail, 1), 3))], true);
  }

  // A finger: a tap clicks, a drag scrolls the page, a press held still drags on it; two fingers zoom the picture.
  function touchDown(event) {
    const point = pointOf(event);
    if (touches.size === 1 && pinch === null) {
      const [[otherId, other]] = touches;
      cancelTouch(other);
      other.gesture = "pinch";
      pinch = { ids: [otherId, event.pointerId], points: new Map([[otherId, other.last], [event.pointerId, point]]) };
      touches.set(event.pointerId, { start: point, last: point, at: event.timeStamp, gesture: "pinch", timer: null });
      return;
    }
    if (touches.size > 1) {
      return;
    }
    const touch = { start: point, last: point, at: event.timeStamp, gesture: "tap", timer: null };
    touch.timer = setTimeout(() => becomePress(touch), LONG_PRESS_MS);
    touches.set(event.pointerId, touch);
  }

  function becomePress(touch) {
    touch.timer = null;
    if (touch.gesture !== "tap") {
      return;
    }
    const at = fractionAt(touch.start);
    if (at === null) {
      touch.gesture = "off";
      return;
    }
    touch.gesture = "press";
    send([move(at), press("down", at, "left", 1)], true);
  }

  function cancelTouch(touch) {
    if (touch.timer !== null) {
      clearTimeout(touch.timer);
      touch.timer = null;
    }
    if (touch.gesture === "press") {
      const at = fractionAt(touch.last) ?? fractionAt(touch.start);
      if (at !== null) {
        send([press("up", at, "left", 1)], true);
      }
    }
  }

  function touchMove(event) {
    const touch = touches.get(event.pointerId);
    if (touch === undefined) {
      return;
    }
    const point = pointOf(event);
    if (pinch !== null && pinch.points.has(event.pointerId)) {
      const [a, b] = pinch.ids;
      const from = { a: pinch.points.get(a), b: pinch.points.get(b) };
      pinch.points.set(event.pointerId, point);
      const to = { a: pinch.points.get(a), b: pinch.points.get(b) };
      const box = image.getBoundingClientRect();
      zoom = pinched(zoom, from, to, { x: box.left - zoom.x, y: box.top - zoom.y });
      applyZoom();
      return;
    }
    const moved = Math.hypot(point.x - touch.start.x, point.y - touch.start.y);
    if (touch.gesture === "tap") {
      touch.gesture = gestureOf(moved, event.timeStamp - touch.at, false);
      if (touch.gesture === "scroll" && touch.timer !== null) {
        clearTimeout(touch.timer);
        touch.timer = null;
      }
    }
    if (touch.gesture === "scroll") {
      const at = fractionAt(point) ?? fractionAt(touch.start);
      const drawn = drawnOf(image.getBoundingClientRect(), natural());
      const scrolled = scrollFor(point.x - touch.last.x, point.y - touch.last.y, drawn, page ?? natural());
      if (at !== null && (scrolled.dx !== 0 || scrolled.dy !== 0)) {
        send([{ kind: "wheel", x: at.x, y: at.y, dx: clampWheel(scrolled.dx), dy: clampWheel(scrolled.dy) }], false);
      }
    } else if (touch.gesture === "press") {
      const at = fractionAt(point);
      if (at !== null) {
        send([move(at)], false);
      }
    }
    touch.last = point;
  }

  function touchUp(event) {
    const touch = touches.get(event.pointerId);
    if (touch === undefined) {
      return;
    }
    touches.delete(event.pointerId);
    if (touch.timer !== null) {
      clearTimeout(touch.timer);
    }
    if (pinch !== null && pinch.points.has(event.pointerId)) {
      // The other finger stays out of it until it lifts too.
      pinch = null;
      return;
    }
    const point = pointOf(event);
    if (touch.gesture === "tap") {
      const at = fractionAt(point);
      if (at === null) {
        return;
      }
      const tap = { x: point.x, y: point.y, at: event.timeStamp };
      const clicks = isDoubleTap(lastTap, tap) ? 2 : 1;
      lastTap = clicks === 2 ? null : tap;
      send([move(at), press("down", at, "left", clicks), press("up", at, "left", clicks)], true);
    } else if (touch.gesture === "press") {
      const at = fractionAt(point) ?? fractionAt(touch.last);
      if (at !== null) {
        send([press("up", at, "left", 1)], true);
      }
    } else if (touch.gesture === "scroll") {
      flush();
    }
  }

  function touchCancel(event) {
    const touch = touches.get(event.pointerId);
    if (touch !== undefined) {
      touches.delete(event.pointerId);
      cancelTouch(touch);
    }
    if (pinch !== null && pinch.points.has(event.pointerId)) {
      pinch = null;
    }
  }

  const onDown = (event) => {
    event.preventDefault();
    image.setPointerCapture(event.pointerId);
    (event.pointerType === "mouse" ? mouseDown : touchDown)(event);
  };
  const onMove = (event) => (event.pointerType === "mouse" ? mouseMove : touchMove)(event);
  const onUp = (event) => (event.pointerType === "mouse" ? mouseUp : touchUp)(event);
  const onCancel = (event) => (event.pointerType === "mouse" ? undefined : touchCancel(event));
  const onWheel = (event) => {
    event.preventDefault();
    const at = fractionAt(pointOf(event));
    if (at === null) {
      return;
    }
    // A wheel that counts in lines or pages is turned into pixels, as a page would scroll by them.
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? (page ?? natural()).height : 1;
    send([{ kind: "wheel", x: at.x, y: at.y, dx: clampWheel(event.deltaX * unit), dy: clampWheel(event.deltaY * unit) }], false);
  };
  const onMenu = (event) => event.preventDefault();

  // The screen's keyboard: a field kept out of sight takes what is typed and hands it on; it is 16 px high, so that a
  // phone does not zoom into it.
  const field = el("textarea", { class: "control-keys", autocapitalize: "off", autocomplete: "off", spellcheck: "false", "aria-label": "Keys for the page" });
  const keyboard = el("button", { class: "btn sm", type: "button", onclick: () => field.focus() }, "Keyboard");
  const onKey = (event) => {
    const name = keyName(event);
    if (name !== null) {
      event.preventDefault();
      send([{ kind: "key", key: name }], true);
    }
  };
  const onInput = (event) => {
    const text = event.inputType === "insertFromPaste" ? (event.dataTransfer?.getData("text/plain") ?? event.data) : event.data;
    const typed = {
      insertText: text,
      insertReplacementText: text,
      insertFromPaste: text,
    }[event.inputType];
    const key = { deleteContentBackward: "Backspace", deleteContentForward: "Delete", insertLineBreak: "Enter", insertParagraph: "Enter" }[event.inputType];
    if (typed === undefined && key === undefined) {
      return;
    }
    event.preventDefault();
    if (key !== undefined) {
      send([{ kind: "key", key }], true);
    } else if (typeof typed === "string" && typed !== "") {
      sendText(typed);
    }
  };
  // What a keyboard composes (accents, other scripts) comes once it is composed.
  const onComposed = (event) => {
    if (typeof event.data === "string" && event.data !== "") {
      sendText(event.data);
    }
    field.value = "";
  };

  function sendText(text) {
    const events = [];
    for (let start = 0; start < text.length; start += TEXT_CHARS) {
      events.push({ kind: "text", text: text.slice(start, start + TEXT_CHARS) });
    }
    send(events, true);
  }

  image.addEventListener("pointerdown", onDown);
  image.addEventListener("pointermove", onMove);
  image.addEventListener("pointerup", onUp);
  image.addEventListener("pointercancel", onCancel);
  image.addEventListener("wheel", onWheel, { passive: false });
  image.addEventListener("contextmenu", onMenu);
  field.addEventListener("keydown", onKey);
  field.addEventListener("beforeinput", onInput);
  field.addEventListener("compositionend", onComposed);
  screen.append(field);
  screen.dataset.controlling = "";

  return {
    keyboard,
    stop() {
      stopped = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      for (const touch of touches.values()) {
        if (touch.timer !== null) {
          clearTimeout(touch.timer);
        }
      }
      touches.clear();
      image.removeEventListener("pointerdown", onDown);
      image.removeEventListener("pointermove", onMove);
      image.removeEventListener("pointerup", onUp);
      image.removeEventListener("pointercancel", onCancel);
      image.removeEventListener("wheel", onWheel);
      image.removeEventListener("contextmenu", onMenu);
      field.remove();
      keyboard.remove();
      zoom = { scale: 1, x: 0, y: 0 };
      for (const property of ["--zoom-scale", "--zoom-x", "--zoom-y"]) {
        image.style.removeProperty(property);
      }
      delete screen.dataset.controlling;
    },
  };
}

/** A wheel's move as the daemon takes it, at most 4000 pixels either way. */
function clampWheel(value) {
  return Math.max(-4000, Math.min(4000, value));
}
