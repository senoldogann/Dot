// One run as a conversation: the question, what Dot did about it, its answer, and the box to queue a message for after
// the run, steer it or follow up on it.

import { actorFace, dotFace, setActorState } from "../actors.js";
import { agentList } from "../agentlist.js";
import { api, follow, QUIET_BREAK_MS } from "../api.js";
import { BUSY_SEND, valueOf } from "../appearance/settings.js";
import { approvalCard, fromRecord, LEFT_IN_CAMPAIGN, LEFT_IN_RUN } from "../approvals.js";
import { askForWords, readyToSend } from "../attachments.js";
import { backgroundWork } from "../background.js";
import { createCapsule } from "../capsule.js";
import { runChoices } from "../choices.js";
import { createComputer } from "../computer.js";
import { createCostCard } from "../cost.js";
import { createDetails } from "../details.js";
import { composerFrame, keepDraft, NEW_CHAT_DRAFT } from "../composer.js";
import { createContextMeter } from "../contextmeter.js";
import { clip, dateTimeFull, debounce, duration, el, FILE_BUTTONS, fileSource, modelName, OPEN_FILE_EVENT, plural, requestFile, showAfter } from "../dom.js";
import { levelLabel } from "../efforts.js";
import { BACKGROUND_SOURCE, backgroundNote, continuedIn, earlierTurns, loadHistory, RESUME_SOURCE } from "../history.js";
import { dotName, faceState, faceStateAtEnd } from "../face.js";
import { icon } from "../icons.js";
import { createInspector } from "../inspector.js";
import { createQueue } from "../queue.js";
import { showMessageFiles, userMessage } from "../messages.js";
import { messageOutline } from "../outline.js";
import { emptyPanel, errorPanel, loadingPanel, panelSection } from "../panels.js";
import { heldChoice } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { createStageSize } from "../stagesize.js";
import { getSnapshot, onChange, onLifecycle, onStreamBack, readFailed } from "../state.js";
import { createTimeline } from "../timeline.js";
import { attempt, choiceDialog, confirmDialog, toast } from "../ui.js";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
// A delegated run's goal in the bar and the words of its pill (item 82).
const CRUMB_CHARS = 56;
const FOOT_WORDS = { done: "Completed in", failed: "Failed after", cancelled: "Stopped after" };
// The words of a delegated run's pill for a run that does not ask the user (the default needs none).
const APPROVAL_WORDS = { auto: "Auto-approve", yolo: "YOLO" };
const NEAR_BOTTOM_PX = 140;
// "/btw" followed by the side question; the question may span lines.
const BTW_PATTERN = /^\/btw(?:\s+([\s\S]*))?$/i;
const MAX_INPUT_PX = 160;
const TITLE_CHARS = 70;
const QUOTE_CHARS = 160;
// What cut a run off (agent/resume.py `CutOff`), as the line that offers to resume it says it (item 299).
const CUT_OFF_LINES = {
  daemon_restarted: "Dot restarted while this run worked.",
  daemon_shutdown: "Dot was shut down while this run worked.",
  process_ended: "Its dotx command was told to end while this run worked.",
  process_died: "Its dotx command died while this run worked.",
};
// Edit from here (item 282), worded as T3 Code words it: the dialog's line and the owner's two ways of rewinding.
const REWIND_MESSAGE = "Rewind chat to before this message. Your prompt and attachments return to the composer.";
const REWIND_CHOICES = [
  { value: "revert", label: "Revert files too", tone: "danger" },
  { value: "keep", label: "Revert and keep changes", tone: "primary" },
];
// The events that change what became of the lessons of a run; they come on the lifecycle stream, after the run's end.
const LESSON_KINDS = new Set(["lessons_proposed", "lesson_saved", "lesson_dismissed", "lessons_failed"]);
// What a decision on a lesson says once it is made.
const LESSON_DONE = { save: "Saved to memory: later runs find it like any note.", dismiss: "The lesson is dismissed.", undo: "Undone: the lesson is no longer in memory." };
// A replayed run brings its question events in a burst: one refresh after it is enough.
const QUESTIONS_SETTLE_MS = 120;
// The conversation changes with every event of a run: the list of its files is drawn again once it settles.
const FILES_SETTLE_MS = 250;
// A helper that starts or ends reads the chat's agents again (item 227), once a burst of its events has settled.
const AGENTS_SETTLE_MS = 250;
// The events of a run that start or end it.
const RUN_ENDS = new Set(["run_queued", "run_finished"]);
// The moments of a helper the lifecycle stream tells: the rows of the helpers are read from the store once a burst of
// them has settled, whatever the run's own stream brought (item 285).
const HELPER_MOMENTS = new Set(["run_queued", "run_started", "run_finished"]);
const HELPERS_SETTLE_MS = 1500;
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

