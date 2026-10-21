// The words of "while you were away" (item 466).

import assert from "node:assert/strict";
import { test } from "node:test";

import { awayLine, decidedBy } from "../../src/dotagent/server/static/js/awaywords.js";

const NOTHING = {
  done: 0,
  failed: 0,
  cancelled: 0,
  working: 0,
  campaign_sessions: 0,
  decisions: { reviewer_approved: 0, reviewer_denied: 0, yolo_approved: 0, yolo_denied: 0 },
  drafts: 0,
};

test("nothing that happened says nothing", () => {
  assert.equal(awayLine(NOTHING), "");
});

test("one run is one, several are several, and only what happened is said", () => {
  assert.equal(awayLine({ ...NOTHING, done: 1 }), "1 scheduled run: 1 done");
  assert.equal(awayLine({ ...NOTHING, done: 5, failed: 1 }), "6 scheduled runs: 5 done, 1 failed");
  assert.equal(awayLine({ ...NOTHING, cancelled: 1, working: 2 }), "3 scheduled runs: 1 stopped, 2 working");
});

test("campaign sessions, decisions made for the owner and drafts join the line", () => {
  const view = {
    ...NOTHING,
    done: 2,
    campaign_sessions: 1,
    decisions: { reviewer_approved: 4, reviewer_denied: 1, yolo_approved: 9, yolo_denied: 0 },
    drafts: 2,
  };
  assert.equal(
    awayLine(view),
    "2 scheduled runs: 2 done · 1 campaign session · 14 decisions made for you (9 by YOLO, 5 by the review model; 1 denied) · 2 drafts wait for you",
  );
  assert.equal(
    awayLine({ ...NOTHING, decisions: { ...NOTHING.decisions, yolo_approved: 1 }, drafts: 1 }),
    "1 decision made for you (1 by YOLO) · 1 draft waits for you",
  );
});

test("who decided says only who did", () => {
  assert.equal(decidedBy(0, 3, 0), "3 by the review model");
  assert.equal(decidedBy(2, 0, 2), "2 by YOLO; 2 denied");
});
