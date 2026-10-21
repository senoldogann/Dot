// The words of a rule's try-out on past steps (item 469).

import assert from "node:assert/strict";
import { test } from "node:test";

import { outcomeLabel, previewLines } from "../../src/dotagent/server/static/js/rulepreview.js";

const VIEW = {
  scanned: 3200,
  since: null,
  partial: false,
  fits: 18,
  shadowed_by: [{ rule_id: "rul_1", description: "Do it", steps: 9 }],
  in_yolo: 0,
  before_rules: 0,
  would_decide: 9,
  stricter: 3,
  looser: 0,
  same: 1,
  stopped: 2,
  unknown: 3,
  examples: [],
  problem: null,
};

test("what a rule would have done, said in lines", () => {
  assert.deepEqual(previewLines(VIEW, 30), [
    "Would have matched 18 of 3200 steps in the last 30 days.",
    "An earlier rule decides some of them first: Do it (9).",
    "It would decide 9 of them: 3 would ask more than they did, 1 the same, 2 did not run then, 3 from before Dot kept its decisions.",
  ]);
});

test("what no rule decides is said apart: a YOLO run's steps, and the checks before every rule", () => {
  const view = { ...VIEW, fits: 6, shadowed_by: [], in_yolo: 4, before_rules: 1, would_decide: 1, stricter: 0, same: 0, stopped: 0, unknown: 0, looser: 1 };
  assert.deepEqual(previewLines(view, 30), [
    "Would have matched 6 of 3200 steps in the last 30 days.",
    "4 ran in YOLO, which asks nothing whatever the rules say.",
    "1 is settled before any rule, by checks no rule changes, such as money, credentials, messages and posts, and Dot's own files.",
    "It would decide 1 of them: 1 would ask less.",
  ]);
});

test("nothing that fits, and nothing read, say so", () => {
  assert.deepEqual(previewLines({ ...VIEW, fits: 0, would_decide: 0, shadowed_by: [] }, 30), [
    "No step of 3200 steps in the last 30 days fits. The rule is narrow, or the pattern is off; only steps still in the database count.",
  ]);
  assert.deepEqual(previewLines({ ...VIEW, scanned: 0 }, 1), ["No step in the last 1 day reached the policy."]);
});

test("a try-out that read the newest steps only says how far back it got", () => {
  const since = new Date(2026, 9, 2, 12).getTime() / 1000;
  const [first] = previewLines({ ...VIEW, partial: true, since, scanned: 20000 }, 30);
  assert.match(first, /^Would have matched 18 of the newest 20000 steps \(since .+\)\.$/);
});

test("what happened to a past step", () => {
  assert.equal(outcomeLabel(null, true, false), "ran");
  assert.equal(outcomeLabel(null, false, false), "failed");
  assert.equal(outcomeLabel(null, false, true), "did not run");
  assert.equal(outcomeLabel({ answered_by: "policy" }, false, true), "failed");
  assert.equal(outcomeLabel({ answered_by: "user" }, true, true), "you approved it");
  assert.equal(outcomeLabel({ answered_by: "reviewer" }, true, true), "the review model approved it");
  assert.equal(outcomeLabel({ answered_by: "yolo" }, true, true), "ran, YOLO");
});
