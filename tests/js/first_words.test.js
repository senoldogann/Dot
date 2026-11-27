// Dot's first words in a chat (item 2018): the fast model's line as it came, or the words said at once when it has none
// (workstatus.js firstWordsPhrase).

import assert from "node:assert/strict";
import { test } from "node:test";

import { firstWordsPhrase } from "../../src/dotagent/server/static/js/workstatus.js";

test("the words said at once stand until the fast model's line takes their place", () => {
  assert.equal(firstWordsPhrase(""), "Looking into it…");
  assert.equal(firstWordsPhrase("  "), "Looking into it…");
  assert.equal(firstWordsPhrase(" Looking at the form first. "), "Looking at the form first.");
});
