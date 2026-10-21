// Using a held run's page from the screen that shows it (item 475): where a touch or a click falls on the picture of the
// page, as fractions of it (`POST /runs/{id}/input`, item 470), and what a finger's moves come to. Pure, so that it is
// read alone (tests/js/pointer.test.js).

/** How far a finger may move and still tap, in CSS pixels of the screen. */
export const TAP_SLOP_PX = 8;
/** How long a finger stays still before its touch is a press, which a drag follows (a slider, a selection). */
export const LONG_PRESS_MS = 450;
/** Two taps this close in time, and near each other, are a double tap. */
export const DOUBLE_TAP_MS = 300;
/** How far the picture can be zoomed with two fingers, at most. */
export const MAX_ZOOM = 4;

/**
 * The place of `point` on the picture, as fractions of the page it shows: `rect` is where the image is drawn on the
 * screen (its box as `getBoundingClientRect` gives it, with a zoom of the screen's own in it), `natural` the picture's
 * own size. A picture drawn whole inside a box of another shape (`object-fit: contain`) leaves bands that are not the
 * page. Null for a point off the page, and before the picture has a size.
 */
export function fractionOf(point, rect, natural) {
  const page = drawnOf(rect, natural);
  if (page.width <= 0 || page.height <= 0) {
    return null;
  }
  const x = (point.x - page.left) / page.width;
  const y = (point.y - page.top) / page.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) {
    return null;
  }
  return { x, y };
}

/** The place of `point` on the picture as `fractionOf` gives it, held to the page's edges: where a button pressed on
 * the page is let go when the finger or the mouse left the picture. Null only before the picture has a size. */
export function clampedFractionOf(point, rect, natural) {
  const page = drawnOf(rect, natural);
  if (page.width <= 0 || page.height <= 0) {
    return null;
  }
  const clamp = (value) => Math.min(1, Math.max(0, value));
  return { x: clamp((point.x - page.left) / page.width), y: clamp((point.y - page.top) / page.height) };
}

/** Where the page itself is drawn in the image's box `rect` (`{ left, top, width, height }`), the picture being
 * `natural` large and drawn whole: the box less the bands around it; nothing before the picture has a size. */
export function drawnOf(rect, natural) {
  if (natural.width <= 0 || natural.height <= 0 || rect.width <= 0 || rect.height <= 0) {
    return { left: rect.left, top: rect.top, width: 0, height: 0 };
  }
  const scale = Math.min(rect.width / natural.width, rect.height / natural.height);
  const width = natural.width * scale;
  const height = natural.height * scale;
  return { left: rect.left + (rect.width - width) / 2, top: rect.top + (rect.height - height) / 2, width, height };
}

/**
 * What a finger's touch has come to so far: still a `tap`; a `scroll` once it moved further than a tap may before a
 * press came; a `press` once it stayed still long enough, or once it is one already.
 */
export function gestureOf(moved, elapsedMs, pressed) {
  if (pressed) {
    return "press";
  }
  if (moved > TAP_SLOP_PX) {
    return "scroll";
  }
  return elapsedMs >= LONG_PRESS_MS ? "press" : "tap";
}

/** Whether `tap` is the second of a double tap: soon after the `previous` one, and near it. Taps are `{ x, y, at }`. */
export function isDoubleTap(previous, tap) {
  return previous !== null && tap.at - previous.at <= DOUBLE_TAP_MS && Math.hypot(tap.x - previous.x, tap.y - previous.y) <= TAP_SLOP_PX * 2;
}

/**
 * How far the page scrolls, in its CSS pixels, for a finger's move of `dx` and `dy` screen pixels over a picture drawn
 * `drawn` large of a page `page` large: the page follows the finger, so a finger moving up scrolls the page down.
 */
export function scrollFor(dx, dy, drawn, page) {
  // `|| 0` writes a move of nothing as 0, not -0.
  return { dx: drawn.width > 0 ? (-dx * page.width) / drawn.width || 0 : 0, dy: drawn.height > 0 ? (-dy * page.height) / drawn.height || 0 : 0 };
}

/**
 * The zoom of the picture after two fingers moved from `from` to `to` (each `{ a, b }`, two points): it grows as the
 * fingers part, within 1 and MAX_ZOOM, and the point between them stays under them. `zoom` is `{ scale, x, y }`, the
 * picture drawn `scale` times as large and moved by `x` and `y` screen pixels; `origin` is the picture's top left
 * corner on the screen before any zoom.
 */
export function pinched(zoom, from, to, origin) {
  const before = Math.hypot(from.a.x - from.b.x, from.a.y - from.b.y);
  const after = Math.hypot(to.a.x - to.b.x, to.a.y - to.b.y);
  if (before === 0) {
    return zoom;
  }
  const scale = Math.min(MAX_ZOOM, Math.max(1, zoom.scale * (after / before)));
  if (scale === 1) {
    return { scale: 1, x: 0, y: 0 };
  }
  const middleBefore = { x: (from.a.x + from.b.x) / 2, y: (from.a.y + from.b.y) / 2 };
  const middleAfter = { x: (to.a.x + to.b.x) / 2, y: (to.a.y + to.b.y) / 2 };
  // The picture's own point under the fingers before the move, kept under them after it.
  const pictureX = (middleBefore.x - origin.x - zoom.x) / zoom.scale;
  const pictureY = (middleBefore.y - origin.y - zoom.y) / zoom.scale;
  return { scale, x: middleAfter.x - origin.x - pictureX * scale, y: middleAfter.y - origin.y - pictureY * scale };
}

// A key or a chord as the daemon takes it (browser/remote_input.py, KEY_PATTERN): up to four modifiers, then one printable
// ASCII character or a named key.
const KEY = /^(?:(?:Shift|Control|Alt|Meta|ControlOrMeta)\+){0,4}(?:[!-~]|[A-Z][A-Za-z0-9]{1,15})$/;

/**
 * The name Playwright gives a key the screen's keyboard pressed, with its modifiers (`Meta+a`, `Shift+Tab`,
 * `Alt+ArrowLeft`), or null for one that types a character: that comes as text. A character typed with Option or Alt
 * alone, or with Control and Alt together (AltGr), is a character of the keyboard's layout (`@` on a Turkish Mac), so
 * text; a chord the daemon could not take (a letter outside ASCII) is null too. `event` has `key` and the modifier
 * flags of a KeyboardEvent.
 */
export function keyName(event) {
  if (event.key === "Unidentified" || event.key === "Dead" || event.key === "Process" || ["Shift", "Control", "Alt", "Meta"].includes(event.key)) {
    return null;
  }
  // A space is text; with a modifier it is the Mac's own (Command-Space searches it), not the page's.
  if (event.key === " ") {
    return null;
  }
  const printable = [...event.key].length === 1;
  if (printable && !event.metaKey && (!event.ctrlKey || event.altKey)) {
    return null;
  }
  const modifiers = [event.ctrlKey ? "Control" : null, event.altKey ? "Alt" : null, event.metaKey ? "Meta" : null, event.shiftKey && !printable ? "Shift" : null].filter((part) => part !== null);
  const name = [...modifiers, event.key].join("+");
  return KEY.test(name) ? name : null;
}

/** `text` in pieces of at most `size` characters as the daemon counts them, so that no piece ends in the middle of one
 * (the two halves of an emoji). */
export function textPieces(text, size) {
  const characters = [...text];
  const pieces = [];
  for (let start = 0; start < characters.length; start += size) {
    pieces.push(characters.slice(start, start + size).join(""));
  }
  return pieces;
}