/** What a rewind did (item 282), said in a toast, and the files it left as they are, with why, in one of its own. */
function tellRewound(cut) {
  const taken = cut.reverted.length === 0 ? "" : `, and ${plural(cut.reverted.length, "file is as it was", "files are as they were")}`;
  toast("ok", `The chat went back to before your message${taken}.`);
  if (cut.left.length > 0) {
    const named = cut.left.map((edit) => `${edit.path} (${edit.reason})`).join("; ");
    toast("error", `${plural(cut.left.length, "file was", "files were")} left as they are: ${named}`);
  }
}

function backLink() {
  return el("a", { class: "back", href: "#/runs" }, icon("arrowLeft"), "All runs");
}

// What the Plan tab says of a run without a plan (item 203).
const NO_PLAN = "This run has no plan: Dot writes one for work of several steps.";
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
 * Build the page of a loaded run and start following it. `history` is `{ earlier, later }`, the runs of the
 * conversation around this one; `historyProblem` is the error that kept them from loading, or null. Returns the
 * function that stops everything the page started.
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
  // A run that a dotx command drives (`elsewhere`) works here too: its page follows it and its Stop asks that
  // command to stop it, but it takes no steer, and its browser is not shown or taken over from here.
  const elsewhere = detail.elsewhere;
  let active = detail.active || elsewhere;
  // The run was cut off (by the daemon, or by the end of its dotx command) and a new run can go on from where it
  // stopped; `cutOff` is how.
  let resumable = detail.resumable;
  let cutOff = detail.cut_off;
  let finishedAt = run.finished_at;
  let following = active;
  let ticker = null;
  let costTicker = null;
  let scrollQueued = false;
  let answerNode = null;
  // From the answer's coming until the frame that lands the page on it, the way down is not offered: the page is about
  // to move there (item 274).
  let landing = false;
  // A run that is over when the page opens lands on its answer; a live one only when the reader follows it.
  const openedFinished = !active;
  const seen = { steps: 0, input: 0, output: 0, calls: 0, failed: 0, compactions: 0, flagged: 0 };

  ctx.setTitle(clip(run.goal, TITLE_CHARS));

  // ---------------------------------------------------------------------------------------------- header
  // The run is stopped from the composer's send while the box is empty, or from a delegated run's pill (item 120).
  function stopRun() {
    attempt(async () => {
      await api.post(`/runs/${runId}/cancel`, null);
      toast("ok", "Cancelling the run…");
    });
  }
  const resumeButton = el(
    "button",
    {
      class: "btn primary sm",
      type: "button",
      title: "Start a run on this conversation that goes on from where this one was cut off",
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
  // Continue, for a run that was cut off, is a line above the composer (item 120) with the bolt of a restart and what
  // cut the run off (item 299).
  const resumeWords = el("span", { class: "grow" });
  const resumeLine = el("div", { class: "note resume-line restart-note", hidden: true }, icon("bolt"), resumeWords, resumeButton);
  // A delegated run (item 82) names its parent in the bar as a breadcrumb, the parent's goal a link before its own, and
  // has a pill where the composer would be: the model, how long it took, whether its parent still works, the way back.
  // The pill is in the dock, so it stays at the window's foot as the conversation scrolls (item 244).
  const parentId = run.parent_run_id;
  const crumb = parentId === null ? null : el("a", { class: "stage-crumb", href: `#/runs/${parentId}` }, "Parent run");
  const crumbSep = parentId === null ? null : el("span", { class: "stage-sep", "aria-hidden": "true" }, "/");
  const stageName = el("span", { class: "stage-name" }, parentId !== null ? clip(run.goal, CRUMB_CHARS) : agent !== null ? agent.name : dotName());
  const footTime = el("span", null);
  const footParent = el("span", null, "Part of another run");
  const footStop = el(
    "button",
    { class: "child-stop", type: "button", title: "Stop this run", hidden: true, onclick: stopRun },
    icon("stop"),
    el("span", null, "Stop"),
  );
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
            APPROVAL_WORDS[run.approvals] === undefined ? null : el("span", null, APPROVAL_WORDS[run.approvals]),
            footTime,
            footParent,
            el("a", { href: `#/runs/${parentId}` }, "Open parent"),
            footStop,
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
  const connectionNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "Connection lost. Reconnecting…"));
  // A break that mends at once, as when the page comes back into view, says nothing (item 286).
  const showConnectionNote = showAfter(connectionNote, QUIET_BREAK_MS);

  // ------------------------------------------------------------------------------------ details and plan
  const details = createDetails(run, agent, detail.campaign, detail.keep_going);
  const planBody = el("div", { class: "plan-list" }, el("div", { class: "hint" }, NO_PLAN));
  const plan = panelSection({ id: "run-plan", title: "Plan", actions: null, body: planBody });

  // The Plan tab says how far the plan has come (item 203); a run without a plan says why there is none.
  function paintPlan(steps, targets) {
    const done = steps.filter((step) => step.status === "done").length;
    inspector.setCount("plan", steps.length === 0 ? "" : `${done}/${steps.length}`);
    if (steps.length === 0) {
      planBody.replaceChildren(el("div", { class: "hint" }, NO_PLAN));
      return;
    }
    // The native replaceChildren writes a null as the text "null", so a plan without targets passes nothing for them.
    planBody.replaceChildren(...(targets === "" ? [] : [planTargets(targets)]), ...steps.map(planStep));
  }

  function paintStats() {
    paintFoot();
    // The tick of a run that just ended stays for its moment; every other state follows the status.
    if (face.getAttribute("data-state") !== "done") {
      setActorState(face, faceState(status));
    }
    details.paint({
      status,
      model,
      steps: Math.max(run.steps, seen.steps),
      calls: seen.calls,
      failed: seen.failed,
      compactions: seen.compactions,
      flagged: seen.flagged,
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
        toast("ok", LESSON_DONE[action]);
      });
      // Decided now, or decided elsewhere meanwhile (another page, `auto`): show what became of every lesson.
      await reconcile();
    } finally {
      decidingLessons.delete(index);
    }
  }

  function lessonButton(lesson, action, words) {
    return el("button", { class: "btn sm", type: "button", onclick: () => decideLesson(lesson.index, action) }, words);
  }

  function lessonRow(lesson) {
    const text = el("span", { class: "lesson-text" }, lesson.text);
    if (lesson.state === "saved") {
      // Saved on its own or by you, a lesson can be taken back: Undo forgets the note it wrote.
      const said = el("span", { class: "chip" }, icon("check"), lesson.by === "auto" ? "Saved on its own" : "Saved to memory");
      return el("div", { class: "lesson saved" }, text, el("div", { class: "cluster" }, said, lessonButton(lesson, "undo", "Undo")));
    }
    if (lesson.state === "dismissed" && lesson.by === "auto") {
      // Left out by the second look, with why; it is still yours to keep.
      const why = el("span", { class: "lesson-why" }, `Left out: ${lesson.reason}`);
      return el("div", { class: "lesson dismissed" }, text, why, el("div", { class: "cluster" }, lessonButton(lesson, "save", "Save anyway")));
    }
    if (lesson.state === "dismissed") {
      return el("div", { class: "lesson dismissed" }, text, el("span", { class: "chip" }, icon("x"), "Dismissed"));
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
              el("span", null, "This run read content that others can write (web pages, mail, downloads): read each lesson before you save it."),
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
  // The shared frame (composer.js): Enter or ⌘Enter sends, as Settings › General chooses; "/" opens the slash menu,
  // whose one command here asks on the side; what is typed and not sent is kept for this run.
  const frame = composerFrame({
    label: "Message",
    boxLabel: `Message for ${agent !== null ? agent.name : dotName()}`,
    placeholder: "",
    maxPx: MAX_INPUT_PX,
    commands: [{ label: "/btw", hint: "Ask on the side: the run never sees the question.", insert: "/btw " }],
    send: () => submit(false),
    sendLabel: "Steer",
    draftKey: runId,
    listener: null,
  });
  const { textarea: input, attachments, sendButton: send } = frame;
  // How much of its context budget the run's last step filled (item 222), beside the send.
  const meter = createContextMeter({ frame: frame.element });
  send.before(meter.element);
  // The chat's queue (item 148), right above the composer: a message sent while the run works waits there and starts
  // as the chat's next run once this one has ended done; the page follows it there, unless Settings › General has a
  // message steer the run at work (item 230). A campaign's chat takes no queue: a message steers its session, as
  // before.
  const queueing = run.campaign_id === null;
  const queue = createQueue({
    threadId: run.thread_id,
    frame,
    onSteered: (text) => holdMessage(text),
    onStarted: (startedRunId, afterRunId) => {
      if (afterRunId === runId) {
        ctx.navigate(`/runs/${startedRunId}`);
      }
    },
    onEdit: () => syncControls(),
  });
  // ⌘⇧Enter sends what the box holds the other way than the send does (`steers`): to the run now, past the queue, or
  // into the queue where a message steers; with the box empty, it sends the queue's next message to the run (T3
  // Code's key). Escape ends the edit of a queued message. A key the box already took (a pick of the slash menu, or of
  // an input method's composition) is left to it.
  input.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229) {
      return;
    }
    if (event.key === "Enter" && event.shiftKey && (event.metaKey || event.ctrlKey) && active && queue.editing() === null) {
      event.preventDefault();
      if (input.value.trim() === "") {
        queue.steerFirst();
      } else {
        submit(true);
      }
    } else if (event.key === "Escape" && queue.editing() !== null) {
      event.preventDefault();
      queue.cancelEdit();
    }
  });
  // The choices of the next follow-up (item 133), after T3 Code's composer: the mode, the model with its reasoning,
  // approvals and goal mode of the chat (item 199), which the daemon keeps as its first run started and as they are
  // chosen here, so that a reload, another browser or a run moved to a standby model leaves them as they are; a chat
  // from before has the open run's. The agent stays the chat's, and Keep going is a new chat's, since a campaign
  // starts from a goal of its own.
  const start = detail.choices ?? { mode: run.mode, model: run.model, effort: run.effort, speed: run.speed, approvals: run.approvals, goal_mode: run.goal_mode };
  // A level chosen while the chat follows the daemon's default model is that model's.
  const snapshot = getSnapshot();
  const levelModel = start.model ?? (snapshot.status === null ? "" : snapshot.status.models.main);
  const levels = new Map(start.effort === null ? [] : [[levelModel, start.effort]]);
  // Each choice made here is kept for the chat once the one before it was, as the choices are then: the last one made
  // is the one kept. The level is checked against the model's own, so the models are known first.
  let keeping = Promise.resolve();
  function keepChoices() {
    keeping = keeping.then(() =>
      attempt(async () => {
        await choices.ready();
        await api.patch(`/threads/${run.thread_id}`, { choices: choices.kept() });
      }),
    );
  }
  const choices = runChoices({
    frame,
    kept: {
      mode: start.mode,
      model: start.model ?? "",
      level: {
        // A level the model does not offer is not given: the daemon refuses it (item 116).
        read: (info) => {
          const level = levels.get(info.ref) ?? null;
          return level !== null && info.efforts.includes(level) ? level : null;
        },
        write: (info, level) => {
          if (level === null) {
            levels.delete(info.ref);
          } else {
            levels.set(info.ref, level);
          }
        },
      },
      speed: heldChoice(start.speed),
      approvals: heldChoice(start.approvals),
      goalMode: heldChoice(start.goal_mode),
    },
    optionsTitle: "Approvals and goal mode",
    more: () => [],
    onMode: () => input.focus(),
    onChange: keepChoices,
  });
  frame.options.append(choices.pills.mode, choices.pills.model, choices.pills.reasoning, choices.pills.options);
  const stopStatus = onChange(choices.sync);
  // The chat's commands at work (item 229), between the queue and the composer, as T3 Code shows its background work.
  const background = backgroundWork({ threadId: run.thread_id });
  // The line over a phone's composer (item 297): the run's time and its helpers, each the way to where it is told of.
  // The queue's own banner, right under it, tells what waits.
  const capsule = createCapsule({
    onWork: () => inspector.openTab("run"),
    onHelpers: () => inspector.openTab("agents"),
  });
  const dock = el("div", { class: "dock" }, capsule.element, resumeLine, queue.element, background.element, frame.element, foot);

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
    input.dispatchEvent(new Event("input"));
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
  const keys = messageKeys();

  // `other` sends the message the other way than the send does (⌘⇧Enter): see `steers`.
  async function submit(other) {
    if (sending) {
      return;
    }
    const text = input.value.trim();
    if (text === "") {
      askForWords(attachments);
      input.focus();
      return;
    }
    if (queue.editing() !== null) {
      sending = true;
      send.disabled = true;
      try {
        await attempt(() => queue.saveEdit(text));
      } finally {
        sending = false;
        send.disabled = false;
      }
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
        const next = await deliver(text, files, other);
        frame.clear();
        if (next !== null) {
          ctx.navigate(`/runs/${next}`);
        }
      });
    } finally {
      sending = false;
      send.disabled = false;
    }
  }

  // Whether a message sent while the run works goes to it at once rather than into the queue (item 230): a run that a
  // dotx command drives takes no steer and a campaign's session no queue; else Settings › General chooses, and
  // `other` (⌘⇧Enter) turns it around.
  function steers(other) {
    if (elsewhere) {
      return false;
    }
    return !queueing || (valueOf(BUSY_SEND) === "steer") !== other;
  }

  // Send `text` with the files `files` (their ids) as the composer's send does: to the run under way or into the queue
  // while it works (`steers`, `other` the way ⌘⇧Enter sends), otherwise as the chat's next run, whose id is returned
  // (null for the others).
  async function deliver(text, files, other) {
    if (active && !steers(other)) {
      // The level is checked against the model's own, so the models are known first.
      await choices.ready();
      const chosen = choices.value();
      const body = {
        text,
        mode: chosen.mode,
        model: chosen.model,
        effort: chosen.effort,
        speed: chosen.speed,
        once: chosen.once,
        approvals: chosen.approvals,
        goal_mode: chosen.goal_mode,
        agent_id: run.agent_id,
        attachments: files,
      };
      await queue.add({ ...body, key: keys.of("queue", run.thread_id, body) });
      keys.sent();
      choices.sent();
      return null;
    }
    if (active) {
      // A steer joins the run under way, which keeps its level: an armed choice for one message waits for a message
      // that starts a run, and the person is told (item 259).
      if (choices.value().once !== null) {
        toast("ok", "Steered: the run under way keeps its level. Ultrathink and Ultracode wait for a message that starts a run.");
      }
      const entry = holdMessage(text);
      const body = { text, attachments: files };
      try {
        await api.post(`/runs/${runId}/steer`, { ...body, key: keys.of("steer", runId, body) });
      } catch (error) {
        releaseMessage(entry);
        throw error;
      }
      keys.sent();
      return null;
    }
    // The level is checked against the model's own, so the models are known first.
    await choices.ready();
    const chosen = choices.value();
    const body = {
      goal: text,
      mode: chosen.mode,
      model: chosen.model,
      effort: chosen.effort,
      speed: chosen.speed,
      once: chosen.once,
      approvals: chosen.approvals,
      goal_mode: chosen.goal_mode,
      thread_id: run.thread_id,
      agent_id: run.agent_id,
      attachments: files,
    };
    const next = await api.post("/runs", { ...body, key: keys.of("run", body) });
    keys.sent();
    choices.sent();
    return next.id;
  }

  // Edit from here (item 282, after T3 Code): the chat goes back to before the message of the run `past`, and the message
  // with its files goes to the composer of the run the chat ends with now, or of a new chat when none is left; the files
  // the runs taken out changed are taken back too when the owner chooses so.
  async function editFromHere(past) {
    const files = await choiceDialog({ title: "Edit from here?", message: REWIND_MESSAGE, choices: REWIND_CHOICES });
    if (files === null) {
      return;
    }
    await attempt(async () => {
      const cut = await api.post(`/runs/${past.id}/rewind`, { files });
      keepDraft(cut.back_to ?? NEW_CHAT_DRAFT, { text: cut.text, files: cut.files });
      tellRewound(cut);
      ctx.navigate(cut.back_to === null ? "/runs" : `/runs/${cut.back_to}`);
    });
  }

  function syncControls() {
    resumeLine.hidden = active || !resumable;
    resumeWords.textContent = cutOff === null ? "" : CUT_OFF_LINES[cutOff];
    // A delegated run is not steered: its dock keeps the arrow down (item 96) and its pill, whose Stop stops it.
    const delegated = run.parent_run_id !== null;
    frame.element.hidden = delegated;
    dock.classList.toggle("dock-bare", delegated);
    // While a queued message is edited, the send saves it, and an emptied box stops nothing.
    const editing = queue.editing() !== null;
    frame.setStop(active && !delegated && !editing ? stopRun : null);
    // A message waits in the queue with the pills' choices as the next follow-up (a campaign's steers its session); the
    // run under way keeps its own.
    choices.setWaiting(active);
    // The word in the box arms a choice for one message only where the send can carry it (item 259).
    choices.setWords(!editing && !(active && steers(false)));
    footStop.hidden = !active;
    // Short enough for one line of a phone's composer (item 111).
    const working = steers(false) ? "Steer this run, or /btw to ask on the side" : "Queue a message, or /btw to ask on the side";
    input.placeholder = active ? working : "Follow up, or /btw to ask on the side";
    const sendWord = active ? (steers(false) ? "Steer" : "Queue") : "Follow up";
    frame.setSendLabel(editing ? "Save" : sendWord);
    queue.setActive(active && !elsewhere);
    if (active && ticker === null) {
      // The run's figures, and the time of every delegated run that works (in the conversation and in the Agents tab),
      // count on every second.
      ticker = setInterval(() => {
        paintStats();
        timeline.tick(Date.now() / 1000);
        agents.tick(Date.now() / 1000);
        paintCapsule();
      }, 1000);
      costTicker = setInterval(paintCost, COST_REFRESH_MS);
    } else if (!active && ticker !== null) {
      clearInterval(ticker);
      clearInterval(costTicker);
      ticker = null;
      costTicker = null;
    }
    paintCapsule();
  }

  function paintCapsule() {
    capsule.paint({ status: active ? status : null, since: run.created_at, now: Date.now() / 1000, helpers: timeline.helperStatuses() });
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
    const below = answerNode !== null ? answerNode.getBoundingClientRect().top > window.innerHeight : active && !following;
    const unseen = below && !landing;
    jump.hidden = !unseen;
    const words = answerNode !== null ? "Jump to the answer" : "Jump to latest";
    jump.title = words;
    jump.setAttribute("aria-label", words);
  }

  // The reader leaves the end by scrolling up, not because the end moved on while a scroll of the page's own was on its
  // way: a scroll event that comes after the conversation grew reads as far from the end without the reader having
  // moved (item 274).
  let lastScrollY = window.scrollY;

  function onScroll() {
    const y = window.scrollY;
    following = y < lastScrollY ? nearBottom() : following || nearBottom();
    lastScrollY = y;
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
      landing = true;
      requestAnimationFrame(() => {
        landing = false;
        if (!signal.aborted) {
          node.scrollIntoView({ block: "start" });
          paintJump();
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
      cutOff = fresh.cut_off;
      // A chat renamed since the page opened shows its new title in a phone's bar (item 295).
      if (parentId === null) {
        headingTitle.textContent = clip(fresh.title, TITLE_CHARS);
      }
      status = run.status;
      model = run.model;
      finishedAt = run.finished_at;
      paintStats();
      paintWorking();
      syncControls();
      paintCost();
      paintLessons(fresh.lessons);
      paintQuestions(fresh.questions);
      // The rows of the delegated runs follow the store where the run's events did not bring them (item 285).
      timeline.settle(fresh.children, Date.now() / 1000);
    } catch (error) {
      readFailed("The run", error);
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
      // The tick is for an end that happens under the reader's eyes; a replayed end of a run opened later rests.
      if (active) {
        setActorState(face, faceStateAtEnd(update.status));
      }
      active = false;
      computer.setActive(false);
      messagesMissed();
      finishedAt = update.endedAt;
      paintWorking();
      syncControls();
      reconcile();
      loadActions();
    }
    paintStats();
    paintHeading();
    paintCapsule();
  }

  /** A phone's bar under the chat's title: the agent, or Helper on a helper's page, and the model the run uses now. */
  function paintHeading() {
    headingSub.textContent = `${parentId === null ? actorName : "Helper"} · ${modelName(model)}`;
  }

  // The feet of the run's own messages (their time, a copy, a send again) wait for its answer (item 205).
  function paintWorking() {
    thread.classList.toggle("run-working", !TERMINAL.has(status));
  }

  // ------------------------------------------------------------------------------------------ assemble
  const layout = el("div", { class: "run-layout" });
  // A delegated run's page sends nothing: the run that delegated it wrote its goal, and its composer is hidden (item 96).
  const ownsMessages = run.parent_run_id === null;
  // A resumed run's goal is the one of the run above it; its timeline opens with how it resumed that run. The goal of a
  // run that tells of a command in the background is Dot's note (item 229).
  const goalMessage =
    run.source === RESUME_SOURCE
      ? null
      : run.source === BACKGROUND_SOURCE
        ? backgroundNote(run.goal)
        : userMessage({ text: run.goal, sentAt: run.created_at, files: [], editFromHere: ownsMessages && run.rewindable ? () => editFromHere(run) : null });
  // ------------------------------------------------------------------------------------------- actions
  // What the run did and what can be undone: its acting steps, its approvals and its file changes (see server.actions).
  const actionsBody = el("div", { class: "actions-list" });
  const actionsCard = panelSection({ id: "run-actions", title: "Actions", actions: null, body: actionsBody });
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
        el("button", { class: "row-title mono file-link", type: "button", title: "Show the file", dataset: { path: edit.path }, onclick: (event) => requestFile(event.currentTarget) }, edit.path),
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

  // ------------------------------------------------------------------------------------------------- files
  // The files of the conversation (item 195): those its messages carried and those its answers name, in the order it
  // shows them, each opening beside it as its chip or its path does. The list follows the conversation as it grows.
  const filesBody = el("div", { class: "files-list" });
  const filesCard = panelSection({ id: "run-files", title: "Files", actions: null, body: filesBody });
  filesCard.element.hidden = true;
  let filesShown = "";

  function fileRow(source) {
    const name = source.name ?? source.path.slice(source.path.lastIndexOf("/") + 1);
    return el(
      "button",
      { class: "file-row", type: "button", title: source.path ?? name, dataset: source, onclick: (event) => requestFile(event.currentTarget) },
      icon("file"),
      el("span", { class: "file-row-name" }, name),
      source.path === undefined ? null : el("span", { class: "small muted file-row-where" }, source.path),
    );
  }

  function paintFiles(thread) {
    const sources = new Map();
    for (const button of thread.querySelectorAll(FILE_BUTTONS)) {
      const source = fileSource(button);
      const key = source.attachment ?? source.path;
      if (!sources.has(key)) {
        sources.set(key, source);
      }
    }
    const shown = [...sources.keys()].join("\n");
    if (shown === filesShown) {
      return;
    }
    filesShown = shown;
    filesCard.element.hidden = sources.size === 0;
    filesCard.setSummary(el("span", null, sources.size === 1 ? "1 file" : `${sources.size} files`));
    filesBody.replaceChildren(...[...sources.values()].map(fileRow));
  }

  const actorName = agent !== null ? agent.name : dotName();
  // The width of the panel (item 109): chosen on Dot's computer or dragged on the handle beside the panel.
  const stage = createStageSize({ layout });
  const computer = createComputer({
    runId,
    active: detail.active,
    elsewhere,
    held: detail.held,
    name: actorName,
    sizes: stage.chooser,
  });
  // The agents of the chat: the runs its runs handed work to (item 227).
  const agents = agentList({ threadId: run.thread_id, helper: (id) => timeline.helper(id) });
  // The side panel (item 84), in tabs after T3 Code's (item 203), each with its icon (item 227): the run (its details
  // and its cost, item 96), the plan, the actions (and the files of the conversation), the agents of the chat and the
  // screen (Dot's computer and the screenshots). The run's state is the top bar's face and the Details section's first
  // fact; no card at the head of the conversation (item 113).
  const inspector = createInspector({
    layout,
    tabs: [
      { id: "run", label: "Run", icon: "info", sections: [details.element, cost.element] },
      { id: "plan", label: "Plan", icon: "squareCheck", sections: [plan.element] },
      { id: "actions", label: "Actions", icon: "bolt", sections: [actionsCard.element, filesCard.element] },
      { id: "agents", label: "Agents", icon: "users", sections: [agents.element] },
      { id: "screen", label: "Screen", icon: "monitor", sections: [computer.element] },
    ],
    screensIn: "screen",
  });

  /** Read the chat's agents again; their tab counts them, and so does a phone's bar. */
  async function paintAgents() {
    try {
      const count = await agents.refresh();
      if (!signal.aborted) {
        inspector.setCount("agents", count === 0 ? "" : String(count));
        helpersButton.hidden = count === 0;
        helpersCount.textContent = String(count);
      }
    } catch (error) {
      if (!signal.aborted) {
        agents.fail(`The agents could not be loaded: ${error.message}`);
      }
    }
  }

  // A helper that starts or ends changes the list; the events of one moment are read as one.
  const agentsChanged = debounce(() => paintAgents(), AGENTS_SETTLE_MS);
  // A file a message carries, or one the conversation names, takes the panel (item 195).
  layout.addEventListener(OPEN_FILE_EVENT, (event) => {
    event.stopPropagation();
    inspector.showFile(event.detail, event.target);
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
      if (step > 0) {
        meter.setUsed(inputTokens);
      }
    },
    onScaffold: (scaffold) => meter.setBudget(scaffold),
    onCalls: ({ calls, failed }) => {
      seen.calls = calls;
      seen.failed = failed;
      paintStats();
    },
    onCompacted: ({ count, flagged }) => {
      seen.compactions = count;
      seen.flagged = flagged;
      paintStats();
    },
    onRunStatus,
    onAnswer,
    onImage: inspector.addImage,
    onQuestionLeft: debounce(() => reconcile(), QUESTIONS_SETTLE_MS),
    onHeld: computer.setHeld,
    onBrowserStep: (page) => {
      computer.notePage(page);
      screenButton.hidden = false;
    },
    onGoalFiles: goalMessage === null ? null : (files) => showMessageFiles(goalMessage, files),
  });
  const problem =
    historyProblem === null
      ? null
      : el("div", { class: "note warn" }, icon("alert"), el("span", null, `The earlier part of this conversation could not be loaded: ${historyProblem.message}`));
  // Dot's face and name, or a delegated run's breadcrumb, name the page in the top bar (item 78), and the side panel's
  // switch is the bar's one tool (item 120): the run's state, start, agent, model, approvals and campaign are in the
  // Details of the panel's Run tab. The goal, the first message, is the page's heading for a reader of the structure.
  // On a phone the bar names the chat instead, its title over the agent and the model, after T3 Code's phone app (item
  // 295); a helper's page names its goal and says it is a helper.
  const headingTitle = el("span", { class: "stage-heading-title" }, clip(parentId === null ? detail.title : run.goal, TITLE_CHARS));
  const headingSub = el("span", { class: "stage-heading-sub" });
  const heading = el("span", { class: "stage-heading" }, headingTitle, headingSub);
  paintHeading();
  setStageTitle(face, crumb, crumbSep, stageName, heading);
  // A phone's bar holds its tools in one pill (item 295): Dot's computer once the run has had a page, which then opens
  // over the whole page (item 298), the chat's helpers once it has some, and the panel's switch; a wider bar shows the
  // switch alone (app.css).
  const screenButton = el(
    "button",
    { class: "icon-btn stage-quick", type: "button", title: "Dot's computer", "aria-label": "Show Dot's computer", hidden: !detail.held, onclick: () => computer.openFull() },
    icon("monitor"),
  );
  const helpersCount = el("span", { class: "stage-quick-count" });
  const helpersButton = el(
    "button",
    { class: "icon-btn stage-quick stage-helpers", type: "button", title: "Helpers", "aria-label": "Show the helpers", hidden: true, onclick: () => inspector.openTab("agents") },
    icon("users"),
    helpersCount,
  );
  setStageTools(el("div", { class: "stage-pill" }, screenButton, helpersButton, inspector.toggle));

  // The conversation grows into the room a short one leaves, so the dock under it stands at the window's foot from the
  // first message (item 129).
  const thread = el(
    "div",
    { class: "run-thread" },
    el("h1", { class: "visually-hidden" }, run.goal),
    connectionNote,
    problem,
    earlierTurns(history.earlier, (past) => (past.rewindable ? () => editFromHere(past) : null)),
    goalMessage,
    timeline.element,
    waitingList,
    questionsList,
    lessonsCard,
    continuedIn(history.later),
  );
  paintWorking();
  const filesWatch = new MutationObserver(debounce(() => paintFiles(thread), FILES_SETTLE_MS));
  filesWatch.observe(thread, { childList: true, subtree: true });
  const main = el("div", { class: "run-main" }, thread, dock);
  // The marks of the conversation's messages stand at the layout's left edge (item 201).
  const outline = messageOutline({ layout, main, thread, dock });
  layout.append(outline.element, main, stage.handle, inspector.element, inspector.scrim);
  root.replaceChildren(layout);
  stage.paint();
  inspector.arrange();
  paintStats();
  paintCost();
  paintFiles(thread);
  syncControls();
  paintLessons(detail.lessons);
  paintQuestions(detail.questions);
  loadActions();
  paintAgents();
  window.addEventListener("scroll", onScroll, { passive: true });
  // Lessons change after the run, and a question may be answered or skipped in another tab. A helper that starts or
  // ends is read from the store too.
  const helpersChanged = debounce(() => reconcile(), HELPERS_SETTLE_MS);
  const stopLifecycle = onLifecycle((event) => {
    if ((LESSON_KINDS.has(event.kind) || event.kind === "approval_decided") && event.run_id === runId) {
      reconcile();
    } else if (HELPER_MOMENTS.has(event.kind) && event.run_id !== runId && (event.data.parent_run_id === runId || timeline.knows(event.run_id))) {
      helpersChanged();
    }
  });
  // The run as it stands, its lessons and its questions among it, once the lifecycle stream is back after a break.
  const stopBack = onStreamBack(reconcile);

  follow({
    path: `/runs/${runId}/stream`,
    after: 0,
    signal,
    isFinished: (message) => message.data.kind === "run_finished" && message.data.run_id === runId,
    onState: (state) => {
      showConnectionNote(state === "reconnecting");
    },
    onMessage: (message) => {
      if (message.data.kind === "steer" && message.data.run_id === runId) {
        messageRead(message.data.data.text);
      }
      timeline.push(message.data);
      if (message.data.run_id !== runId && RUN_ENDS.has(message.data.kind)) {
        agentsChanged();
        paintCapsule();
      }
      // A delegated run's row says how long it has worked as soon as its events are in, not at the next second.
      if (active) {
        timeline.tick(Date.now() / 1000);
      }
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
    filesWatch.disconnect();
    outline.dispose();
    stopLifecycle();
    stopBack();
    computer.dispose();
    inspector.dispose();
    stage.dispose();
    stopStatus();
    choices.dispose();
    queue.dispose();
    background.dispose();
    meter.dispose();
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
