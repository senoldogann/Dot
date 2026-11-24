import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BUDGET_MS,
  FIRST_WAIT_MS,
  GAP_EVENTS,
  isKeptWrite,
  longGap,
  MAX_WAIT_MS,
  nextStep,
  READ_TIMEOUT_MS,
  TAIL_EVENTS,
  tailToAdd,
  TRIES,
  waitAfter,
  worthTryingAgain,
} from "../../src/dotagent/server/static/js/retryrules.js";

test("the numbers are the iPhone app's: 10 s a try, three tries, 0.4 s doubling to 3 s, 20 s in all, 500 events and 400", () => {
  assert.equal(READ_TIMEOUT_MS, 10_000);
  assert.equal(TRIES, 3);
  assert.equal(FIRST_WAIT_MS, 400);
  assert.equal(MAX_WAIT_MS, 3000);
  assert.equal(BUDGET_MS, 20_000);
  assert.equal(GAP_EVENTS, 500);
  assert.equal(TAIL_EVENTS, 400);
});

test("a wait doubles from 0.4 s to 3 s: half of it fixed, the other half chance", () => {
  // The least a wait is is half the grown wait; the most is all of it.
  assert.equal(waitAfter(1, 0), 200);
  assert.equal(waitAfter(1, 0.999999), 400);
  assert.equal(waitAfter(2, 0), 400);
  assert.equal(waitAfter(2, 1), 800);
  assert.equal(waitAfter(3, 0), 800);
  assert.equal(waitAfter(3, 1), 1600);
  assert.equal(waitAfter(4, 1), 3000);
  // It stops growing at 3 s however many tries failed.
  assert.equal(waitAfter(9, 1), 3000);
  assert.equal(waitAfter(9, 0), 1500);
});

test("the wait grows with the tries that failed and stays within its bounds for any chance", () => {
  let before = 0;
  for (let failed = 1; failed <= 6; failed += 1) {
    const least = waitAfter(failed, 0);
    const most = waitAfter(failed, 0.999999);
    assert.ok(least >= before / 2, `${failed}: ${least}`);
    assert.ok(most <= MAX_WAIT_MS && least >= 1, `${failed}: ${least}..${most}`);
    assert.ok(least <= most);
    before = most;
  }
  // Two phones that lost the link together are not told to ask together: chance spreads the wait.
  assert.notEqual(waitAfter(2, 0.1), waitAfter(2, 0.9));
});

test("a read that got no answer, ran out of time, or met a daemon that is busy is tried again", () => {
  for (const status of [0, 408, 429, 500, 502, 503, 504, 599]) {
    assert.equal(worthTryingAgain({ status }), true, String(status));
  }
  assert.equal(worthTryingAgain({}), true);
});

test("what will not change is never asked again: a refusal, an answer that cannot be read, a read that was cancelled", () => {
  for (const status of [400, 401, 403, 404, 409, 410, 422]) {
    assert.equal(worthTryingAgain({ status }), false, String(status));
  }
  assert.equal(worthTryingAgain({ status: 0, unreadable: true }), false);
  assert.equal(worthTryingAgain({ status: 0, cancelled: true }), false);
});

test("a failed try is tried again after a wait, up to three tries", () => {
  const first = nextStep({ failed: 1, elapsed: 10_000, chance: 0, linkUp: true, failure: { status: 0 } });
  assert.deepEqual(first, { wait: 200 });
  const second = nextStep({ failed: 2, elapsed: 20, chance: 1, linkUp: true, failure: { status: 503 } });
  assert.deepEqual(second, { wait: 800 });
  assert.deepEqual(nextStep({ failed: 3, elapsed: 0, chance: 0.5, linkUp: true, failure: { status: 0 } }), { giveUp: true });
});

test("a refusal is given up at once, tries left or not", () => {
  assert.deepEqual(nextStep({ failed: 1, elapsed: 0, chance: 0, linkUp: true, failure: { status: 404 } }), { giveUp: true });
  assert.deepEqual(nextStep({ failed: 1, elapsed: 0, chance: 0, linkUp: true, failure: { status: 401 } }), { giveUp: true });
  assert.deepEqual(nextStep({ failed: 1, elapsed: 0, chance: 0, linkUp: true, failure: { status: 0, cancelled: true } }), { giveUp: true });
});

test("while the phone has no link at all a failure spends no try: the read waits for the link", () => {
  for (const failed of [1, 2, 3, 7]) {
    assert.deepEqual(nextStep({ failed, elapsed: 100, chance: 0.3, linkUp: false, failure: { status: 0 } }), { waitForLink: true });
  }
  // But a refusal is a refusal with or without a link.
  assert.deepEqual(nextStep({ failed: 1, elapsed: 0, chance: 0, linkUp: false, failure: { status: 404 } }), { giveUp: true });
});

test("a try that would start past the 20 s the read may take does not start", () => {
  assert.deepEqual(nextStep({ failed: 1, elapsed: BUDGET_MS - 100, chance: 1, linkUp: true, failure: { status: 0 } }), { giveUp: true });
  assert.deepEqual(nextStep({ failed: 1, elapsed: BUDGET_MS - 1000, chance: 0, linkUp: true, failure: { status: 0 } }), { wait: 200 });
});

test("a gap of 500 events or more is a long one; fewer are read in full", () => {
  assert.equal(longGap(0), false);
  assert.equal(longGap(499), false);
  assert.equal(longGap(500), true);
  assert.equal(longGap(1200), true);
});

test("the tail is added only when the daemon said that it knows `tail`", () => {
  const tail = [{ id: 900 }, { id: 901 }, { id: 905 }];
  assert.deepEqual(tailToAdd(tail, true, 400), tail);
  // An older daemon ignores `tail` and answers the first events of the run: no end of it.
  assert.equal(tailToAdd([{ id: 1 }, { id: 2 }], false, 0), null);
});

test("events the chat holds already are not added again", () => {
  assert.deepEqual(tailToAdd([{ id: 5 }, { id: 6 }, { id: 7 }], true, 6), [{ id: 7 }]);
  assert.deepEqual(tailToAdd([{ id: 5 }], true, 9), []);
});

test("only the writes the outbox keeps give up after 30 s: a message, an answer, a Stop and a change of a queue", () => {
  const kept = [
    "/runs",
    "/runs/run_a1/steer",
    "/runs/run_a1/cancel",
    "/approvals/ap_9/decision",
    "/threads/th_3/queue",
    "/threads/th_3/queue/resume",
    "/queue/q_7",
  ];
  for (const path of kept) {
    assert.equal(isKeptWrite(path), true, path);
  }
  // A rewind, a loop, a queued message's Steer, a schedule, an agent, a file or a voice may take as long as they take.
  const left = ["/runs/run_a1/rewind", "/runs/run_a1/loop", "/queue/q_7/steer", "/schedules", "/agents", "/files", "/voice", "/threads/th_3/queue/other"];
  for (const path of left) {
    assert.equal(isKeptWrite(path), false, path);
  }
});
