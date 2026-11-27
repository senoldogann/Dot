// What the one capsule over a phone's composer says (dockwords.js, item 1383): which segments it has and their words.
// Pure, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { dockSegments } from "../../src/dotagent/server/static/js/dockwords.js";

/** A state with nothing to say; each test changes what it is about. */
function state(changes = {}) {
  return {
    status: null,
    took: 0,
    stopping: false,
    reconnecting: false,
    plan: null,
    helpers: { live: 0, waitedFor: 0, letGo: 0 },
    waits: false,
    loops: { count: 0, rhythm: null, problem: false },
    queue: { count: 0, held: false, problem: false, sending: false },
    running: { count: 0, problem: false },
    tasks: { open: 0 },
    ...changes,
  };
}

const ids = (segments) => segments.map((segment) => segment.id);

test("a chat with nothing to say has no capsule", () => {
  assert.deepEqual(dockSegments(state()), []);
  // A run that ended, helpers that finished and a plan that is done say nothing either.
  assert.deepEqual(dockSegments(state({ plan: { done: 3, total: 3 }, helpers: { live: 2, waitedFor: 2, letGo: 0 } })), []);
});

test("a run at work says how long it has worked, with its Stop", () => {
  const segments = dockSegments(state({ status: "running", took: 36 }));
  assert.deepEqual(ids(segments), ["status", "stop"]);
  assert.equal(segments[0].label, "Working · 36s");
  assert.equal(segments[0].short, "36s");
  assert.equal(segments[0].tone, "work");
  assert.equal(segments[1].label, "Stop");
  assert.equal(segments[1].name, "Stop the task");
});

test("a run that waits for the owner says so, in a tone of its own, and a run not started says that", () => {
  const waiting = dockSegments(state({ status: "blocked", took: 90 }))[0];
  assert.deepEqual([waiting.label, waiting.short, waiting.tone], ["Waiting for you", "Waiting for you", "waiting"]);
  const queued = dockSegments(state({ status: "queued" }))[0];
  assert.deepEqual([queued.label, queued.tone], ["Waiting to start", "quiet"]);
});

test("a run that was told to stop says it stops, and offers no Stop", () => {
  const segments = dockSegments(state({ status: "running", took: 5, stopping: true }));
  assert.deepEqual(ids(segments), ["status"]);
  assert.equal(segments[0].label, "Stopping…");
});

test("a lost stream says it reconnects, over the time, and keeps the Stop of a run that works", () => {
  const working = dockSegments(state({ status: "running", took: 36, reconnecting: true }));
  assert.deepEqual(ids(working), ["status", "stop"]);
  assert.equal(working[0].label, "Chat updates interrupted");
  assert.equal(working[0].tone, "warn");
  // With no run to stop, the capsule is the one segment that says it.
  assert.deepEqual(ids(dockSegments(state({ reconnecting: true }))), ["status"]);
});

test("the plan, when the run has one, says how far it has come", () => {
  const plan = dockSegments(state({ status: "running", plan: { done: 2, total: 5 } })).find((segment) => segment.id === "plan");
  assert.deepEqual([plan?.label, plan?.short, plan?.name, plan?.sheet], ["Plan 2/5", "2/5", "Plan, 2 of 5 steps done", true]);
  assert.equal(dockSegments(state({ status: "running", plan: { done: 0, total: 0 } })).some((segment) => segment.id === "plan"), false);
});

test("helpers that work are counted, and the name says whether the run waits for them", () => {
  const waited = dockSegments(state({ status: "running", helpers: { live: 2, waitedFor: 2, letGo: 0 } })).find((segment) => segment.id === "helpers");
  assert.equal(waited?.label, "2 helpers");
  assert.equal(waited?.short, "2");
  assert.equal(waited?.name, "2 helpers. Waiting on 2 helpers");
  const loose = dockSegments(state({ status: "running", helpers: { live: 1, waitedFor: 0, letGo: 1 } })).find((segment) => segment.id === "helpers");
  assert.equal(loose?.label, "1 helper");
  assert.equal(loose?.name, "1 helper. 1 helper keeps working in the background");
});

test("the status says it waits on helpers only while the run has stopped for them, and Working beside them", () => {
  const helpers = { live: 2, waitedFor: 2, letGo: 0 };
  const beside = dockSegments(state({ status: "running", took: 40, helpers, waits: false }));
  assert.equal(beside[0].label, "Working · 40s", "the run works beside its helpers");
  assert.ok(beside.some((segment) => segment.id === "helpers"), "the helpers keep their own segment");
  const waits = dockSegments(state({ status: "running", took: 40, helpers, waits: true }));
  assert.deepEqual([waits[0].label, waits[0].short, waits[0].tone], ["Waiting on 2 helpers", "Waiting on 2 helpers", "work"]);
  assert.deepEqual(ids(waits), ["status", "stop", "helpers"]);
  // Helpers it let go hold nothing: with none waited for, it works.
  const loose = dockSegments(state({ status: "running", took: 40, helpers: { live: 1, waitedFor: 0, letGo: 1 }, waits: true }));
  assert.equal(loose[0].label, "Working · 40s");
  // A wait for the owner says that, whatever the helpers do.
  assert.equal(dockSegments(state({ status: "blocked", helpers, waits: true }))[0].label, "Waiting for you");
});

