// The plan as its run left it and its head's words (planwords.js, the owner's report of 2026-11-28). Pure.

import assert from "node:assert/strict";
import { test } from "node:test";

import { planAtEnd, planCurrentStep, planEnd, planHead } from "../../src/dotagent/server/static/js/planwords.js";

const steps = [
  { text: "Read the CV", status: "done" },
  { text: "Search LinkedIn", status: "in_progress" },
  { text: "Write the letters", status: "pending" },
];

test("a failed or stopped run's step in progress says so; at work or done the plan stands as it is", () => {
  assert.equal(planEnd("failed"), "failed");
  assert.equal(planEnd("cancelled"), "stopped");
  assert.equal(planEnd("running"), "working");
  assert.equal(planEnd(null), "working");
  assert.deepEqual(planAtEnd(steps, "failed").map((step) => step.status), ["done", "failed", "pending"]);
  assert.deepEqual(planAtEnd(steps, "stopped").map((step) => step.status), ["done", "stopped", "pending"]);
  assert.equal(planAtEnd(steps, "working"), steps);
});

test("the head counts the done steps and says where the run stands", () => {
  assert.deepEqual(planHead(steps, "working"), { done: 1, total: 3, count: "1 of 3 done", state: "Working on step 2" });
  assert.equal(planHead(planAtEnd(steps, "failed"), "failed").state, "Failed at step 2");
  assert.equal(planHead(planAtEnd(steps, "stopped"), "stopped").state, "Stopped at step 2");
  assert.equal(planHead([{ status: "done" }], "done").state, "The run is done");
  assert.equal(planHead([{ status: "done" }], "working").state, "Finishing");
  assert.equal(planCurrentStep([{ status: "done" }, { status: "skipped" }]), null);
});
