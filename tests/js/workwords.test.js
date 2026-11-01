// The words of the work orders on the Agents page (item 907).

import assert from "node:assert/strict";
import { test } from "node:test";

import { attemptWords, dueWords, isOpen, span, stateOf } from "../../src/dotagent/server/static/js/workwords.js";

const NOW = 1_000_000;

test("a status is said in plain words, an unknown one as it is", () => {
  assert.equal(stateOf("in_review").label, "Waiting for Dot's check");
  assert.equal(stateOf("escalated").label, "Waiting for you");
  assert.equal(stateOf("something else").label, "something else");
});

test("only an accepted or a cancelled order is over", () => {
  assert.deepEqual(
    ["assigned", "working", "in_review", "returned", "escalated"].map(isOpen),
    [true, true, true, true, true],
  );
  assert.equal(isOpen("accepted"), false);
  assert.equal(isOpen("cancelled"), false);
});

test("a span is said in its biggest unit", () => {
  assert.equal(span(5 * 60), "5 min");
  assert.equal(span(3 * 3600 + 20), "3 h");
  assert.equal(span(86_400), "1 day");
  assert.equal(span(3 * 86_400), "3 days");
  assert.equal(span(-10), "0 min");
});

test("an open order is due in a while or late; one with no time or one that is over says nothing", () => {
  assert.equal(dueWords({ due_at: NOW + 7200, status: "working" }, NOW), "due in 2 h");
  assert.equal(dueWords({ due_at: NOW - 1800, status: "working" }, NOW), "30 min late");
  assert.equal(dueWords({ due_at: null, status: "working" }, NOW), "");
  assert.equal(dueWords({ due_at: NOW - 1800, status: "accepted" }, NOW), "");
});

test("the tries are counted from the first", () => {
  assert.equal(attemptWords(0), "");
  assert.equal(attemptWords(1), "1 try");
  assert.equal(attemptWords(3), "3 tries");
});
