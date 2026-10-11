// One run as a conversation: the question, what Dot did about it, its answer, and the box to steer the run or to follow up on it.

import { actorFace, dotFace, setActorState } from "../actors.js";
import { api, follow } from "../api.js";
import { approvalCard, fromRecord, LEFT_IN_CAMPAIGN, LEFT_IN_RUN } from "../approvals.js";
import { readyToSend } from "../attachments.js";
import { createComputer } from "../computer.js";
import { createCostCard } from "../cost.js";
import { createDetails } from "../details.js";
import { composerFrame } from "../composer.js";
import { clip, dateTime, dateTimeFull, debounce, duration, el, modelName } from "../dom.js";
import { levelLabel } from "../efforts.js";
import { continuedIn, earlierTurns, loadHistory, RESUME_SOURCE } from "../history.js";
import { dotName, faceState, faceStateAtEnd } from "../face.js";
import { icon } from "../icons.js";
import { createCharacterCard } from "../charactercard.js";
import { createInspector } from "../inspector.js";
import { userMessage } from "../messages.js";
import { emptyPanel, errorPanel, foldCard, loadingPanel, statusBadge } from "../panels.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { createStageSize } from "../stagesize.js";
import { onLifecycle } from "../state.js";
import { createTimeline } from "../timeline.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
// A delegated run's goal in the bar and the words of its pill (item 82).
const CRUMB_CHARS = 56;
const FOOT_WORDS = { done: "Completed in", failed: "Failed after", cancelled: "Stopped after" };
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
// The events that change what became of the lessons of a run; they come on the lifecycle stream, after the run's end.
const LESSON_KINDS = new Set(["lessons_proposed", "lesson_saved", "lesson_dismissed", "lessons_failed"]);
const LESSON_STATES = { saved: { mark: "check", text: "Saved to memory" }, dismissed: { mark: "x", text: "Dismissed" } };
// A replayed run brings its question events in a burst: one refresh after it is enough.
const QUESTIONS_SETTLE_MS = 120;
// How often the cost card is read again while the run works (its numbers come from a query over the run's events).
const COST_REFRESH_MS = 10_000;
// What the Actions panel says of a file change, by the state of its undo (see undo.restore.UndoStep).
const EDIT_STATES = {
  ready: { tone: "b-ok", text: "can be taken back" },
  conflict: { tone: "b-warn", text: "changed since" },
  cannot: { tone: "b-bad", text: "cannot be taken back" },
  as_before: { tone: "b-muted", text: "as before the run" },
};
const APPROVAL_TONES = { approved: "b-ok", denied: "b-bad", expired: "b-muted", pending: "b-warn", left: "b-warn" };

function backLink() {
  return el("a", { class: "back", href: "#/runs" }, icon("arrowLeft"), "All runs");
}

const STEP_MARKS = { done: "check", failed: "x", skipped: "arrowRight" };
const STEP_LABELS = { pending: "Not started", in_progress: "In progress", done: "Done", failed: "Failed", skipped: "Skipped" };

/** The targets of an open-ended goal, the first line of the plan: what the run works toward and how it measures it. */
function planTargets(targets) {
  return el("div", { class: "plan-targets", title: "Targets" }, icon("target"), el("span", { class: "text" }, targets));
}

/** One step of the plan: its mark, its text, the steps it waits for and, under a closed step, the evidence the run
 * recorded for it. Plans stored before evidence existed have none. */
function planStep(step) {
  const markIcon = STEP_MARKS[step.status];
  const mark = el("span", { class: "mark" }, markIcon === undefined ? null : icon(markIcon));
  const evidence = step.evidence ?? "";
  // Plans stored before dependencies existed have none.
  const dependsOn = step.depends_on ?? [];
  const line = el("span", { class: "line" }, el("span", { class: "text" }, step.text));
  if (dependsOn.length > 0) {
    line.append(el("span", { class: "after", title: "Waits for these steps" }, `after ${dependsOn.join(", ")}`));
  }
  const body = el("span", { class: "body" }, line);
  if (evidence !== "") body.append(el("span", { class: "evidence", title: "Evidence" }, evidence));
  return el("div", { class: `plan-step ${step.status}`, title: STEP_LABELS[step.status] ?? step.status }, mark, body);
}

