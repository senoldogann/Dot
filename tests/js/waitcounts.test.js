// What the line over the composer says of the subagents (item 1351): a run waits only on those it did not let go.

import assert from "node:assert/strict";
import { test } from "node:test";

import { waitCounts, waitsOnHelpers } from "../../src/dotagent/server/static/js/waitcounts.js";

test("a run waits on every subagent that works until it lets one go", () => {
  assert.deepEqual(waitCounts(["running", "queued", "done"], []), { live: 2, letGo: 0, waitedFor: 2 });
});

test("a subagent the run stopped waiting for works in the background and is not waited for", () => {
  assert.deepEqual(waitCounts(["running", "running"], ["running", "running"]), { live: 2, letGo: 2, waitedFor: 0 });
  assert.deepEqual(waitCounts(["running", "running"], ["running"]), { live: 2, letGo: 1, waitedFor: 1 });
});

test("one that let go and has ended is not counted, and a line over none is empty", () => {
  assert.deepEqual(waitCounts(["done", "running"], ["done"]), { live: 1, letGo: 0, waitedFor: 1 });
  assert.deepEqual(waitCounts([], []), { live: 0, letGo: 0, waitedFor: 0 });
  assert.deepEqual(waitCounts(["failed", "cancelled"], ["failed"]), { live: 0, letGo: 0, waitedFor: 0 });
});

test("a run waits on its helpers only while every open call of its step is a wait on one", () => {
  const delegate = (wait) => ({ tool: "delegate", args: wait === undefined ? { goal: "a part" } : { goal: "a part", wait } });
  assert.equal(waitsOnHelpers([delegate(undefined), delegate(true)]), true);
  assert.equal(waitsOnHelpers([delegate(undefined), { tool: "shell", args: { command: "npm test" } }]), false, "a command beside it runs");
  assert.equal(waitsOnHelpers([delegate(false)]), false, "a helper it asked not to wait for holds nothing");
  assert.equal(waitsOnHelpers([{ tool: "Task", args: {} }]), true, "a coding CLI's agent tool waits too");
  assert.equal(waitsOnHelpers([{ tool: "agent_status", args: {} }]), false, "a name with agent in it is no wait");
  assert.equal(waitsOnHelpers([]), false, "no open call: the model works on its next step");
});
