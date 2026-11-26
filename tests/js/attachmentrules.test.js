import assert from "node:assert/strict";
import { test } from "node:test";

import { goesStreamed, isMedia, limitOf } from "../../src/dotagent/server/static/js/attachments.js";

const file = (name, type, size) => ({ name, type, size });

test("a video or a sound is told by its type or its name, as the daemon tells one", () => {
  assert.equal(isMedia(file("clip.mp4", "video/mp4", 1)), true);
  assert.equal(isMedia(file("IMG_0042.MOV", "", 1)), true);
  assert.equal(isMedia(file("memo", "audio/x-m4a", 1)), true);
  assert.equal(isMedia(file("Audio Message.caf", "application/octet-stream", 1)), true);
  assert.equal(isMedia(file("notes.pdf", "application/pdf", 1)), false);
});

test("a video may be 200 MiB, any other file 20 MiB", () => {
  assert.equal(limitOf(file("a.mp4", "video/mp4", 1)), 200 * 1024 * 1024);
  assert.equal(limitOf(file("a.pdf", "application/pdf", 1)), 20 * 1024 * 1024);
});

test("a video or a file past a few megabytes goes as the raw body, a small document as base64", () => {
  assert.equal(goesStreamed(file("a.mp4", "video/mp4", 10)), true);
  assert.equal(goesStreamed(file("a.pdf", "application/pdf", 5 * 1024 * 1024)), true);
  assert.equal(goesStreamed(file("a.txt", "text/plain", 1024)), false);
});