/**
 * Build the page of a loaded run and start following it. `history` is `{ earlier, later }`, the runs of the conversation
 * around this one; `historyProblem` is the error that kept them from loading, or null. Returns the function that stops
 * everything the page started.
 */
function actionStep(step) {
  return el(
    "div",
    { class: "list-row action-step", "data-ok": String(step.ok) },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, step.summary === "" ? step.tool : step.summary),
      step.evidence === "" ? null : el("div", { class: "row-text small muted" }, step.evidence),
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: "badge b-info" }, step.channel),
        el("span", null, step.tool),
        step.target === "" ? null : el("span", { class: "mono" }, step.target),
        el("span", { class: `badge ${step.ok ? "b-ok" : "b-bad"}` }, step.ok ? "ok" : "failed"),
        el("span", null, dateTimeFull(step.at)),
      ),
    ),
  );
}

function approvalRow(record) {
  return el(
    "div",
    { class: "list-row action-approval" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, record.summary),
      record.note === "" ? null : el("div", { class: "row-text small muted" }, record.note),
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: `badge ${APPROVAL_TONES[record.status] ?? "b-muted"}` }, record.status),
        el("span", null, record.tool),
        record.decided_by === null ? null : el("span", null, `by ${record.decided_by}`),
        el("span", null, dateTimeFull(record.created_at)),
      ),
    ),
  );
}

