// The chat a page opens at from the last chat screen (item 810): its first read that finds nothing is the opening's, and
// none after a read has found it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { openedAtLastChat, openingOf, tookOpening } from "../../src/dotagent/server/static/js/prefs.js";

test("the page that opened at a chat takes the mark once, and no other page has it", () => {
  openedAtLastChat("/runs/run_a");
  assert.equal(tookOpening("/runs/run_b"), false);
  assert.equal(tookOpening("/runs/run_a"), true);
  assert.equal(tookOpening("/runs/run_a"), false);
});

test("a read that finds nothing is the opening's until one has found the chat", () => {
  openedAtLastChat("/runs/run_c");
  const opening = openingOf("/runs/run_c");
  assert.equal(opening.gone(), true);
  // A read that failed otherwise (no network) decides nothing: the next read is the opening's still.
  assert.equal(opening.gone(), true);
  opening.found();
  // Deleted since and read again (a Reload): the owner sees Task not found.
  assert.equal(opening.gone(), false);
});

test("a page that did not open at the chat never takes its reads for the opening's", () => {
  openedAtLastChat("/runs/run_d");
  assert.equal(openingOf("/runs/run_e").gone(), false);
  // The mark of the chat the page opened at waits for that chat's page.
  assert.equal(openingOf("/runs/run_d").gone(), true);
  assert.equal(openingOf("/runs/run_d").gone(), false);
});
