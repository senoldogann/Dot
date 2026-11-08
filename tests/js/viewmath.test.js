// The arithmetic of the full-screen viewer's zoom (item 1056).

import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_SCALE, clamped, pinchedScale, zoomedAbout } from "../../src/dotagent/server/static/js/viewmath.js";

test("a zoom about a point keeps that point where it is on the screen", () => {
  const view = zoomedAbout({ scale: 1, x: 0, y: 0 }, 2.5, 100, 200);
  // The content's point under (100, 200) before is (100, 200); after, it is drawn at x + 100 * scale.
  assert.equal(view.x + 100 * view.scale, 100);
  assert.equal(view.y + 200 * view.scale, 200);
});

test("the view stays within the content, and the scale within its bounds", () => {
  assert.deepEqual(clamped({ scale: 2, x: 50, y: -900 }, 400, 800), { scale: 2, x: 0, y: -800 });
  assert.deepEqual(clamped({ scale: 9, x: 0, y: 0 }, 400, 800), { scale: MAX_SCALE, x: 0, y: 0 });
  assert.deepEqual(clamped({ scale: 0.5, x: -30, y: -30 }, 400, 800), { scale: 1, x: 0, y: 0 });
});

test("a pinch on a page follows the fingers, though the page measures them in a frame the view scales", () => {
  // The fingers spread 4 times from a fitted view; the frame, scaled to 2 by the moves before, reads them 2 times apart.
  let scale = 1;
  for (const spread of [1.5, 2, 3, 4]) {
    scale = pinchedScale(spread / scale, scale);
  }
  assert.equal(scale, 4);
  assert.equal(pinchedScale(10, 1), MAX_SCALE);
});