function showRun({ root, ctx, signal, detail, history, historyProblem, reload }) {
  const runId = detail.run.id;
  let run = detail.run;
  let status = run.status;
  // Dot's face on Dot's own run; a delegated run wears the face of its own hue (item 83); a run of an agent wears the
  // agent's (item 86).
  const agent = detail.agent;
  const face =
    agent !== null ? actorFace(agent.id, faceState(status), agent.look) : run.parent_run_id === null ? dotFace(faceState(status)) : actorFace(run.id, faceState(status), null);
  let model = run.model;
  let active = detail.active;
  // The daemon cut the run off and a new run can go on from where it stopped.
  let resumable = detail.resumable;
  let finishedAt = run.finished_at;
  let following = active;
  let ticker = null;
  let costTicker = null;
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
  const resumeButton = el(
    "button",
    {
      class: "btn primary",
      type: "button",
      title: "Start a run on this conversation that goes on from where the daemon cut this one off",
      onclick: () =>
        attempt(async () => {
          const next = await api.post(`/runs/${runId}/resume`, null);
          toast("ok", "Resuming the run…");
          ctx.navigate(`/runs/${next.id}`);
        }),
    },
    icon("arrowRight"),
    el("span", { class: "label" }, "Continue"),
  );
  // A delegated run (item 82) names its parent in the bar as a breadcrumb, the parent's goal a link before its own, and
  // closes its conversation with a pill: the model, how long it took, whether its parent still works, the way back.
  const parentId = run.parent_run_id;
  const crumb = parentId === null ? null : el("a", { class: "stage-crumb", href: `#/runs/${parentId}` }, "Parent run");
  const crumbSep = parentId === null ? null : el("span", { class: "stage-sep", "aria-hidden": "true" }, "/");
  const stageName = el("span", { class: "stage-name" }, parentId !== null ? clip(run.goal, CRUMB_CHARS) : agent !== null ? agent.name : dotName());
  const footTime = el("span", null);
  const footParent = el("span", null, "Part of another run");
  const foot =
    parentId === null
      ? null
      : el(
          "div",
          { class: "child-foot-row" },
          el(
            "div",
            { class: "child-foot meta-facts" },
            el("span", null, run.effort === null ? modelName(run.model) : `${modelName(run.model)} ${levelLabel(run.effort)}`),
            APPROVAL_CHIPS[run.approvals] === undefined ? null : el("span", null, APPROVAL_CHIPS[run.approvals].text),
            footTime,
            footParent,
            el("a", { href: `#/runs/${parentId}` }, "Open parent"),
          ),
        );

  /** The pill's time: how long the run has worked, or took, as it stands now. */
  function paintFoot() {
    if (foot === null) {
      return;
    }
    const seconds = Math.max((finishedAt ?? Date.now() / 1000) - run.created_at, 0);
    footTime.textContent = `${FOOT_WORDS[status] ?? "Working for"} ${duration(seconds)}`;
  }

  if (parentId !== null) {
    api
      .get(`/runs/${parentId}`)
      .then((parent) => {
        if (signal.aborted) {
          return;
        }
        crumb.textContent = clip(parent.run.goal, CRUMB_CHARS);
        crumb.title = parent.run.goal;
        footParent.textContent = parent.active ? "Part of a working run" : "Runs on its own";
      })
      .catch((error) => console.warn("the parent run could not be loaded", error));
  }
  const campaign = detail.campaign;
  const campaignChip =
    campaign === null
      ? null
      : el(
          "a",
          { class: "chip", href: `#/campaigns/${campaign.id}`, title: `Session ${campaign.session} of the campaign “${campaign.title}”` },
          icon("target"),
          `Campaign · session ${campaign.session}`,
        );
  const agentChip =
    agent === null ? null : el("a", { class: "chip", href: `#/agents/${agent.id}`, title: `A chat with ${agent.name}: ${agent.role}` }, icon("user"), agent.name);
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
  const plan = foldCard({ id: "run-plan", title: "Plan", actions: null, body: planBody, openByDefault: false });
  plan.element.hidden = true;

  function paintPlan(steps, targets) {
    const done = steps.filter((step) => step.status === "done").length;
    plan.element.hidden = steps.length === 0;
    plan.setSummary(`${done} of ${steps.length} done`);
    // The native replaceChildren writes a null as the text "null", so a plan without targets passes nothing for them.
    planBody.replaceChildren(...(targets === "" ? [] : [planTargets(targets)]), ...steps.map(planStep));
  }

  function paintStats() {
    paintFoot();
    statusSlot.replaceChildren(statusBadge(status));
    // The tick of a run that just ended stays for its moment; every other state follows the status.
    if (face.getAttribute("data-state") !== "done") {
      setActorState(face, faceState(status));
    }
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

  // ------------------------------------------------------------------------------------------------ cost
  // What the run cost and where (see cost.js): read on arrival, every COST_REFRESH_MS while it works, once at its end.
  const cost = createCostCard();

  async function paintCost() {
    try {
      const costs = await api.get(`/runs/${runId}/stats`);
      if (!signal.aborted) {
        cost.paint(costs);
      }
    } catch (error) {
      console.warn("the cost of the run could not be read", error);
    }
  }

  // --------------------------------------------------------------------------------------------- lessons
  // What a run that reached its goal the hard way can tell the runs after it, drawn once it ended (see agent.lessons).
  const lessonsBody = el("div", { class: "lessons" });
  const lessonsCard = el(
    "section",
    { class: "card", hidden: true, "aria-label": "Lessons from this run" },
    el("div", { class: "card-head" }, icon("sparkle"), el("span", null, "Lessons from this run")),
    lessonsBody,
  );
  // The lessons being saved or dismissed, by index, so that a second press does not send the same decision twice.
  const decidingLessons = new Set();

  async function decideLesson(index, action) {
    if (decidingLessons.has(index)) {
      return;
    }
    decidingLessons.add(index);
    try {
      await attempt(async () => {
        await api.post(`/runs/${runId}/lessons/${index}/${action}`, null);
        toast("ok", action === "save" ? "Saved to memory: later runs find it like any note." : "The lesson is dismissed.");
      });
      // Decided now, or decided elsewhere meanwhile (another page, `auto`): show what became of every lesson.
      await reconcile();
    } finally {
      decidingLessons.delete(index);
    }
  }

  function lessonRow(lesson) {
    const text = el("span", { class: "lesson-text" }, lesson.text);
    const settled = LESSON_STATES[lesson.state];
    if (settled !== undefined) {
      return el("div", { class: `lesson ${lesson.state}` }, text, el("span", { class: "chip" }, icon(settled.mark), settled.text));
    }
    // The words sit in the buttons themselves, as on an approval card, so that a phone keeps them.
    const save = el("button", { class: "btn primary sm", type: "button", onclick: () => decideLesson(lesson.index, "save") }, icon("check"), "Save to memory");
    const dismiss = el("button", { class: "btn sm", type: "button", onclick: () => decideLesson(lesson.index, "dismiss") }, "Dismiss");
    return el("div", { class: "lesson" }, text, el("div", { class: "cluster" }, save, dismiss));
  }

  /** `lessons` is what became of the lessons of the run, or null when none were drawn from it. */
  function paintLessons(lessons) {
    lessonsCard.hidden = lessons === null;
    if (lessons === null) {
      return;
    }
    const proposed = lessons.lessons.some((lesson) => lesson.state === "proposed");
    lessonsBody.replaceChildren(
      ...lessons.lessons.map(lessonRow),
      ...[
        lessons.tainted && proposed
          ? el(
              "div",
              { class: "note warn" },
              icon("alert"),
              el("span", null, "This run read content that others can write (web pages, mail, downloads), so its lessons are never saved on their own. Read each one before you save it."),
            )
          : null,
        lessons.error === null ? null : el("div", { class: "note bad" }, icon("alert"), el("span", null, lessons.error)),
      ].filter((node) => node !== null),
    );
  }

  // ------------------------------------------------------------------------------------------ questions
  // The questions the run left for you and went on without, while they are open; an answer carries the work on in
  // this conversation: into this run while it works, else into a new run on the thread, which the page opens once no
  // other question of this run is open here. A skip, or a decision the daemon refused, only refreshes the page.
  const questionsList = el("div", { class: "stack tight", hidden: true, "aria-label": "Dot asked you" });
  const questionCards = new Map();

  async function questionDecided(decided) {
    const lastAnswered = decided !== null && decided.status === "approved" && questionCards.size === 1;
    if (lastAnswered && !active) {
      const [newest] = await api.get(`/runs?thread_id=${encodeURIComponent(run.thread_id)}&limit=1`);
      if (newest !== undefined && newest.id !== runId) {
        ctx.navigate(`/runs/${newest.id}`);
        return;
      }
    }
    await reconcile();
  }

  /** `questions` are the stored questions the run left that are still open; a card that is there already stays as it is. */
  function paintQuestions(questions) {
    const open = new Set(questions.map((question) => question.id));
    for (const [id, node] of questionCards) {
      if (!open.has(id)) {
        node.remove();
        questionCards.delete(id);
      }
    }
    for (const question of questions) {
      if (!questionCards.has(question.id)) {
        const card = approvalCard({
          approval: fromRecord(question),
          heading: null,
          onSettled: (decided) => attempt(() => questionDecided(decided)),
          leftNote: detail.campaign === null ? LEFT_IN_RUN : LEFT_IN_CAMPAIGN,
        });
        questionCards.set(question.id, card.node);
        questionsList.append(card.node);
      }
    }
    questionsList.hidden = questionCards.size === 0;
  }

  // ------------------------------------------------------------------------------------------------ dock
  // The shared frame (composer.js): Enter sends and Shift+Enter breaks a line; "/" opens the slash menu, whose one
  // command here asks on the side; what is typed and not sent is kept for this run.
  const frame = composerFrame({
    label: "Message",
    boxLabel: "Message for Dot",
    placeholder: "",
    rows: 1,
    maxPx: MAX_INPUT_PX,
    commands: [{ label: "/btw", hint: "Ask on the side: the run never sees the question.", insert: "/btw " }],
    send: () => submit(),
    sendLabel: "Steer",
    draftKey: runId,
    listener: null,
  });
  const { textarea: input, attachments, sendButton: send } = frame;
  const dock = el("div", { class: "dock" }, frame.element);

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
  // The tokens of the answer count in the run's totals, read again once it is in (a finished run streams nothing more).
  function askAside(question) {
    input.value = "";
    frame.autosize();
    const done = holdAside(question);
    attempt(() => api.post(`/runs/${runId}/btw`, { text: question })).finally(() => {
      done();
      reconcile();
    });
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
          frame.clear();
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
        frame.clear();
        ctx.navigate(`/runs/${next.id}`);
      });
    } finally {
      sending = false;
      send.disabled = false;
    }
  }

  send.addEventListener("click", submit);

  function syncControls() {
    cancelButton.hidden = !active;
    resumeButton.hidden = active || !resumable;
    // A delegated run is not steered: its dock keeps only the arrow down (item 96).
    const delegated = run.parent_run_id !== null;
    frame.element.hidden = delegated;
    dock.classList.toggle("dock-bare", delegated);
    // Short enough for one line of a phone's composer (item 111).
    input.placeholder = active ? "Steer this run, or /btw to ask on the side" : "Follow up, or /btw to ask on the side";
    frame.setSendLabel(active ? "Steer" : "Follow up");
    if (active && ticker === null) {
      ticker = setInterval(paintStats, 1000);
      costTicker = setInterval(paintCost, COST_REFRESH_MS);
    } else if (!active && ticker !== null) {
      clearInterval(ticker);
      clearInterval(costTicker);
      ticker = null;
      costTicker = null;
    }
  }

  // ------------------------------------------------------------------------------------------------ scroll
  // The way down (item 96): a round arrow just above the composer while the end, or the answer, is below the window.
  const jump = el(
    "button",
    {
      class: "btn icon-only jump",
      type: "button",
      hidden: true,
      title: "Jump to latest",
      "aria-label": "Jump to latest",
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
  );
  dock.prepend(jump);

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - NEAR_BOTTOM_PX;
  }

  /** The button leads to the answer once there is one that is still below the window, else to the end of a live run. */
  function paintJump() {
    const unseen = answerNode !== null ? answerNode.getBoundingClientRect().top > window.innerHeight : active && !following;
    jump.hidden = !unseen;
    const words = answerNode !== null ? "Jump to the answer" : "Jump to latest";
    jump.title = words;
    jump.setAttribute("aria-label", words);
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

  // Only the newest refresh paints: lesson events come in bursts, and an older answer that arrives last would put back
  // what changed since.
  let refreshes = 0;

  async function reconcile() {
    refreshes += 1;
    const mine = refreshes;
    try {
      const fresh = await api.get(`/runs/${runId}`);
      if (signal.aborted || mine !== refreshes) {
        return;
      }
      run = fresh.run;
      resumable = fresh.resumable;
      status = run.status;
      model = run.model;
      finishedAt = run.finished_at;
      paintStats();
      syncControls();
      paintCost();
      paintLessons(fresh.lessons);
      paintQuestions(fresh.questions);
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
    character.onStatus(status, update.endedAt);
    if (update.model !== null) {
      model = update.model;
    }
    if (terminal) {
      // The tick is for an end that happens under the reader's eyes; a replayed end of a run opened later rests.
      if (active) {
        setActorState(face, faceStateAtEnd(update.status));
      }
      active = false;
      computer.setActive(false);
      messagesMissed();
      finishedAt = update.endedAt;
      syncControls();
      reconcile();
      loadActions();
    }
    paintStats();
  }

  // ------------------------------------------------------------------------------------------ assemble
  const layout = el("div", { class: "run-layout" });
  // ------------------------------------------------------------------------------------------- actions
  // What the run did and what can be undone: its acting steps, its approvals and its file changes (see server.actions).
  const actionsBody = el("div", { class: "actions-list" });
  const actionsCard = foldCard({ id: "run-actions", title: "Actions", actions: null, body: actionsBody, openByDefault: false });
  // The paths being taken back, so that a second press does not send the same undo twice.
  const undoing = new Set();

  async function undoEdit(edit) {
    if (undoing.has(edit.path)) {
      return;
    }
    const confirmed = await confirmDialog({
      title: edit.action === "restore" ? "Put the file back as it was?" : "Delete the file the run created?",
      message: edit.path,
      confirmLabel: edit.action === "restore" ? "Restore" : "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    undoing.add(edit.path);
    try {
      await attempt(async () => {
        const fresh = await api.post(`/runs/${runId}/undo`, { path: edit.path });
        toast("ok", edit.action === "restore" ? "The file is back as it was." : "The file is deleted.");
        paintActions(fresh);
      });
    } finally {
      undoing.delete(edit.path);
    }
  }

  function editRow(edit, ended) {
    const state = EDIT_STATES[edit.state] ?? { tone: "b-muted", text: edit.state };
    const button =
      edit.state === "ready" && ended
        ? el(
            "button",
            { class: "btn sm", type: "button", title: `Take back the change to ${edit.path}`, onclick: () => undoEdit(edit) },
            icon("undo"),
            el("span", { class: "label" }, edit.action === "restore" ? "Restore" : "Delete"),
          )
        : null;
    return el(
      "div",
      { class: "list-row action-edit", "data-state": edit.state },
      el(
        "div",
        { class: "row-main" },
        el("div", { class: "row-title mono" }, edit.path),
        edit.reason === null ? null : el("div", { class: "row-text small muted" }, edit.reason),
        el(
          "div",
          { class: "row-meta" },
          el("span", { class: `badge ${state.tone}` }, state.text),
          el("span", null, edit.tool),
          el("span", null, edit.changes === 1 ? "1 change" : `${edit.changes} changes`),
          el("span", null, dateTimeFull(edit.noted_at)),
        ),
      ),
      button === null ? null : el("div", { class: "row-actions" }, button),
    );
  }

  function group(title, rows, emptyText) {
    return el(
      "div",
      { class: "actions-group" },
      el("div", { class: "small muted actions-group-title" }, title),
      rows.length === 0 ? el("div", { class: "hint" }, emptyText) : el("div", { class: "list" }, ...rows),
    );
  }

  function paintActions(view) {
    const undoable = view.edits.filter((edit) => edit.state === "ready").length;
    actionsCard.setSummary(
      `${view.steps.length} acting, ${view.approvals.length} asked, ${view.edits.length} files` +
        (undoable === 0 ? "" : ` (${undoable} can be taken back)`),
    );
    actionsBody.replaceChildren(
      group("Acting steps", view.steps.map(actionStep), "No step changed anything."),
      group("Approvals and questions", view.approvals.map(approvalRow), "Nothing was asked."),
      group(
        "Files changed",
        view.edits.map((edit) => editRow(edit, view.ended)),
        "No file was changed with the file tools (what the shell, the browser or the desktop changed is not noted).",
      ),
      ...(view.ended || view.edits.length === 0 ? [] : [el("div", { class: "hint" }, "The buttons to take a change back come once the run has ended.")]),
    );
  }

  async function loadActions() {
    try {
      const view = await api.get(`/runs/${runId}/actions`);
      if (!signal.aborted) {
        paintActions(view);
      }
    } catch (error) {
      if (!signal.aborted) {
        actionsBody.replaceChildren(el("div", { class: "note warn" }, icon("alert"), el("span", null, `The actions could not be loaded: ${error.message}`)));
      }
    }
  }

  const actorName = agent !== null ? agent.name : dotName();
  // The width of the panel (item 109): chosen on Dot's computer or dragged on the handle beside the panel.
  const stage = createStageSize({ layout, onChange: () => placeCharacter() });
  const computer = createComputer({ runId, active: detail.active, held: detail.held, name: actorName, sizes: stage.chooser });
  // The character beside the chat (item 103): the run's agent, moving with the run, with its name and the word of its
  // state; at the head of the panel, or at the head of the conversation while the panel is wider than its own width.
  const character = createCharacterCard({
    id: agent !== null ? agent.id : run.parent_run_id === null ? "dot" : run.id,
    name: actorName,
    look: agent !== null ? agent.look : null,
    status,
    endedAt: run.finished_at,
    size: 160,
  });
  const chatHead = el("div", { class: "run-chat-head", hidden: true });
  function placeCharacter() {
    const atHead = layout.dataset.inspector === "on" && layout.dataset.stage !== "compact";
    if (atHead && character.element.parentElement !== chatHead) {
      chatHead.append(character.element);
    } else if (!atHead && character.element.parentElement === chatHead) {
      computer.element.before(character.element);
    }
    chatHead.hidden = !atHead;
    character.element.dataset.place = atHead ? "chat" : "panel";
  }
  // The side panel (item 84): the character, the computer, the details, the cost (item 96), the plan and the actions.
  const inspector = createInspector({
    layout,
    content: [character.element, computer.element, details.element, cost.element, plan.element, actionsCard.element],
    onArrange: placeCharacter,
  });
  const timeline = createTimeline({
    rootRunId: runId,
    actorId: agent !== null ? agent.id : run.parent_run_id === null ? "dot" : run.id,
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
    onQuestionLeft: debounce(() => reconcile(), QUESTIONS_SETTLE_MS),
    onHeld: computer.setHeld,
    onBrowserStep: (page) => {
      computer.notePage(page);
      character.lookAside();
    },
  });
  const problem =
    historyProblem === null
      ? null
      : el("div", { class: "note warn" }, icon("alert"), el("span", null, `The earlier part of this conversation could not be loaded: ${historyProblem.message}`));
  // Dot's face, name, the run's state and its start name the page in the top bar (item 78); the run's chips and
  // buttons are the bar's tools, the chips waiting in the side panel's Details card on a phone. The goal, the first
  // message, is the page's heading for a reader of the structure.
  setStageTitle(face, crumb, crumbSep, stageName, el("span", { class: "stage-sub" }, statusSlot, el("span", { class: "stage-time" }, dateTime(run.created_at))));
  // A delegated run's chips are in its pill and in the Details card; its bar keeps the room for the breadcrumb.
  setStageTools(...(parentId === null ? [agentChip, modelChip, ...modeChips, campaignChip] : []), inspector.toggle, resumeButton, cancelButton);

  layout.append(
    el(
      "div",
      { class: "run-main" },
      el("h1", { class: "visually-hidden" }, run.goal),
      chatHead,
      connectionNote,
      problem,
      earlierTurns(history.earlier),
      // A resumed run's goal is the one of the run above it; its timeline opens with how it resumed that run.
      run.source === RESUME_SOURCE ? null : userMessage(run.goal, dateTimeFull(run.created_at)),
      timeline.element,
      waitingList,
      questionsList,
      lessonsCard,
      continuedIn(history.later),
      dock,
      foot,
    ),
    stage.handle,
    inspector.element,
    inspector.scrim,
  );
  root.replaceChildren(layout);
  stage.paint();
  inspector.arrange();
  paintStats();
  paintCost();
  syncControls();
  paintLessons(detail.lessons);
  paintQuestions(detail.questions);
  loadActions();
  window.addEventListener("scroll", onScroll, { passive: true });
  // Lessons change after the run, and a question may be answered or skipped in another tab.
  const stopLifecycle = onLifecycle((event) => {
    if ((LESSON_KINDS.has(event.kind) || event.kind === "approval_decided") && event.run_id === runId) {
      reconcile();
    }
  });

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
      root.replaceChildren(backLink(), emptyPanel("Run not found", `No run has the id ${runId}.`));
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
    stopLifecycle();
    computer.dispose();
    inspector.dispose();
    stage.dispose();
    character.dispose();
    frame.dispose();
    if (ticker !== null) {
      clearInterval(ticker);
      clearInterval(costTicker);
      ticker = null;
      costTicker = null;
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
      const problem = error.status === 404 ? emptyPanel("Run not found", `No run has the id ${runId}.`) : errorPanel(error, () => attempt(open));
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
