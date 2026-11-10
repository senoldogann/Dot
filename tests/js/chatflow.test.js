// The chat flow: one transition turns a run's events into sessions, activity and cards.

import assert from "node:assert/strict";
import { test } from "node:test";

import { emptyFlow, presentActivity, questionAnswer, splitStream, transition } from "../../src/dotagent/server/static/js/chatflow.js";

const ROOT = "run_root";

function record(kind, runId, data, ts = 1) {
  return { type: "record", record: { kind, run_id: runId, ts, data } };
}

function apply(state, ...events) {
  return events.reduce((current, event) => transition(current, event), state);
}

test("a streamed reply splits thinking, progress lines and the answer", () => {
  const split = splitStream("<thinking>look at the log</thinking>\nProgress: read the log\nUpdate: tests pass\n\nThe suite is green.\n");
  assert.equal(split.thinking, "look at the log");
  assert.deepEqual(split.progress, ["read the log", "tests pass"]);
  assert.equal(split.answer, "The suite is green.");
});

test("several chosen options and a written line are one answer", () => {
  assert.equal(questionAnswer(["Ship it", "Ask first"], "tonight"), "Ship it; Ask first\ntonight");
  assert.equal(questionAnswer([], "  tonight  "), "tonight");
  assert.equal(questionAnswer([], "  "), "");
});

test("a delegated run moves queued, running, done, and a finished one stays finished", () => {
  const state = apply(
    emptyFlow(ROOT),
    record("run_queued", "run_child", { parent_run_id: ROOT, goal: "Fix the flaky phone test", agent_name: "Coder" }),
    record("run_started", "run_child", { model: "fast" }),
    record("tool_call", "run_child", { call_id: "c1", tool: "read_file", args: { path: "tests/phone.py" } }),
    record("tool_result", "run_child", { call_id: "c1", tool: "read_file", ok: true }),
    record("run_finished", "run_child", { status: "done", result: "Fixed." }),
    record("run_started", "run_child", {}),
  );
  const session = state.sessions.run_child;
  assert.equal(session.status, "done");
  assert.equal(session.title, "Coder");
  assert.equal(session.prompt, "Fix the flaky phone test");
  assert.equal(session.parentId, ROOT);
  assert.deepEqual(
    state.activity.run_child.steps.map((step) => step.status),
    ["done"],
  );
  assert.equal(presentActivity(state.activity.run_child).answer, "Fixed.");
});

test("a failed session and a cancelled one are terminal, and a cut-off step fails with them", () => {
  const failed = apply(
    emptyFlow(ROOT),
    record("run_queued", "run_bad", { parent_run_id: ROOT, goal: "Ship it" }),
    record("tool_call", "run_bad", { call_id: "c1", tool: "shell", args: { command: "pytest" } }),
    record("run_finished", "run_bad", { status: "failed", result: "pytest failed" }),
  );
  assert.equal(failed.sessions.run_bad.status, "failed");
  assert.equal(failed.activity.run_bad.steps[0].status, "failed");

  const stopped = apply(emptyFlow(ROOT), record("run_queued", "run_stop", { parent_run_id: ROOT, goal: "Wait" }), record("run_finished", "run_stop", { status: "cancelled", result: "" }));
  assert.equal(stopped.sessions.run_stop.status, "cancelled");
});

test("opening a session that was never started stays on the chat", () => {
  const state = emptyFlow(ROOT);
  assert.equal(transition(state, { type: "open_session", id: "missing" }), state);
  const started = transition(state, record("run_queued", "run_child", { parent_run_id: ROOT, goal: "Read the brief" }));
  const open = transition(started, { type: "open_session", id: "run_child" });
  assert.deepEqual(open.view, { kind: "session", id: "run_child" });
  assert.equal(transition(open, { type: "close_session" }).view.kind, "chat");
});

test("thinking stays out of the answer, and a progress line in the answer becomes its own item", () => {
  const state = apply(
    emptyFlow(ROOT),
    record("delta", ROOT, { kind: "thinking", text: "check the lock" }),
    record("delta", ROOT, { kind: "text", text: "Progress: waiting on the lock\nDone waiting." }),
    record("tool_call", ROOT, { call_id: "n1", tool: "notify", args: { title: "Lock released", message: "the suite can run" } }),
  );
  const shown = presentActivity(state.activity[ROOT]);
  assert.equal(shown.thinkingPhase, "done");
  assert.equal(shown.thinking, "check the lock");
  assert.equal(shown.answer, "Done waiting.");
  assert.ok(shown.progress.includes("waiting on the lock"));
  assert.ok(shown.progress.includes("Lock released — the suite can run"));
  assert.equal(shown.current.tool, "notify");
  assert.equal(shown.current.status, "running");
});

