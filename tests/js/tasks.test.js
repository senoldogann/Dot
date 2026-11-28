// The words of a chat's tasks (taskwords.js, item 2013): how a task stands, and what a message's route says. Pure, so it
// runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { openTasks, routedWords, taskStateWords } from "../../src/dotagent/server/static/js/taskwords.js";

/** A task; each test changes what it is about. */
function task(changes = {}) {
  return { thread_id: "thr_x", title: "Improve my x.com account", run_id: "run_x", status: "running", state: "working", own: false, ...changes };
}

test("a task says whether it works, waits for you or how it ended", () => {
  assert.equal(taskStateWords(task()), "Working");
  assert.equal(taskStateWords(task({ state: "waits", status: "done" })), "Waits for you");
  assert.equal(taskStateWords(task({ state: "ended", status: "done" })), "Done");
  assert.equal(taskStateWords(task({ state: "ended", status: "cancelled" })), "Stopped");
  assert.equal(taskStateWords(task({ state: "ended", status: "failed" })), "Failed");
  assert.equal(openTasks([task(), task({ state: "waits" }), task({ state: "ended" })]), 2);
});

test("a message routed elsewhere says where, with Open and Move; one that stayed says nothing", () => {
  const sent = routedWords({ kind: "continued", thread_id: "thr_form", run_id: "run_f", message_id: null }, "thr_chat", "thr_chat", "Fill the form");
  assert.deepEqual(sent, { text: "Sent to Fill the form", open: true, move: true });
  const started = routedWords({ kind: "new", thread_id: "thr_x", run_id: "run_x", message_id: null }, "thr_chat", "thr_chat", "x.com");
  assert.deepEqual(started, { text: "Started a new task: x.com", open: true, move: true });
  assert.equal(routedWords({ kind: "queued", thread_id: "thr_chat", run_id: null, message_id: "que_1" }, "thr_chat", "thr_chat", "This chat"), null);
  const all = routedWords({ kind: "stopped", thread_id: "thr_chat", run_id: null, message_id: null }, "thr_chat", "thr_chat", "This chat");
  assert.deepEqual(all, { text: "Stopped every task", open: false, move: false });
  // A steer went into a run already at work: Move would stop it, so it is not offered (the pre-push review).
  const steered = routedWords({ kind: "steered", thread_id: "thr_x", run_id: "run_x", message_id: null }, "thr_chat", "thr_chat", "x.com");
  assert.deepEqual(steered, { text: "Sent to x.com", open: true, move: false });
  const one = routedWords({ kind: "stopped", thread_id: "thr_x", run_id: "run_x", message_id: null }, "thr_chat", "thr_chat", "x.com");
  assert.equal(one?.text, "Stopped x.com");
});
