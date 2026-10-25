// Whether the page is away (item 662; not the Away card of away.js): its window is not the one in front, or it is out
// of sight. What repeats without end stands still then (the style sheets' `--endless`, main.js marks the page) and the
// moving faces stop drawing (character/ticker.js), as nobody watches them and they would be drawn at the screen's rate
// all the same. Focus that moves into a frame of the page (a page Dot shows, a preview) is still the page's: `blur` is
// read again once the focus has settled, when `document.hasFocus()` counts the frame.

// How often the page looks again whether it is away.
const LOOK_AGAIN_MS = 2000;
const listeners = new Set();

function measure() {
  return document.hidden || !document.hasFocus();
}

let away = measure();

function update() {
  const now = measure();
  if (now === away) {
    return;
  }
  away = now;
  for (const listener of listeners) {
    listener(away);
  }
}

window.addEventListener("focus", update);
window.addEventListener("blur", () => setTimeout(update, 0));
document.addEventListener("visibilitychange", update);
// Focus in a frame of the page tells the page nothing, either way: the owner going to another app, or coming back to
// the frame, is heard by the frame's window, not the page's. So the page looks again now and then, away or not, and
// the events only make it sooner.
setInterval(update, LOOK_AGAIN_MS);

/** Whether the page is away now. */
export function isAway() {
  return away;
}

/** Call `listener(away)` each time the page goes away or comes back; returns the function that stops it. */
export function onAway(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
