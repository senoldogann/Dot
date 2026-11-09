// The rows of a section, nested (item 68): delegated runs under the run that delegated them, and a run with several rows
// (item 482: the steps it kept for you) shows each of them.

import assert from "node:assert/strict";
import { test } from "node:test";

import { nestRows, placeWork } from "../../src/dotagent/server/static/js/nesting.js";

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

// Item 1074: a run another run delegated whose parent works no more (a work order's attempt outlives the lead's turn)
// stands under the chat its work came from, which works while it does, and not as a row of its own.
test("a delegated run whose parent works no more stands under its chat, which works", () => {
  const chat = { id: "thr_lead", title: "Plan the launch", latest_run_id: "run_lead", latest_status: "done", pinned_at: null };
  const attempt = { id: "a", run_id: "run_order", parent_run_id: "run_lead", root_thread_id: "thr_lead", status: "running", title: "Work order" };
  const own = { id: "b", run_id: "run_alone", parent_run_id: null, root_thread_id: null, status: "running", title: "Alone" };
  const { working } = placeWork([attempt, own], [chat]);
  const lead = working.find((entry) => entry.chat !== null);
  assert.equal(lead.chat.id, "thr_lead");
  assert.equal(lead.chat.latest_status, "running");
  assert.deepEqual(
    lead.children.map((kid) => kid.run_id),
    ["run_order"],
  );
  assert.equal(working.filter((entry) => entry.chat === null && entry.row.run_id === "run_order").length, 0);
  assert.equal(working.filter((entry) => entry.chat === null).length, 1);
});
