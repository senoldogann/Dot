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
  assert.equal(odd.line, "Repeats on a schedule");
});