test("one loop says how often it repeats, a loop of many words and several say how many", () => {
  const one = dockSegments(state({ loops: { count: 1, rhythm: "every 10 min", problem: false } }))[0];
  assert.deepEqual([one.id, one.label, one.short, one.name], ["loops", "every 10 min", "1", "Loop, every 10 min"]);
  const long = dockSegments(state({ loops: { count: 1, rhythm: "on Mon, Wed and Fri at 18:30", problem: false } }))[0];
  assert.deepEqual([long.label, long.name], ["1 loop", "1 loop"]);
  const none = dockSegments(state({ loops: { count: 1, rhythm: null, problem: false } }))[0];
  assert.equal(none.label, "1 loop");
  const many = dockSegments(state({ loops: { count: 2, rhythm: null, problem: false } }))[0];
  assert.deepEqual([many.label, many.short], ["2 loops", "2"]);
});

test("the queue is counted, a held one says it is paused, and a message on its way says so", () => {
  const counted = dockSegments(state({ queue: { count: 3, held: false, problem: false, sending: false } }))[0];
  assert.deepEqual([counted.id, counted.label, counted.short, counted.tone], ["queue", "3 queued", "3", "quiet"]);
  const held = dockSegments(state({ queue: { count: 3, held: true, problem: false, sending: false } }))[0];
  assert.deepEqual([held.label, held.tone], ["3 paused", "warn"]);
  const sending = dockSegments(state({ queue: { count: 0, held: false, problem: false, sending: true } }))[0];
  assert.deepEqual([sending.label, sending.short], ["Sending…", "…"]);
  const unread = dockSegments(state({ queue: { count: 0, held: false, problem: true, sending: false } }))[0];
  assert.deepEqual([unread.label, unread.tone], ["Could not read", "warn"]);
});

test("the commands at work are counted, and a list that could not be read says so", () => {
  const running = dockSegments(state({ running: { count: 2, problem: false } }))[0];
  assert.deepEqual([running.id, running.label, running.short], ["running", "2 running", "2"]);
  const unread = dockSegments(state({ running: { count: 0, problem: true } }))[0];
  assert.deepEqual([unread.label, unread.tone], ["Could not read", "warn"]);
});

test("a read that failed after one that worked keeps its count, in the warning's tone, and its name says so", () => {
  const running = dockSegments(state({ running: { count: 2, problem: true } }))[0];
  assert.deepEqual([running.label, running.tone, running.name], ["2 running", "warn", "2 running. Could not read"]);
  const one = dockSegments(state({ loops: { count: 1, rhythm: "every 10 min", problem: true } }))[0];
  assert.deepEqual([one.label, one.tone, one.name], ["every 10 min", "warn", "Loop, every 10 min. Could not read"]);
  const many = dockSegments(state({ loops: { count: 3, rhythm: null, problem: true } }))[0];
  assert.deepEqual([many.label, many.tone, many.name], ["3 loops", "warn", "3 loops. Could not read"]);
  // The queue says it as well: a count with a problem is in the warning's tone, and named so.
  const queue = dockSegments(state({ queue: { count: 2, held: false, problem: true, sending: false } }))[0];
  assert.deepEqual([queue.label, queue.tone, queue.name], ["2 queued", "warn", "2 queued. Could not read"]);
});

test("the segments come in one order, and the chat's own stand when no run works", () => {
  const everything = dockSegments(
    state({
      status: "running",
      took: 36,
      plan: { done: 1, total: 4 },
      helpers: { live: 2, waitedFor: 2, letGo: 0 },
      loops: { count: 1, rhythm: "every 10 min", problem: false },
      queue: { count: 1, held: false, problem: false, sending: false },
      running: { count: 1, problem: false },
    }),
  );
  assert.deepEqual(ids(everything), ["status", "stop", "plan", "helpers", "loops", "queue", "running"]);
  const resting = dockSegments(
    state({
      loops: { count: 1, rhythm: "every 10 min", problem: false },
      queue: { count: 1, held: true, problem: false, sending: false },
      running: { count: 1, problem: false },
    }),
  );
  assert.deepEqual(ids(resting), ["loops", "queue", "running"]);
});

test("a chat with several tasks counts those that work or wait, before its loops (item 2013)", () => {
  const one = dockSegments(state({ tasks: { open: 1 } }));
  assert.deepEqual(ids(one), ["tasks"]);
  assert.equal(one[0].label, "1 task");
  assert.equal(one[0].sheet, true);
  const many = dockSegments(state({ tasks: { open: 3 }, loops: { count: 1, rhythm: "every 30 min", problem: false } }));
  assert.deepEqual(ids(many), ["tasks", "loops"]);
  assert.equal(many[0].label, "3 tasks");
  assert.equal(many[0].short, "3");
});
