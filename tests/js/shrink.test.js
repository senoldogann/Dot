// The size a large picture is brought to before it is uploaded (item 990), and the name it goes by.

import assert from "node:assert/strict";
import { test } from "node:test";

import { fitWithin, jpegName, SHRINK_EDGE } from "../../src/dotagent/server/static/js/shrink.js";

test("a picture within the edge keeps its size, and a larger one its shape on the edge", () => {
  assert.deepEqual(fitWithin(1200, 900, SHRINK_EDGE), { width: 1200, height: 900 });
  assert.deepEqual(fitWithin(3000, 1800, SHRINK_EDGE), { width: 2048, height: 1229 });
  // A phone's screenshot, standing.
  assert.deepEqual(fitWithin(1320, 2868, SHRINK_EDGE), { width: 943, height: 2048 });
  assert.deepEqual(fitWithin(40000, 10, SHRINK_EDGE), { width: 2048, height: 1 });
});

test("a picture sent as a JPEG keeps its name with .jpg", () => {
  assert.equal(jpegName("IMG_3174.png"), "IMG_3174.jpg");
  assert.equal(jpegName("photo.final.webp"), "photo.final.jpg");
  assert.equal(jpegName("noext"), "noext.jpg");
  assert.equal(jpegName(".hidden"), ".hidden.jpg");
});
