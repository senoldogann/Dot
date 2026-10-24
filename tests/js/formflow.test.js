// What a form in a dialog does apart from the page (item 615): whether it was changed, which field takes the focus, and
// what its buttons do: a submit that is busy, fails and stays open, or is done and closes; a close that asks first.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createFormFlow, firstFieldOf, stateOfControls } from "../../src/dotagent/server/static/js/formflow.js";

/** A control as the browser hands it to stateOfControls: a field with a value, or a button with attributes. */
function control(tagName, fields = {}, attributes = {}) {
  return { tagName, type: "", value: "", checked: false, getAttribute: (name) => attributes[name] ?? null, ...fields };
}

test("a form's state changes with a typed value, a ticked box, a chosen option, a switch and a pressed button", () => {
  const text = control("INPUT", { type: "text", value: "Morning" });
  const area = control("TEXTAREA", { value: "Look at the tide." });
  const box = control("INPUT", { type: "checkbox", checked: false });
  const pick = control("SELECT", { value: "task" });
  const toggle = control("BUTTON", {}, { "aria-checked": "true" });
  const pressed = control("BUTTON", {}, { "aria-pressed": "false" });
  const all = [text, area, box, pick, toggle, pressed];
  const before = stateOfControls(all);
  assert.equal(stateOfControls(all), before);

  text.value = "Evening";
  assert.notEqual(stateOfControls(all), before);
  text.value = "Morning";
  assert.equal(stateOfControls(all), before);

  area.value += " ";
  assert.notEqual(stateOfControls(all), before);
  area.value = "Look at the tide.";

  box.checked = true;
  assert.notEqual(stateOfControls(all), before);
  box.checked = false;

  pick.value = "research";
  assert.notEqual(stateOfControls(all), before);
  pick.value = "task";

  toggle.getAttribute = () => "false";
  assert.notEqual(stateOfControls(all), before);
  toggle.getAttribute = (name) => ({ "aria-checked": "true" })[name] ?? null;
  assert.equal(stateOfControls(all), before);

  pressed.getAttribute = (name) => ({ "aria-pressed": "true" })[name] ?? null;
  assert.notEqual(stateOfControls(all), before);
});

test("two fields that trade their values are a changed form, not the same one", () => {
  const one = control("INPUT", { type: "text", value: "a" });
  const other = control("INPUT", { type: "text", value: "b" });
  const before = stateOfControls([one, other]);
  one.value = "b";
  other.value = "a";
  assert.notEqual(stateOfControls([one, other]), before);
});

test("the field that takes the focus is the first one on the screen that can be typed in", () => {
  const hidden = { type: "hidden", id: "hidden" };
  const off = { type: "text", id: "off", disabled: true };
  const fixed = { type: "text", id: "fixed", readOnly: true };
  const folded = { type: "text", id: "folded" };
  const name = { type: "text", id: "name" };
  const goal = { type: "text", id: "goal" };
  const shown = (field) => field.id !== "folded";
  assert.equal(firstFieldOf([hidden, off, fixed, folded, name, goal], shown), name);
  assert.equal(firstFieldOf([hidden, off, fixed, folded], shown), null);
  assert.equal(firstFieldOf([], shown), null);
});

/** A flow over a form that says what it was asked, with `changed` and the answer to the question as the test sets them. */
function flowOf({ changed = false, discards = true } = {}) {
  const seen = { busy: [], problems: [], finished: [], lost: [], asked: 0, changed, discards };
  let answer = () => Promise.resolve(seen.discards);
  const flow = createFormFlow({
    changed: () => seen.changed,
    discard: () => {
      seen.asked += 1;
      return answer();
    },
    busy: (which) => seen.busy.push(which),
    problem: (error) => seen.problems.push(error === null ? null : error.message),
    finish: (submitted) => seen.finished.push(submitted),
    lost: (error) => seen.lost.push(error.message),
  });
  return {
    flow,
    seen,
    /** The owner takes their time over the question: `answer(value)` gives the reply. */
    hold() {
      let reply = () => undefined;
      answer = () => new Promise((resolve) => (reply = resolve));
      return (value) => reply(value);
    },
  };
}

/** An action that the test finishes when it likes. */
function slowAction() {
  let done = () => undefined;
  let fail = () => undefined;
  const calls = { count: 0 };
  const run = () => {
    calls.count += 1;
    return new Promise((resolve, reject) => {
      done = resolve;
      fail = reject;
    });
  };
  return { run, calls, done: (value) => done(value), fail: (error) => fail(error) };
}

test("a submit is busy while it runs, then done and closed", async () => {
  const { flow, seen } = flowOf();
  const action = slowAction();
  const submitting = flow.submit(action.run, "primary");
  assert.deepEqual(seen.busy, ["primary"]);
  assert.equal(flow.working(), true);
  assert.deepEqual(seen.finished, []);
  action.done(undefined);
  assert.equal(await submitting, true);
  assert.deepEqual(seen.busy, ["primary", null]);
  assert.deepEqual(seen.finished, [true]);
  assert.equal(flow.working(), false);
});

test("a second press while a submit runs does nothing, so a request is sent once", async () => {
  const { flow, seen } = flowOf();
  const action = slowAction();
  const first = flow.submit(action.run, "primary");
  assert.equal(await flow.submit(action.run, "primary"), false);
  assert.equal(await flow.submit(action.run, "alternative"), false);
  assert.equal(action.calls.count, 1);
  action.done(undefined);
  await first;
  assert.deepEqual(seen.finished, [true]);
});

