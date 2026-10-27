// What a night of learning that took no task says of the pool of test tasks (harnessnight.js, item 762).

import assert from "node:assert/strict";
import { test } from "node:test";

import { poolNightWords } from "../../src/dotagent/server/static/js/harnessnight.js";

test("a night that tried tasks of the pool says so", () => {
  assert.equal(
    poolNightWords("calibrating", { calibrated: ["a", "b"], headroom: [] }),
    "tried test tasks, learned from no task",
  );
});

test("a night with nothing to learn does not say it tried tasks", () => {
  // Every task of the pool was known and too few had headroom: the record names headroom and no task tried.
  assert.equal(
    poolNightWords("keep", { headroom: ["a"], decision: "nothing to learn on the pool" }),
    "tried no test task, learned from no task",
  );
  assert.equal(poolNightWords("keep", { headroom: [], calibrated: [] }), "tried no test task, learned from no task");
});

test("a night that was not about the pool says nothing of it", () => {
  assert.equal(poolNightWords("keep", { decision: "no change" }), null);
  assert.equal(poolNightWords("failed", {}), null);
});
