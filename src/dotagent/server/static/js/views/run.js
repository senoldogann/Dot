// One run as a conversation: the question, what Dot did about it, its answer, and the box to steer the run or to follow up on it.

import { api, follow } from "../api.js";
import { createAttachments, readyToSend } from "../attachments.js";
import { createDetails } from "../details.js";
import { clip, dateTimeFull, el, modelName } from "../dom.js";
import { levelLabel } from "../efforts.js";
import { continuedIn, earlierTurns, loadHistory } from "../history.js";
import { icon } from "../icons.js";
import { createInspector } from "../inspector.js";
import { userMessage } from "../messages.js";
import { emptyPanel, errorPanel, foldCard, loadingPanel, statusBadge } from "../panels.js";
import { createTimeline } from "../timeline.js";
import { attempt, toast } from "../ui.js";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
// The chips of a run that does not ask the user (the default needs none): the words and what they mean.
const APPROVAL_CHIPS = {
  auto: { text: "Auto-approve", hint: "A review model answered the questions of the safety policy in your place" },
  yolo: { text: "YOLO", hint: "Nothing was asked: what the safety policy would ask ran at once and was recorded" },
};
const NEAR_BOTTOM_PX = 140;
// "/btw" followed by the side question; the question may span lines.
const BTW_PATTERN = /^\/btw(?:\s+([\s\S]*))?$/i;
const MAX_INPUT_PX = 160;
const TITLE_CHARS = 70;
const QUOTE_CHARS = 160;

function backLink() {
  return el("a", { class: "back", href: "#/runs" }, icon("arrowLeft"), "All runs");
}

const STEP_MARKS = { done: "check", failed: "x", skipped: "arrowRight" };
const STEP_LABELS = { pending: "Not started", in_progress: "In progress", done: "Done", failed: "Failed", skipped: "Skipped" };

function planStep(step) {
  const markIcon = STEP_MARKS[step.status];
  const mark = el("span", { class: "mark" }, markIcon === undefined ? null : icon(markIcon));
  return el(
    "div",
    { class: `plan-step ${step.status}`, title: STEP_LABELS[step.status] ?? step.status },
    mark,
    el("span", { class: "text" }, step.text),
  );
}

/**
 * Build the page of a loaded run and start following it. `history` is `{ earlier, later }`, the runs of the conversation
 * around this one; `historyProblem` is the error that kept them from loading, or null. Returns the function that stops
 * everything the page started.
 */