test("a submit that fails leaves the dialog open with the reason, and can be tried again", async () => {
  const { flow, seen } = flowOf();
  const action = slowAction();
  const first = flow.submit(action.run, "primary");
  action.fail(new Error("That name is taken."));
  assert.equal(await first, false);
  assert.deepEqual(seen.problems, [null, "That name is taken."]);
  assert.deepEqual(seen.busy, ["primary", null]);
  assert.deepEqual(seen.finished, []);
  assert.equal(flow.working(), false);

  const again = flow.submit(() => Promise.resolve(), "primary");
  assert.equal(await again, true);
  // The reason of the last failure is cleared as the next try begins.
  assert.deepEqual(seen.problems, [null, "That name is taken.", null]);
  assert.deepEqual(seen.finished, [true]);
});

test("a submit that finds a field missing and says false leaves the dialog open and says nothing more", async () => {
  const { flow, seen } = flowOf();
  assert.equal(await flow.submit(() => Promise.resolve(false), "primary"), false);
  assert.deepEqual(seen.finished, []);
  assert.deepEqual(seen.problems, [null]);
  assert.deepEqual(seen.busy, ["primary", null]);
});

test("whatever else a submit resolves to, it is done", async () => {
  for (const value of [undefined, null, true, 0, "", { id: 1 }]) {
    const { flow, seen } = flowOf();
    assert.equal(await flow.submit(() => Promise.resolve(value), "primary"), true);
    assert.deepEqual(seen.finished, [true]);
  }
});

test("the second way to submit is busy by its own name", async () => {
  const { flow, seen } = flowOf();
  const action = slowAction();
  const submitting = flow.submit(action.run, "alternative");
  assert.deepEqual(seen.busy, ["alternative"]);
  action.done(undefined);
  await submitting;
});

test("closing an unchanged form asks nothing", async () => {
  const { flow, seen } = flowOf({ changed: false });
  assert.equal(await flow.dismiss(), true);
  assert.equal(seen.asked, 0);
  assert.deepEqual(seen.finished, [false]);
});

test("closing a changed form asks first, and Keep editing leaves it as it is", async () => {
  const { flow, seen } = flowOf({ changed: true, discards: false });
  assert.equal(await flow.dismiss(), false);
  assert.equal(seen.asked, 1);
  assert.deepEqual(seen.finished, []);
  // Still open: it can be submitted and closed after.
  assert.equal(await flow.submit(() => Promise.resolve(), "primary"), true);
  assert.deepEqual(seen.finished, [true]);
});

test("closing a changed form drops the changes once the owner says so", async () => {
  const { flow, seen } = flowOf({ changed: true, discards: true });
  assert.equal(await flow.dismiss(), true);
  assert.equal(seen.asked, 1);
  assert.deepEqual(seen.finished, [false]);
});

test("Escape pressed again while the question is open asks it once", async () => {
  const { flow, seen, hold } = flowOf({ changed: true });
  const answer = hold();
  const first = flow.dismiss();
  assert.equal(await flow.dismiss(), false);
  assert.equal(seen.asked, 1);
  answer(true);
  assert.equal(await first, true);
  assert.deepEqual(seen.finished, [false]);
});

test("nothing closes the dialog while a submit runs, and the submit still closes it when it is done", async () => {
  const { flow, seen } = flowOf({ changed: true });
  const action = slowAction();
  const submitting = flow.submit(action.run, "primary");
  assert.equal(await flow.dismiss(), false);
  assert.equal(seen.asked, 0);
  assert.deepEqual(seen.finished, []);
  action.done(undefined);
  await submitting;
  assert.deepEqual(seen.finished, [true]);
});

test("a form that is closed cannot be submitted or closed again", async () => {
  const { flow, seen } = flowOf();
  assert.equal(await flow.dismiss(), true);
  assert.equal(await flow.submit(() => Promise.resolve(), "primary"), false);
  assert.equal(await flow.dismiss(), false);
  assert.deepEqual(seen.finished, [false]);
});

test("a page that goes away takes its dialog with it at once, whatever was typed", async () => {
  const { flow, seen } = flowOf({ changed: true });
  flow.abandon();
  flow.abandon();
  assert.equal(seen.asked, 0);
  assert.deepEqual(seen.finished, [false]);
});

test("a submit that ends after its dialog is gone closes nothing twice, and a failure of it is told", async () => {
  const done = flowOf();
  const slow = slowAction();
  const running = done.flow.submit(slow.run, "primary");
  done.flow.abandon();
  slow.done(undefined);
  assert.equal(await running, false);
  assert.deepEqual(done.seen.finished, [false]);
  assert.deepEqual(done.seen.lost, []);

  const failed = flowOf();
  const failing = slowAction();
  const attempt = failed.flow.submit(failing.run, "primary");
  failed.flow.abandon();
  failing.fail(new Error("The daemon said no."));
  assert.equal(await attempt, false);
  assert.deepEqual(failed.seen.finished, [false]);
  assert.deepEqual(failed.seen.lost, ["The daemon said no."]);
  assert.deepEqual(failed.seen.problems, [null]);
});

test("a question answered after the dialog is gone does not close it again", async () => {
  const { flow, seen, hold } = flowOf({ changed: true });
  const answer = hold();
  const closing = flow.dismiss();
  flow.abandon();
  answer(true);
  assert.equal(await closing, false);
  assert.deepEqual(seen.finished, [false]);
});
