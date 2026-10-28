// The first steps of a Dot that started with an empty store (item 811): their order, their count, and when the home
// shows them.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIDDEN, STEPS, showsFirstSteps, stepsOf } from "../../src/dotagent/server/static/js/firststepslist.js";

/** A view of the server with the steps named done. */
function view(fresh, ...done) {
  return { fresh, steps: STEPS.map((step) => ({ id: step.id, done: done.includes(step.id) })) };
}

test("the steps keep the home's order whatever order the server names them in, and are counted", () => {
  const { steps, finished } = stepsOf({
    fresh: true,
    steps: [
      { id: "chat", done: true },
      { id: "name", done: true },
      { id: "phone", done: false },
    ],
  });
  assert.deepEqual(
    steps.map((step) => [step.id, step.done]),
    [
      ["name", true],
      ["phone", false],
      ["chat", true],
    ],
  );
  assert.equal(finished, 2);
});

test("a step the server does not name is left out, and one it names that the home does not know is too", () => {
  const { steps } = stepsOf({ fresh: true, steps: [{ id: "name", done: false }, { id: "later", done: false }] });
  assert.deepEqual(
    steps.map((step) => step.id),
    ["name"],
  );
});

test("the card shows on a fresh Dot with a step left, and never once hidden, all done or used for long", () => {
  assert.equal(showsFirstSteps(view(true, "name"), null), true);
  assert.equal(showsFirstSteps(view(true, "name"), HIDDEN), false);
  assert.equal(showsFirstSteps(view(true, ...STEPS.map((step) => step.id)), null), false);
  assert.equal(showsFirstSteps(view(false), null), false);
});

test("each step but the first chat leads to the page where it is done", () => {
  for (const step of STEPS) {
    assert.equal(step.href === null, step.id === "chat", step.id);
    if (step.href !== null) {
      assert.match(step.href, /^#\/(settings|customize)\//);
    }
  }
});