function showRun({ root, ctx, signal, detail, history, historyProblem, reload }) {
  const runId = detail.run.id;
  let run = detail.run;
  let status = run.status;
  let model = run.model;
  let active = detail.active;
  let finishedAt = run.finished_at;
  let following = active;
  let ticker = null;
  let scrollQueued = false;
  let answerNode = null;
  // A run that is over when the page opens lands on its answer; a live one only when the reader follows it.
  const openedFinished = !detail.active;
  const seen = { steps: 0, input: 0, output: 0, calls: 0, failed: 0 };

  ctx.setTitle(clip(run.goal, TITLE_CHARS));

  // ---------------------------------------------------------------------------------------------- header
  const statusSlot = el("span", { class: "chat-status" });
  const modelChip = el("span", { class: "chip chat-model" });
  const cancelButton = el(
    "button",
    {
      class: "btn danger",
      type: "button",
      onclick: () =>
        attempt(async () => {
          await api.post(`/runs/${runId}/cancel`, null);
          toast("ok", "Cancelling the run…");
        }),
    },
    icon("stop"),
    el("span", { class: "label" }, "Cancel"),
  );
  const parentChip =
    run.parent_run_id === null ? null : el("a", { class: "chip", href: `#/runs/${run.parent_run_id}` }, icon("branch"), "Delegated by another run");
  const approvalChip = APPROVAL_CHIPS[run.approvals];
  const modeChips = [
    approvalChip === undefined
      ? null
      : el("span", { class: `chip approvals-${run.approvals}`, title: approvalChip.hint }, icon(run.approvals === "yolo" ? "bolt" : "shield"), approvalChip.text),
    run.goal_mode ? el("span", { class: "chip", title: "A checker keeps this run working until its goal is really reached" }, icon("target"), "Goal mode") : null,
  ];
  const connectionNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "Connection lost. Reconnecting…"));

  // ------------------------------------------------------------------------------------ details and plan
  const details = createDetails(run);
  const planBody = el("div", { class: "plan-list" });
  const plan = foldCard({ id: "run-plan", title: "Plan", actions: null, body: planBody, openByDefault: true });
  plan.element.hidden = true;

  function paintPlan(steps) {
    const done = steps.filter((step) => step.status === "done").length;
    plan.element.hidden = steps.length === 0;
    plan.setSummary(`${done} of ${steps.length} done`);
    planBody.replaceChildren(...steps.map(planStep));
  }

  function paintStats() {
    statusSlot.replaceChildren(statusBadge(status));
    modelChip.hidden = model === "";
    modelChip.textContent = run.effort === null ? modelName(model) : `${modelName(model)} · ${levelLabel(run.effort)}`;
    modelChip.title = model;
    details.paint({
      status,
      model,
      steps: Math.max(run.steps, seen.steps),
      calls: seen.calls,
      failed: seen.failed,
      input: Math.max(run.input_tokens, seen.input),
      output: Math.max(run.output_tokens, seen.output),
      finishedAt,
    });
  }

  // ------------------------------------------------------------------------------------------------ dock
  const input = el("textarea", { class: "textarea", rows: "1", "aria-label": "Message for Dot" });
  const sendLabel = el("span", { class: "label" });
  const send = el("button", { class: "btn primary send", type: "button" }, icon("arrowUp"), sendLabel);
  const dockInner = el("div", { class: "dock-inner" });
  const attachments = createAttachments({ dropTarget: dockInner, input });
  dockInner.append(attachments.button, attachments.picker, input, send);
  const dock = el("div", { class: "dock" }, attachments.list, dockInner);

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, MAX_INPUT_PX)}px`;
  }

  // Steer messages that are sent but not read yet; each one leaves when the run reports reading it.
  const waitingMessages = [];
  const waitingList = el("div", { class: "stack tight", hidden: true });

  function holdMessage(text) {
    const label = el("span", null, `Waiting for the next step: “${clip(text, QUOTE_CHARS)}”`);
    const node = el("div", { class: "note" }, icon("clock"), label);
    const entry = { text, node, label };
    waitingMessages.push(entry);
    waitingList.append(node);
    waitingList.hidden = false;
    return entry;
  }

  function releaseMessage(entry) {
    const at = waitingMessages.indexOf(entry);
    if (at !== -1) {
      waitingMessages.splice(at, 1);
    }
    entry.node.remove();
    waitingList.hidden = waitingList.childElementCount === 0;
  }

  function messageRead(text) {
    const entry = waitingMessages.find((candidate) => candidate.text === text);
    if (entry !== undefined) {
      releaseMessage(entry);
    }
  }

  function messagesMissed() {
    for (const entry of waitingMessages.splice(0)) {
      entry.node.className = "note warn";
      entry.label.textContent = `The run ended before Dot read: “${clip(entry.text, QUOTE_CHARS)}”`;
    }
  }

  // Side questions on their way; the answer itself arrives on the run's stream.
  function holdAside(question) {
    const node = el("div", { class: "note" }, icon("clock"), el("span", null, `Asking on the side: “${clip(question, QUOTE_CHARS)}”`));
    waitingList.append(node);
    waitingList.hidden = false;
    return () => {
      node.remove();
      waitingList.hidden = waitingList.childElementCount === 0;
    };
  }

  // "/btw <question>" asks on the side: the run keeps working and never sees the question. Several can be in flight.
  function askAside(question) {
    input.value = "";
    autosize();
    const done = holdAside(question);
    attempt(() => api.post(`/runs/${runId}/btw`, { text: question })).finally(done);
  }

  // True from the moment a message is on its way until the answer is in: Enter does not look at the disabled button,
  // so a second press must be refused here, or a follow-up would start two runs on one thread.
  let sending = false;

  async function submit() {
    if (sending) {
      return;
    }
    const text = input.value.trim();
    if (text === "") {
      input.focus();
      return;
    }
    const aside = BTW_PATTERN.exec(text);
    if (aside !== null) {
      const question = (aside[1] ?? "").trim();
      if (question === "") {
        input.focus();
        return;
      }
      askAside(question);
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    const files = attachments.ids();
    sending = true;
    send.disabled = true;
    try {
      await attempt(async () => {
        if (active) {
          const entry = holdMessage(text);
          try {
            await api.post(`/runs/${runId}/steer`, { text, attachments: files });
          } catch (error) {
            releaseMessage(entry);
            throw error;
          }
          input.value = "";
          autosize();
          attachments.clear();
          return;
        }
        const next = await api.post("/runs", {
          goal: text,
          mode: run.mode,
          model: run.model === "" ? null : run.model,
          effort: run.effort,
          approvals: run.approvals,
          goal_mode: run.goal_mode,
          thread_id: run.thread_id,
          attachments: files,
        });
        attachments.clear();
        ctx.navigate(`/runs/${next.id}`);
      });
    } finally {
      sending = false;
      send.disabled = false;
    }
  }

  send.addEventListener("click", submit);
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });

  function syncControls() {
    cancelButton.hidden = !active;
    dock.hidden = run.parent_run_id !== null;
    input.placeholder = active
      ? "Steer this run (Dot reads it before its next step), or ask on the side with /btw…"
      : "Follow up in the same conversation, or ask about this run with /btw…";
    const sendText = active ? "Steer" : "Follow up";
    sendLabel.textContent = sendText;
    send.title = sendText;
    send.setAttribute("aria-label", sendText);
    if (active && ticker === null) {
      ticker = setInterval(paintStats, 1000);
    } else if (!active && ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  // ------------------------------------------------------------------------------------------------ scroll
  const jumpLabel = el("span", null, "Jump to latest");
  const jump = el(
    "button",
    {
      class: "btn primary sm jump",
      type: "button",
      hidden: true,
      onclick: () => {
        jump.hidden = true;
        if (answerNode !== null) {
          answerNode.scrollIntoView({ block: "start", behavior: "smooth" });
          return;
        }
        following = true;
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
      },
    },
    icon("arrowDown"),
    jumpLabel,
  );

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - NEAR_BOTTOM_PX;
  }

  /** The button leads to the answer once there is one that is still below the window, else to the end of a live run. */
  function paintJump() {
    const unseen = answerNode !== null ? answerNode.getBoundingClientRect().top > window.innerHeight : active && !following;
    jump.hidden = !unseen;
    jumpLabel.textContent = answerNode !== null ? "Jump to the answer" : "Jump to latest";
  }

  function onScroll() {
    following = nearBottom();
    paintJump();
  }

  function onGrow() {
    if (!following) {
      paintJump();
      return;
    }
    if (scrollQueued) {
      return;
    }
    scrollQueued = true;
    requestAnimationFrame(() => {
      scrollQueued = false;
      if (!signal.aborted) {
        window.scrollTo({ top: document.documentElement.scrollHeight });
      }
    });
  }

  function onAnswer(node) {
    answerNode = node;
    if (following || openedFinished) {
      following = false;
      requestAnimationFrame(() => {
        if (!signal.aborted) {
          node.scrollIntoView({ block: "start" });
        }
      });
    }
  }

  async function reconcile() {
    try {
      const fresh = await api.get(`/runs/${runId}`);
      if (signal.aborted) {
        return;
      }
      run = fresh.run;
      status = run.status;
      model = run.model;
      finishedAt = run.finished_at;
      paintStats();
      syncControls();
    } catch (error) {
      console.warn("the run could not be refreshed", error);
    }
  }

  function onRunStatus(update) {
    const terminal = TERMINAL.has(update.status);
    if (!active && !terminal) {
      return;
    }
    status = update.status;
    if (update.model !== null) {
      model = update.model;
    }
    if (terminal) {
      active = false;
      messagesMissed();
      finishedAt = update.endedAt;
      syncControls();
      reconcile();
    }
    paintStats();
  }

  // ------------------------------------------------------------------------------------------ assemble
  const layout = el("div", { class: "run-layout" });
  const inspector = createInspector({ layout, content: [details.element, plan.element] });
  const timeline = createTimeline({
    rootRunId: runId,
    startedAt: run.created_at,
    onGrow,
    onPlan: paintPlan,
    onStats: ({ step, input: inputTokens, output: outputTokens }) => {
      seen.steps = Math.max(seen.steps, step);
      seen.input += inputTokens;
      seen.output += outputTokens;
      paintStats();
    },
    onCalls: ({ calls, failed }) => {
      seen.calls = calls;
      seen.failed = failed;
      paintStats();
    },
    onRunStatus,
    onAnswer,
    onImage: inspector.addImage,
  });
  const problem =
    historyProblem === null
      ? null
      : el("div", { class: "note warn" }, icon("alert"), el("span", null, `The earlier part of this conversation could not be loaded: ${historyProblem.message}`));
  const chatHead = el(
    "header",
    { class: "chat-head" },
    statusSlot,
    el("h1", { class: "run-title", title: run.goal }, run.goal),
    modelChip,
    modeChips,
    parentChip,
    inspector.toggle,
    cancelButton,
  );

  layout.append(
    el(
      "div",
      { class: "run-main" },
      backLink(),
      chatHead,
      connectionNote,
      problem,
      earlierTurns(history.earlier),
      userMessage(run.goal, dateTimeFull(run.created_at)),
      timeline.element,
      waitingList,
      continuedIn(history.later),
      dock,
    ),
    inspector.element,
    inspector.scrim,
  );
  root.replaceChildren(layout, jump);
  inspector.arrange();
  paintStats();
  syncControls();
  window.addEventListener("scroll", onScroll, { passive: true });

  follow({
    path: `/runs/${runId}/stream`,
    after: 0,
    signal,
    isFinished: (message) => message.data.kind === "run_finished" && message.data.run_id === runId,
    onState: (state) => {
      connectionNote.hidden = state !== "reconnecting";
    },
    onMessage: (message) => {
      if (message.data.kind === "steer" && message.data.run_id === runId) {
        messageRead(message.data.data.text);
      }
      timeline.push(message.data);
    },
  }).catch((error) => {
    if (signal.aborted) {
      return;
    }
    console.error("the run stream ended", error);
    if (error.status === 404) {
      root.replaceChildren(backLink(), emptyPanel("search", "Run not found", `No run has the id ${runId}.`));
      return;
    }
    toast("error", `Live updates stopped: ${error.message}`);
    root.prepend(
      el(
        "div",
        { class: "note bad" },
        icon("alert"),
        el("span", null, "Live updates stopped."),
        el("button", { class: "btn sm", type: "button", onclick: reload }, "Reload"),
      ),
    );
  });

  return () => {
    window.removeEventListener("scroll", onScroll);
    inspector.dispose();
    if (ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  };
}

export function mount(root, ctx) {
  const runId = ctx.params[0];
  const controller = new AbortController();
  let stopRun = () => undefined;

  async function open() {
    stopRun();
    stopRun = () => undefined;
    root.replaceChildren(loadingPanel());
    let detail;
    try {
      detail = await api.get(`/runs/${runId}`);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const problem = error.status === 404 ? emptyPanel("search", "Run not found", `No run has the id ${runId}.`) : errorPanel(error, () => attempt(open));
      root.replaceChildren(backLink(), problem);
      return;
    }
    let history = { earlier: [], later: [] };
    let historyProblem = null;
    try {
      history = await loadHistory(detail.run);
    } catch (error) {
      console.warn("the conversation of this run could not be listed", error);
      historyProblem = error;
    }
    if (controller.signal.aborted) {
      return;
    }
    stopRun = showRun({ root, ctx, signal: controller.signal, detail, history, historyProblem, reload: () => attempt(open) });
  }

  attempt(open);
  return () => {
    controller.abort();
    stopRun();
  };
}
