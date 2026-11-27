// What a chat's side panel says of a schedule (automationwords.js, item 1334): a title and a quiet line, pure, so it
// runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { automationWords } from "../../src/dotagent/server/static/js/automationwords.js";

const NOW = Date.UTC(2026, 10, 22, 12, 0) / 1000;
const DAY_S = 86400;

/** A schedule as `GET /api/threads/{id}/automations` gives it. */
function view(fields) {
  return {
    record: { name: "Check", cron: null, run_at: null, enabled: true, next_run_at: null, last_status: "", ...fields },
    ran_once: false,
    ...fields.view,
  };
}

test("a schedule that repeats says how, and a paused one says so", () => {
  const six = automationWords(view({ name: "Gistsmith iPhone build check", cron: "0 */6 * * *", enabled: false }), false, NOW);
  assert.equal(six.title, "Gistsmith iPhone build check");
  assert.equal(six.line, "Every 6 hours · paused");
  assert.equal(six.active, false);
});

test("a loop names its rhythm, loses its Loop: and says when it asks next", () => {
  const next = NOW + DAY_S * 2;
  const loop = automationWords(view({ name: "Loop: check the build", cron: "0 9 * * *", next_run_at: next }), true, NOW);
  assert.equal(loop.title, "check the build");
  assert.match(loop.line, /^Loops every day at 09:00 · next /);
  assert.equal(loop.active, true);
});

test("a schedule that runs once says its day, and one that ran says it is done", () => {
  const once = automationWords(view({ name: "Reddit comments", run_at: NOW + DAY_S * 10, next_run_at: NOW + DAY_S * 10 }), false, NOW);
  assert.match(once.line, /^Once, \d+ \w+ at \d\d:\d\d$/);
  const ran = automationWords(view({ run_at: NOW - 60, enabled: false, view: { ran_once: true } }), false, NOW);
  assert.match(ran.line, / · done$/);
  assert.equal(ran.done, true);
  assert.equal(ran.active, false);
});

test("a schedule whose last run failed, or that runs now, says it by its state", () => {
  assert.equal(automationWords(view({ cron: "0 9 * * *", last_status: "failed: the model stopped" }), false, NOW).failed, true);
  const off = automationWords(view({ cron: "0 9 * * *", enabled: false, last_status: "failed: x" }), false, NOW);
  assert.equal(off.failed, false);
  assert.equal(automationWords(view({ cron: "0 9 * * *", last_status: "running" }), false, NOW).running, true);
});

test("a cron that has no words is said plainly, never as the cron", () => {
  const odd = automationWords(view({ cron: "0 9 1 * *" }), false, NOW);
  assert.equal(odd.line, "Repeats as a routine");
});

test("a schedule says how its last run ended and when, and how many failed in a row (item 1355)", () => {
  const ran = automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 3600, last_status: "done" }), false, NOW);
  assert.match(ran.last, /^Last run done · \d\d:\d\d$/);
  const older = automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 2 * DAY_S, last_status: "done" }), false, NOW);
  assert.match(older.last, /^Last run done · \d+ \w+ \d\d:\d\d$/);
  const failed = automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 3600, last_status: "failed: x" }), false, NOW);
  assert.match(failed.last, /^Last run failed · /);
  const row = automationWords(
    view({ cron: "0 9 * * *", last_run_at: NOW - 3600, last_status: "failed: x", runs_failed_in_row: 3 }),
    false,
    NOW,
  );
  assert.match(row.last, /^Last 3 runs failed · latest /);
  // A skip carries no time of its own, so none is said, and one that has no earlier run says it too.
  const skipped = automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 60, last_status: "skipped: busy" }), false, NOW);
  assert.equal(skipped.last, "Last firing skipped");
  const first = automationWords(view({ cron: "0 9 * * *", last_run_at: null, last_status: "skipped: the emergency stop" }), false, NOW);
  assert.equal(first.last, "Last firing skipped");
});

test("firings skipped one after the other say how many in a row, from two on, and a run after them says its own end (item 1401)", () => {
  const skipped = (count) =>
    automationWords(view({ cron: "*/10 * * * *", last_run_at: NOW - 60, last_status: "skipped: busy", skipped_in_row: count }), false, NOW).last;
  assert.equal(skipped(1), "Last firing skipped");
  assert.equal(skipped(2), "Skipped 2 in a row");
  assert.equal(skipped(27), "Skipped 27 in a row");
  // An older daemon sends no count: the plain word, as before.
  assert.equal(skipped(undefined), "Last firing skipped");
  // The count is the firings' own: a status that is no skip says how its run ended, whatever the count was.
  const done = automationWords(
    view({ cron: "*/10 * * * *", last_run_at: NOW - 60, last_status: "done", skipped_in_row: 4 }),
    false,
    NOW,
  );
  assert.match(done.last, /^Last run done · /);
});

test("a schedule that never ran, or works now, has no word of its last run", () => {
  assert.equal(automationWords(view({ cron: "0 9 * * *", last_run_at: null, last_status: "" }), false, NOW).last, "");
  assert.equal(automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 5, last_status: "running" }), false, NOW).last, "");
  assert.equal(automationWords(view({ cron: "0 9 * * *", last_run_at: NOW - 5, last_status: "cancelled" }), false, NOW).last, "");
});
