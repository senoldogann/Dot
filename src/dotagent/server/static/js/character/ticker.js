// One requestAnimationFrame loop for every moving character on the page. It runs only while something is
// subscribed and the tab is visible, and hands each subscriber the seconds since the last frame, clamped at 64 ms
// so a tab shown again resumes without jumping ahead.

const subscribers = new Set();
let handle = 0;
let last = 0;

function frame(ms) {
  handle = 0;
  const dt = last ? Math.min((ms - last) / 1000, 0.064) : 0;
  last = ms;
  for (const tick of subscribers) tick(dt);
  if (subscribers.size && !document.hidden) handle = requestAnimationFrame(frame);
}

function start() {
  if (handle || !subscribers.size || document.hidden) return;
  last = 0;
  handle = requestAnimationFrame(frame);
}

function stop() {
  if (handle) cancelAnimationFrame(handle);
  handle = 0;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stop();
  else start();
});

export const ticker = {
  add(tick) {
    subscribers.add(tick);
    start();
  },
  remove(tick) {
    subscribers.delete(tick);
    if (!subscribers.size) stop();
  },
  // For the page that reads it: whether a frame is scheduled, and how many characters move.
  get running() {
    return handle !== 0;
  },
  get count() {
    return subscribers.size;
  },
};
