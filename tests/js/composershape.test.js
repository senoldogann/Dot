// The composer's shape and the words of its empty box, as DotNext's (item 2015).
import assert from "node:assert/strict";
import { test } from "node:test";

import { COMPACT_CHARS, composerShape, emptyBoxWords } from "../../src/dotagent/server/static/js/composershape.js";

test("short words with nothing added are one row; long words, a line break, a file or a pick open it", () => {
  assert.equal(composerShape("", 0, 0), "compact");
  assert.equal(composerShape("x".repeat(COMPACT_CHARS), 0, 0), "compact");
  assert.equal(composerShape("x".repeat(COMPACT_CHARS + 1), 0, 0), "expanded");
  assert.equal(composerShape("two\nlines", 0, 0), "expanded");
  assert.equal(composerShape("", 1, 0), "expanded");
  assert.equal(composerShape("", 0, 1), "expanded");
});

test("characters are counted as a person counts them, an emoji as one", () => {
  assert.equal(composerShape("😀".repeat(COMPACT_CHARS), 0, 0), "compact");
});

test("the empty box says Message Dot, by the name Dot is called, or Ask and the agent's name", () => {
  assert.equal(emptyBoxWords("Dot", false), "Message Dot");
  assert.equal(emptyBoxWords("Miro", false), "Message Miro");
  assert.equal(emptyBoxWords("Scout", true), "Ask Scout");
});