test("a reset drops the live thought and the live answer", () => {
  const streamed = apply(emptyFlow(ROOT), record("delta", ROOT, { kind: "thinking", text: "nope" }), record("delta", ROOT, { kind: "text", text: "half" }));
  const reset = transition(streamed, record("delta", ROOT, { kind: "reset", text: "" }));
  assert.equal(reset.activity[ROOT].thinking.text, "");
  assert.equal(reset.activity[ROOT].answer.text, "");
});

test("a question is a card with options, and the answer is the user's reply", () => {
  const asked = apply(
    emptyFlow(ROOT),
    record("approval_requested", ROOT, {
      approval_id: "apr_1",
      severity: "question",
      summary: "Which colour?",
      args: { question: "Which colour?", options: [{ label: "Blue", description: "The calm one", recommended: true }, "Green"], multiple: false },
    }),
  );
  assert.equal(asked.activity[ROOT].wait, "question");
  const card = asked.cards[0];
  assert.equal(card.kind, "question");
  assert.equal(card.phase, "open");
  assert.equal(card.options[0].recommended, true);
  assert.equal(card.options[1].label, "Green");
  const answered = transition(asked, record("approval_decided", ROOT, { approval_id: "apr_1", status: "approved", note: "Blue" }));
  assert.equal(answered.cards[0].phase, "answered");
  assert.equal(answered.cards[0].answer, "Blue");
  assert.equal(answered.activity[ROOT].wait, "none");
  assert.equal(transition(answered, record("approval_decided", ROOT, { approval_id: "apr_1", status: "denied", note: "" })), answered);
});

test("a question the run left does not block the session", () => {
  const state = apply(
    emptyFlow(ROOT),
    record("run_queued", "run_child", { parent_run_id: ROOT, goal: "Ask later" }),
    record("run_started", "run_child", {}),
    record("question_left", "run_child", { approval_id: "apr_2", severity: "question", summary: "Go on?", args: { question: "Go on?", options: ["Yes"] } }),
  );
  assert.equal(state.sessions.run_child.status, "running");
  assert.equal(state.cards[0].phase, "open");
  assert.equal(state.activity.run_child, undefined);
});

test("an approval card records the daemon's decision and does not invent one", () => {
  const asked = apply(
    emptyFlow(ROOT),
    record("run_queued", "run_child", { parent_run_id: ROOT, goal: "Delete the build" }),
    record("run_started", "run_child", {}),
    record("approval_requested", "run_child", { approval_id: "apr_3", severity: "high", summary: "Delete the build", args: {} }),
  );
  assert.equal(asked.sessions.run_child.status, "blocked");
  assert.equal(asked.cards[0].kind, "approval");
  assert.equal(asked.cards[0].phase, "pending");
  const denied = transition(asked, record("approval_decided", "run_child", { approval_id: "apr_3", status: "denied", note: "no" }));
  assert.equal(denied.cards[0].phase, "denied");
  assert.equal(denied.sessions.run_child.status, "running");
  const expired = transition(asked, record("approval_decided", "run_child", { approval_id: "apr_3", status: "expired", note: "no decision before the deadline" }));
  assert.equal(expired.cards[0].phase, "expired");
});

test("a shown page is a report card, once", () => {
  const once = apply(emptyFlow(ROOT), record("page_shown", ROOT, { page_id: "page_1", title: "The audit" }));
  const twice = transition(once, record("page_shown", ROOT, { page_id: "page_1", title: "The audit" }));
  assert.equal(twice, once);
  assert.equal(once.cards[0].kind, "report");
  assert.equal(once.cards[0].href, "/pages/page_1");
  assert.equal(once.cards[0].title, "The audit");
});

test("a plan's steps are separate progress items, and folding the activity is one transition", () => {
  const state = apply(emptyFlow(ROOT), record("plan", ROOT, { steps: [{ text: "Read", status: "done" }, { text: "Fix", status: "in_progress" }] }));
  const shown = presentActivity(state.activity[ROOT]);
  assert.deepEqual(shown.progress, ["done: Read", "in_progress: Fix"]);
  const closed = transition(state, { type: "toggle_activity", id: ROOT });
  assert.equal(closed.activity[ROOT].fold, "closed");
  assert.equal(transition(closed, { type: "toggle_activity", id: ROOT }).activity[ROOT].fold, "open");
});

test("a step that ends without a start is still one step, failed when the tool failed", () => {
  const state = apply(emptyFlow(ROOT), record("tool_result", ROOT, { call_id: "c9", tool: "shell", ok: false }));
  assert.equal(state.activity[ROOT].steps[0].status, "failed");
});
