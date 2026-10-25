// One requestAnimationFrame loop for every moving character on the page. It runs only while something is
// subscribed and the page is not away (its window in front and in sight, item 662), and hands each subscriber the
// seconds since the last frame, clamped at 64 ms so a page that comes back resumes without jumping ahead.

import { isAway, onAway } from "../infront.js";

const subscribers = new Set();
let handle = 0;
let last = 0;

function frame(ms) {
  handle = 0;
  const dt = last ? Math.min((ms - last) / 1000, 0.064) : 0;
  last = ms;
  for (const tick of subscribers) tick(dt);
  if (subscribers.size && !isAway()) handle = requestAnimationFrame(frame);
}

function start() {
  if (handle || !subscribers.size || isAway()) return;
  last = 0;
  handle = requestAnimationFrame(frame);
}

function stop() {
  if (handle) cancelAnimationFrame(handle);
  handle = 0;
}

onAway((away) => {
  if (away) stop();
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
