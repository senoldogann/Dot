// The rows of a section, nested (item 68): delegated runs under the run that delegated them, and a run with several rows
// (item 482: the steps it kept for you) shows each of them.

import assert from "node:assert/strict";
import { test } from "node:test";

import { nestRows } from "../../src/dotagent/server/static/js/nesting.js";

function row(id, runId, parentRunId) {
  return { id, run_id: runId, parent_run_id: parentRunId };
}

test("a delegated run hangs under the run that delegated it", () => {
  const nested = nestRows([row("a", "run_1", null), row("b", "run_2", "run_1"), row("c", "run_3", null)]);
  assert.deepEqual(
    nested.map(({ row: shown, children }) => [shown.id, children.map((kid) => kid.id)]),
    [
      ["a", ["b"]],
      ["c", []],
    ],
  );
});

test("each row of a run with several stands, the first taking its delegated runs", () => {
  const nested = nestRows([row("kept", "run_1", null), row("left", "run_1", null), row("kid", "run_2", "run_1")]);
  assert.deepEqual(
    nested.map(({ row: shown, children }) => [shown.id, children.map((kid) => kid.id)]),
    [
      ["kept", ["kid"]],
      ["left", []],
    ],
  );
});

test("a row of no run, or whose parent is not listed, stands on its own", () => {
  const nested = nestRows([row("due", null, null), row("orphan", "run_5", "run_9")]);
  assert.deepEqual(
    nested.map(({ row: shown }) => shown.id),
    ["due", "orphan"],
  );
});
