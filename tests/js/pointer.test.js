// Using a held run's page from the screen (item 475): where a touch falls on the picture, what a finger's moves come to,
// and the names of the keys.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DOUBLE_TAP_MS, LONG_PRESS_MS, MAX_ZOOM, TAP_SLOP_PX, fractionOf, gestureOf, isDoubleTap, keyName, pinched, scrollFor } from "../../src/dotagent/server/static/js/pointer.js";

const NATURAL = { width: 1280, height: 860 };

test("a point on the picture is a fraction of the page, the bands of a picture drawn whole are not", () => {
  // Drawn at its own shape: the box is the picture.
  assert.deepEqual(fractionOf({ x: 195, y: 131 }, { left: 0, top: 0, width: 390, height: 262 }, NATURAL), { x: 0.5, y: 0.5 });
  // Drawn whole in a taller box: bands above and below.
  const tall = { left: 0, top: 0, width: 390, height: 600 };
  const middle = fractionOf({ x: 195, y: 300 }, tall, NATURAL);
  assert.ok(middle !== null && Math.abs(middle.x - 0.5) < 1e-9 && Math.abs(middle.y - 0.5) < 1e-9, JSON.stringify(middle));
  assert.equal(fractionOf({ x: 195, y: 20 }, tall, NATURAL), null);
  // A box the screen zoomed is larger and moved, and the fractions stay those of the page.
  const zoomed = { left: -195, top: -131, width: 780, height: 524 };
  assert.deepEqual(fractionOf({ x: 195, y: 131 }, zoomed, NATURAL), { x: 0.5, y: 0.5 });
  assert.equal(fractionOf({ x: 10, y: 10 }, { left: 0, top: 0, width: 390, height: 262 }, { width: 0, height: 0 }), null);
});

test("a touch is a tap, a scroll or a press by how it moved and how long it stayed", () => {
  assert.equal(gestureOf(TAP_SLOP_PX - 1, LONG_PRESS_MS - 1, false), "tap");
  assert.equal(gestureOf(TAP_SLOP_PX + 1, 100, false), "scroll");
  assert.equal(gestureOf(2, LONG_PRESS_MS, false), "press");
  assert.equal(gestureOf(200, 2000, true), "press");
});

test("two taps soon and near are a double tap", () => {
  const first = { x: 100, y: 100, at: 1000 };
  assert.equal(isDoubleTap(null, first), false);
  assert.equal(isDoubleTap(first, { x: 104, y: 98, at: 1000 + DOUBLE_TAP_MS - 10 }), true);
  assert.equal(isDoubleTap(first, { x: 104, y: 98, at: 1000 + DOUBLE_TAP_MS + 10 }), false);
  assert.equal(isDoubleTap(first, { x: 160, y: 100, at: 1100 }), false);
});

test("the page follows the finger, in its own pixels", () => {
  // A finger moving 50 px up a picture 262 px high of a page 860 px high scrolls the page about 164 px down.
  const moved = scrollFor(0, -50, { width: 390, height: 262 }, { width: 1280, height: 860 });
  assert.equal(moved.dx, 0);
  assert.ok(Math.abs(moved.dy - (50 * 860) / 262) < 1e-9, String(moved.dy));
  assert.deepEqual(scrollFor(10, 10, { width: 0, height: 0 }, { width: 1280, height: 860 }), { dx: 0, dy: 0 });
});

test("two fingers zoom the picture around the point between them, within its bounds", () => {
  const origin = { x: 0, y: 0 };
  const start = { scale: 1, x: 0, y: 0 };
  const apart = pinched(start, { a: { x: 100, y: 100 }, b: { x: 200, y: 100 } }, { a: { x: 50, y: 100 }, b: { x: 250, y: 100 } }, origin);
  assert.equal(apart.scale, 2);
  // The point between the fingers, 150 by 100, is the same point of the picture after the zoom.
  assert.equal((150 - apart.x) / apart.scale, 150 / 1);
  const far = pinched(start, { a: { x: 100, y: 100 }, b: { x: 110, y: 100 } }, { a: { x: 0, y: 100 }, b: { x: 400, y: 100 } }, origin);
  assert.equal(far.scale, MAX_ZOOM);
  const back = pinched(apart, { a: { x: 50, y: 100 }, b: { x: 250, y: 100 } }, { a: { x: 140, y: 100 }, b: { x: 160, y: 100 } }, origin);
  assert.deepEqual(back, { scale: 1, x: 0, y: 0 });
});

test("a key is named as Playwright names it, a character typed as it is is not", () => {
  const plain = { key: "a", ctrlKey: false, altKey: false, metaKey: false, shiftKey: false };
  assert.equal(keyName(plain), null);
  assert.equal(keyName({ ...plain, key: "A", shiftKey: true }), null);
  assert.equal(keyName({ ...plain, metaKey: true }), "Meta+a");
  assert.equal(keyName({ ...plain, key: "Tab", shiftKey: true }), "Shift+Tab");
  assert.equal(keyName({ ...plain, key: "ArrowLeft" }), "ArrowLeft");
  assert.equal(keyName({ ...plain, key: "Enter" }), "Enter");
  assert.equal(keyName({ ...plain, key: "Unidentified" }), null);
  assert.equal(keyName({ ...plain, key: "Shift", shiftKey: true }), null);
  assert.equal(keyName({ ...plain, key: " " }), null);
});
