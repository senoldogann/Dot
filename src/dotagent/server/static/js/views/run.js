// One run as a conversation: the question, what Dot did about it, its answer, and the box to queue a message for after
// the run, steer it or follow up on it.

import { actorFace, dotFace, setActorState, teamDuo } from "../actors.js";
import { agentList } from "../agentlist.js";
import { api, eventNotShown, follow, query, sse } from "../api.js";
import { AUTO_COMPUTER, BUSY_SEND, settingValue } from "../appearance/settings.js";
import { approvalCard, fromRecord, LEFT_IN_CAMPAIGN, LEFT_IN_RUN } from "../approvals.js";
import { messageText, readyToSend } from "../attachments.js";
import { backgroundWork } from "../background.js";
import { createCapsule } from "../capsule.js";
import { AUTO_MODE, MESSAGE_MODES, runChoices } from "../choices.js";
import { createComputer } from "../computer.js";
import { createCostCard } from "../cost.js";
import { createDetails } from "../details.js";
import { CHAT_BOX_PX, composerFrame, keepDraft, moveDraft, NEW_CHAT_DRAFT } from "../composer.js";
import { createContextMeter } from "../contextmeter.js";
import { createContextPanel } from "../contextpanelview.js";
import { clip, debounce, duration, el, modelName, OPEN_FILE_EVENT, plural, setText, showAfter } from "../dom.js";
import { levelLabel } from "../efforts.js";
import { BACKGROUND_SOURCE, backgroundNote, earlierTurns, loadHistory, RESUME_SOURCE } from "../history.js";
import { dotName, faceState, faceStateAtEnd } from "../face.js";
import { icon } from "../icons.js";
import { createInspector } from "../inspector.js";
import { chatLoops, LOOP_COMMAND, LOOP_HOW, LOOP_WORD, loopOf } from "../loops.js";
import { createQueue } from "../queue.js";
import { recapSection } from "../recap.js";
import { showDotReaction, showMessageFiles, showMessagePicks, userMessage } from "../messages.js";
import { messageOutline } from "../outline.js";
import { chatLoading, emptyPanel, errorPanel, panelFold, panelSection } from "../panels.js";
import { LAST_CHAT_KEY, openingOf, readPref, removePref } from "../prefs.js";
import { PAGE_ANSWER_EVENT } from "../pageframe.js";
import { planBubble, planStep, planTargets } from "../planbubble.js";
import { createReactions, REACTION_EVENTS } from "../reactions.js";
import { heldChoice } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { changesSection } from "../changes.js";
import { runChangesCard } from "../runchanges.js";
import { setupSection } from "../setup.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { createStageSize } from "../stagesize.js";
import { RUN_BACK_EVENT } from "../shell.js";
import { getSnapshot, onChange, onLifecycle, onStreamBack, quietBreak, readFailed } from "../state.js";
import { createTimeline } from "../timeline.js";
import { createWorkStatus } from "../workstatus.js";
import { attempt, choiceDialog, toast } from "../ui.js";
import { stopReading } from "../voice.js";
import { keepChatLook, PAGE_READY_EVENT } from "../waiting.js";
import { narrowOr, PHONE } from "../layout.js";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
// A delegated run's goal in the bar and the words of its pill (item 82).
const CRUMB_CHARS = 56;
const FOOT_WORDS = { done: "Completed in", failed: "Failed after", cancelled: "Stopped after" };
// The words of a delegated run's pill for its approvals, but for the default's, which needs none.
const APPROVAL_WORDS = { supervised: "Supervised", auto: "Auto-approve", yolo: "YOLO" };
const NEAR_BOTTOM_PX = 140;
// How long a chat that opens finished keeps its foot in sight while what is above it settles (item 440).
const SETTLE_OPEN_MS = 2500;
// "/btw" followed by the side question; the question may span lines.
const BTW_PATTERN = /^\/btw(?:\s+([\s\S]*))?$/i;
const TITLE_CHARS = 70;
const QUOTE_CHARS = 160;
// What cut a run off (agent/resume.py `CutOff`), as the line that offers to resume it says it (item 299).
const CUT_OFF_LINES = {
  daemon_restarted: "Dot restarted while this task worked.",
  daemon_shutdown: "Dot was shut down while this task worked.",
  process_ended: "Its Terminal command was told to end while this task worked.",
  process_died: "Its Terminal command stopped suddenly while this task worked.",
};
// Edit from here (item 282): the dialog's line and the owner's two ways of rewinding.
const REWIND_MESSAGE = "Take this chat back to before this message. Your message and its files go back in the message box.";
const REWIND_CHOICES = [
  { value: "revert", label: "Also undo the file changes", tone: "danger" },
  { value: "keep", label: "Keep the file changes", tone: "primary" },
];
// The events that change what became of the lessons of a run; they come on the lifecycle stream, after the run's end.
const LESSON_KINDS = new Set(["lessons_proposed", "lesson_saved", "lesson_dismissed", "lessons_failed"]);
// What a decision on a lesson says once it is made.
const LESSON_DONE = { save: "Saved to memory: later tasks find it like any note.", dismiss: "The lesson is dismissed.", undo: "Undone: the lesson is no longer in memory." };
// A replayed run brings its question events in a burst: one refresh after it is enough.
const QUESTIONS_SETTLE_MS = 120;
// The tools whose results change a file the Changes tab lists (item 368), and how long its read waits for more.
const CHANGING_TOOLS = new Set(["write_file", "edit_file"]);
const CHANGES_SETTLE_MS = 400;
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
// The newest runs of a chat read for its newest turn, its delegated runs listed among them (item 451).
const NEWEST_READ = 20;

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
  return el("a", { class: "back", href: "#/runs" }, icon("arrowLeft"), "All chats");
}

/**
 * Whether the task `runId`, which a read of its page did not find, is the chat the page opened at from `LAST_CHAT_KEY`
 * and no read found yet (`opening`, `openingOf`): one that is gone (Dot started with an empty store, the chat was
 * deleted) is forgotten there, and New chat opens in its place, saying so (item 810), where Dot opened at "Task not
 * found" every time. A missing task the owner went to says so, as before.
 */
function openedGone(runId, opening) {
  if (!opening.gone()) {
    return false;
  }
  const path = `/runs/${runId}`;
  if (readPref(LAST_CHAT_KEY) === path) {
    removePref(LAST_CHAT_KEY);
  }
  toast("info", "The chat that was open last is gone, so Dot opened a new one.");
  location.replace(`${location.pathname}${location.search}#/runs`);
  return true;
}

// What the Plan tab says of a run without a plan (item 203).
const NO_PLAN = "This task has no plan: Dot writes one for work of several steps.";

/**
 * Build the page of a loaded run and start following it. `history` is `loadHistory`'s `{ earlier, read }`, the runs of
 * the conversation before this one; `historyProblem` is the error that kept them from loading, or null. `readNow` says
 * that `detail` was read from the daemon by this open, not kept from an earlier one. Returns the function that stops
 * everything the page started.
 */
// The runs opened last, kept while the page lives (`{ detail, history, messages }`, the stream's messages so far), so
// that coming back to one (from a delegated run's page, say) draws it at once as it stood and follows its stream on
// from there, instead of an empty page that fills again as the whole stream is read once more. The kept `detail` is the
// run as it was loaded, brought up to its end when the run ends on the page (`onRunStatus`), so that a run that is over
// comes back at rest; one that was still at work has its state read again before it is drawn (item 446).
const KEPT_RUNS = 6;
const keptRuns = new Map();

function keepRun(runId, kept) {
  keptRuns.delete(runId);
  keptRuns.set(runId, kept);
  while (keptRuns.size > KEPT_RUNS) {
    keptRuns.delete(keptRuns.keys().next().value);
  }
}

function showRun({ root, ctx, signal, detail, readNow, history, historyProblem, reload, kept }) {
  const runId = detail.run.id;
  let run = detail.run;
  let status = run.status;
  // Dot's face on Dot's own run; a delegated run wears the face of its own hue (item 83); a run of an agent wears the
  // agent's (item 86); a chat with a team wears the faces of its agents (item 610).
  const agent = detail.agent;
  const team = detail.team ?? null;
  const face =
    agent !== null
      ? actorFace(agent.id, faceState(status), agent.look)
      : run.parent_run_id !== null
        ? actorFace(run.id, faceState(status), null)
        : team !== null
          ? teamDuo(team, faceState(status))
          : dotFace(faceState(status));
  let model = run.model;
  // A run that a dotx command drives (`elsewhere`) works here too: its page follows it and its Stop asks that
  // command to stop it, but it takes no steer, and its browser is not shown or taken over from here.
  const elsewhere = detail.elsewhere;
  let active = detail.active || elsewhere;
  // The owner holds the run with a ✋ (item 586): it waits for what they write next, which is then sent to it at once, not
  // queued behind a run that waits for it.
  let holding = detail.holding === true;
  // The run was cut off (by the daemon, or by the end of its dotx command) and a new run can go on from where it
  // stopped; `cutOff` is how.
  let resumable = detail.resumable;
  let cutOff = detail.cut_off;
  let finishedAt = run.finished_at;
  // The end of the run as the page read it when it opened, if it was over then (item 646): the end its stored events
  // replay is that one (a run's end event is never after its record's end), and the record, the bill and the changes
  // read at the open stand for it; an end that comes later, of a run that went on again, reads them anew.
  const endAtOpen = !active && TERMINAL.has(run.status) ? run.finished_at : null;
  // A chat opens at its foot, a live run and a finished one alike (item 440), and keeps there as it settles.
  let following = true;
  let ticker = null;
  let costTicker = null;
  let scrollQueued = false;
  let answerNode = null;
  // From the answer's coming until the frame that lands the page on it, the way down is not offered: the page is about
  // to move there (item 274).
  let landing = false;
  // A run that is over when the page opens starts at the chat's foot, where its newest message stands (item 440).
  const openedFinished = !active;
  const seen = { steps: 0, input: 0, output: 0, calls: 0, failed: 0, compactions: 0, flagged: 0 };

  ctx.setTitle(clip(run.goal, TITLE_CHARS));

  // ---------------------------------------------------------------------------------------------- header
  // The run is stopped from the composer's send while the box is empty, or from a delegated run's pill (item 120).
  function stopRun() {
    attempt(async () => {
      await api.post(`/runs/${runId}/cancel`, null);
      toast("ok", "Stopping the task…");
    });
  }
  const resumeButton = el(
    "button",
    {
      class: "btn primary sm",
      type: "button",
      title: "Start a task in this conversation that goes on from where this one was cut off",
      onclick: () =>
        attempt(async () => {
          const next = await api.post(`/runs/${runId}/resume`, null);
          toast("ok", "Continuing the task…");
          goOn(`/runs/${next.id}`);
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
  // A phone's way back from a chat's page is the sidebar in place, never a page of chats to walk through; from a
  // delegated run it is the run that started it (item 401). The shell draws the button, the page says where it leads.
  window.dispatchEvent(new CustomEvent(RUN_BACK_EVENT, { detail: { to: parentId === null ? null : `#/runs/${parentId}` } }));
  const crumb = parentId === null ? null : el("a", { class: "stage-crumb", href: `#/runs/${parentId}` }, "Main task");
  const crumbSep = parentId === null ? null : el("span", { class: "stage-sep", "aria-hidden": "true" }, "/");
  // Whose chat it is, named in the bar: the agent, the team (item 610) or Dot.
  const chatWith = agent !== null ? agent.name : team !== null ? team.name : dotName();
  const stageName = el("span", { class: "stage-name" }, parentId !== null ? clip(run.goal, CRUMB_CHARS) : chatWith);
  const footTime = el("span", null);
  const footParent = el("span", null, "Part of another task");
  const footStop = el(
    "button",
    { class: "child-stop", type: "button", title: "Stop this task", hidden: true, onclick: stopRun },
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
        footParent.textContent = parent.active ? "Part of a task that is working" : "Works on its own";
      })
      .catch((error) => console.warn("the parent run could not be loaded", error));
  }
  const connectionNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "Connection lost. Reconnecting…"));
  // A break that mends at once, as when the page comes back into view, says nothing (items 286 and 328); one that lasts
  // is told over the conversation, never in its way: what is read does not move (item 328).
  const showConnectionNote = showAfter(connectionNote, quietBreak);
  const connectionSlot = el("div", { class: "connection-slot" }, connectionNote);

  // ------------------------------------------------------------------------------------ details and plan
  // The recap heads the Run tab (item 536): what the run did, does and waits for, in a few lines.
  // A turn that has no recap of its own yet shows the one of the chat's turn before it (item 650), so a new message
  // does not leave the panel empty.
  const recap = recapSection({ runId, earlierRunId: run.parent_run_id === null ? (history.earlier.at(-1)?.id ?? null) : null, signal });
  // What the run is made of (item 539), under the recap: the skills and MCP servers it can use, the files its chat
  // wrote, the pages and searches it read and the notes it saved. A row of Artifacts opens the Changes tab, which the
  // inspector holds after the panel.
  let showChanges = () => undefined;
  const context = createContextPanel({ onOpenChanges: () => showChanges() });
  // What the chat's turns before this one used, read and remembered (item 650): the panel holds the whole chat's
  // context, from its start, under this run's own.
  if (run.parent_run_id === null && history.earlier.length > 0) {
    api
      .get(`/runs/${runId}/chat-context`)
      .then((view) => {
        if (!signal.aborted) {
          context.onEarlier(view.steps);
        }
      })
      .catch((error) => console.warn("the context of the chat's earlier turns could not be read", error));
  }
  const details = createDetails(run, agent, detail.campaign, detail.keep_going, team);
  const planBody = el("div", { class: "plan-list" }, el("div", { class: "hint" }, NO_PLAN));
  const plan = panelSection({ id: "run-plan", title: "Plan", actions: null, body: planBody });

  // The plan as it stands, with how many of its steps are done: the Plan tab, the capsule's part over the composer and
  // its popover (item 644) draw it.
  let planNow = { steps: [], targets: "", done: 0 };
  /** @type {ReturnType<typeof planBubble> | null} */
  let bubble = null;

  // The Plan tab says how far the plan has come (item 203); a run without a plan says why there is none.
  function paintPlan(steps, targets) {
    const done = steps.filter((step) => step.status === "done").length;
    planNow = { steps, targets, done };
    paintCapsule();
    bubble?.paint(steps, targets);
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

  // ----------------------------------------------------------------------------------------------- set up
  // How the run was set up (item 365): its notes and skills, its context budget, its tool groups, the model a step
  // went to. The conversation keeps none of these lines; the Run tab lists them.
  const setup = setupSection();
  // The files the chat's runs wrote or created (item 368), in a tab of their own that counts them; read on arrival,
  // after each write or edit a run reports, and once the run ends.
  const changes = changesSection({
    runId,
    onCount: (files) => inspector.setCount("changes", files === 0 ? "" : String(files)),
    onFiles: (files) => context.onFiles(files),
  });
  const refreshChanges = debounce(() => attempt(changes.refresh), CHANGES_SETTLE_MS);

  // ------------------------------------------------------------------------------------------------ cost
  // What the run cost and where (see cost.js): read on arrival, every COST_REFRESH_MS while it works, once at its end.
  const cost = createCostCard();

  // ------------------------------------------------------------------------------------------------ more
  // The Run tab holds what a glance needs: the recap and what the run used, wrote, read and remembered (items 536 and
  // 539). The facts of the run (its model, its approvals, its work, its time, its mode and its id), how it was set up
  // and where its time went fold away under More, closed the first time (items 452 and 544); a line of the set-up that
  // warns is counted beside its title.
  const more = panelFold({
    id: "run-more",
    title: "More",
    actions: null,
    body: el("div", { class: "run-more" }, details.element, setup.element, cost.element),
    openAtFirst: false,
  });

  function paintMore() {
    const warnings = setup.warnings();
    more.setSummary(warnings === 0 ? null : el("span", { class: "run-more-warning" }, icon("alert"), plural(warnings, "warning", "warnings")));
  }

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
  // More shows when the four lines cut a lesson's text, measured where it stands: a phone's column cuts a shorter text
  // than a wide one does, and a card still folded is measured once it opens. One watch for the rows drawn last.
  const lessonCuts = new ResizeObserver((entries) => {
    for (const { target } of entries) {
      const more = target.nextElementSibling;
      if (!target.classList.contains("open") && more instanceof HTMLElement) {
        more.hidden = target.scrollHeight <= target.clientHeight + 1;
      }
    }
  });
  const lessonsCount = el("span", { class: "lessons-count" });
  // Folded on arrival (item 374): the lessons of a run are a note to open when it is wanted, not a wall over the answer;
  // its head says what waits inside (paintLessons).
  const lessonsCard = el(
    "details",
    { class: "card lessons-card", hidden: true, role: "region", "aria-label": "Lessons from this task" },
    el(
      "summary",
      { class: "lessons-head" },
      el("span", { class: "lessons-mark" }, icon("brain")),
      el("span", { class: "lessons-title" }, "Lessons from this task"),
      lessonsCount,
      el("span", { class: "lessons-chevron" }, icon("chevron")),
    ),
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
    return el("button", { class: "btn ghost sm", type: "button", onclick: () => decideLesson(lesson.index, action) }, words);
  }

  // A lesson reads as a line of the conversation, not as a card of its own (item 374): the text folds to four lines
  // with More beside it, and what became of it is a quiet word under the text, the actions the only buttons.
  function lessonRow(lesson) {
    const text = el("div", { class: "lesson-text" }, lesson.text);
    const more = el("button", { class: "lesson-more", type: "button", hidden: true }, "More");
    more.addEventListener("click", () => {
      const open = text.classList.toggle("open");
      more.textContent = open ? "Less" : "More";
      more.setAttribute("aria-expanded", open ? "true" : "false");
    });
    lessonCuts.observe(text);
    const body = el("div", { class: "lesson-body" }, text, more);
    if (lesson.state === "saved") {
      // Saved on its own or by you, a lesson can be taken back: Undo forgets the note it wrote.
      return el(
        "div",
        { class: "lesson saved" },
        body,
        el(
          "div",
          { class: "lesson-foot" },
          el("span", { class: "lesson-state" }, icon("check"), lesson.by === "auto" ? "Saved on its own" : "Saved to memory"),
          lessonButton(lesson, "undo", "Undo"),
        ),
      );
    }
    if (lesson.state === "dismissed" && lesson.by === "auto") {
      // Left out by the second look, with why; it is still yours to keep.
      return el(
        "div",
        { class: "lesson dismissed" },
        body,
        el("div", { class: "lesson-foot" }, el("span", { class: "lesson-why" }, `Left out: ${lesson.reason}`), lessonButton(lesson, "save", "Save anyway")),
      );
    }
    if (lesson.state === "dismissed") {
      return el("div", { class: "lesson dismissed" }, body, el("div", { class: "lesson-foot" }, el("span", { class: "lesson-state" }, icon("x"), "Dismissed")));
    }
    // The words sit in the buttons themselves, as on an approval card, so that a phone keeps them.
    const save = el("button", { class: "btn primary sm", type: "button", onclick: () => decideLesson(lesson.index, "save") }, icon("check"), "Save to memory");
    const dismiss = el("button", { class: "btn ghost sm", type: "button", onclick: () => decideLesson(lesson.index, "dismiss") }, "Dismiss");
    return el("div", { class: "lesson" }, body, el("div", { class: "lesson-foot" }, save, dismiss));
  }

  // The notes forgotten from this card, by id: the daemon no longer names them, and the card still says what became of
  // them (item 654).
  const forgottenNotes = new Map();

  /** A note the disliked run was handed, with Forget: it goes to the trash, which keeps it 30 days. */
  function doubtedRow(note) {
    if (forgottenNotes.has(note.id)) {
      return el("div", { class: "lesson dismissed" }, el("div", { class: "lesson-text" }, note.text), el("div", { class: "lesson-foot" }, el("span", { class: "lesson-state" }, icon("check"), "Forgotten: in the trash for 30 days")));
    }
    const forget = el("button", { class: "btn ghost sm", type: "button" }, "Forget");
    forget.addEventListener("click", async () => {
      forget.disabled = true;
      try {
        await attempt(async () => {
          await api.del(`/memories/${encodeURIComponent(note.id)}`);
          forgottenNotes.set(note.id, note);
          toast("ok", "Forgotten: it waits in the trash for 30 days");
        });
        await reconcile();
      } finally {
        forget.disabled = false;
      }
    });
    return el(
      "div",
      { class: "lesson doubted" },
      el("div", { class: "lesson-text" }, note.text),
      el("div", { class: "lesson-foot" }, note.pinned ? el("span", { class: "lesson-state" }, icon("pin"), "Pinned") : null, forget),
    );
  }

  /** What the run you disliked was handed (item 654): one of these notes may be what led it astray. */
  function doubtedNotes(disliked) {
    if (disliked === null) {
      return [];
    }
    const named = new Set(disliked.notes.map((note) => note.id));
    const notes = [...disliked.notes, ...[...forgottenNotes.values()].filter((note) => !named.has(note.id))];
    if (notes.length === 0) {
      return [];
    }
    const said = disliked.reaction === "again" ? "asked to do again (🔁)" : "reacted 👎 to";
    return [
      el("div", { class: "lessons-doubted-head" }, `The run you ${said} was handed these notes. One may be what led it astray: forget the ones that should not guide Dot again.`),
      ...notes.map(doubtedRow),
    ];
  }

  /**
   * `lessons` is what became of the lessons of the run, or null when none were drawn from it; `disliked` is the run
   * this one follows up after your 👎 or 🔁, with the notes it was handed, or null.
   */
  function paintLessons(lessons, disliked = null) {
    const doubted = doubtedNotes(disliked);
    lessonsCard.hidden = lessons === null && doubted.length === 0;
    if (lessons === null) {
      const left = disliked?.notes.length ?? 0;
      lessonsCount.textContent = left > 0 ? `${left} to look at` : `${forgottenNotes.size} forgotten`;
      lessonsCount.dataset.tone = "";
      lessonCuts.disconnect();
      lessonsBody.replaceChildren(...doubted);
      return;
    }
    const proposed = lessons.lessons.filter((lesson) => lesson.state === "proposed").length;
    // Folded, the head says what waits inside: why the lessons could not be drawn, the ones that wait for your
    // decision, else how many there are.
    lessonsCount.textContent =
      lessons.error !== null ? "could not be drawn" : proposed > 0 ? `${proposed} to decide` : String(lessons.lessons.length);
    lessonsCount.dataset.tone = lessons.error !== null ? "bad" : proposed > 0 ? "warn" : "";
    lessonCuts.disconnect();
    lessonsBody.replaceChildren(
      ...lessons.lessons.map(lessonRow),
      ...[
        lessons.tainted && proposed > 0
          ? el(
              "div",
              { class: "note warn" },
              icon("alert"),
              el("span", null, "This task read things that other people can write (web pages, mail, downloads): read each lesson before you save it."),
            )
          : null,
        lessons.error === null ? null : el("div", { class: "note bad" }, icon("alert"), el("span", null, lessons.error)),
      ].filter((node) => node !== null),
      ...doubted,
    );
  }

  // ------------------------------------------------------------------------------------------ questions
  // The questions the run left for you and went on without, while they are open; an answer carries the work on in
  // this conversation: into this run while it works, else into a new run on the thread, which the page opens once no
  // other question of this run is open here. A skip, or a decision the daemon refused, refreshes the page; once the
  // last question open here has closed, the page goes on at the chat's newer run if one started meanwhile (item 451).
  const questionsList = el("div", { class: "stack tight", hidden: true, "aria-label": "Dot asked you" });
  const questionCards = new Map();

  async function questionDecided(decided) {
    const lastAnswered = decided !== null && decided.status === "approved" && questionCards.size === 1;
    if (lastAnswered && !active) {
      const [newest] = await api.get(`/runs?thread_id=${encodeURIComponent(run.thread_id)}&limit=1`);
      if (newest !== undefined && newest.id !== runId) {
        goOn(`/runs/${newest.id}`);
        return;
      }
    }
    await reconcile();
  }

  /** `questions` are the stored questions the run left that are still open; a card that is there already stays as it is. */
  function paintQuestions(questions) {
    const wereOpen = questionCards.size;
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
          onUseHere: null,
        });
        questionCards.set(question.id, card.node);
        questionsList.append(card.node);
      }
    }
    questionsList.hidden = questionCards.size === 0;
    // The last question open here closed, answered here or on another screen or skipped: the page goes on at the
    // chat's newer run if one started meanwhile (item 451), which it did not while a question stood.
    if (wereOpen > 0 && questionCards.size === 0) {
      catchUp();
    }
  }

  // ------------------------------------------------------------------------------------------------ dock
  // The shared frame (composer.js): Enter or ⌘Enter sends, as Settings › General chooses (never plain Return on a touch screen); "/" opens the slash menu,
  // whose one command here asks on the side; what is typed and not sent is kept for this run.
  const frame = composerFrame({
    label: "Message",
    boxLabel: `Message for ${agent !== null ? agent.name : dotName()}`,
    placeholder: "",
    maxPx: CHAT_BOX_PX,
    commands: [
      { label: "/btw", hint: "Ask on the side: the task never sees the question.", insert: "/btw " },
      // The dock shows no mode on Auto (item 495): a mode is named for the chat's next messages by its command.
      ...MESSAGE_MODES.map((option) => ({
        label: `/${option.id}`,
        hint: `${option.label}: ${option.hint}`,
        insert: "",
        run: () => choices.setMode(option.id),
      })),
      LOOP_COMMAND,
    ],
    send: () => submit(false),
    sendLabel: "Steer",
    draftKey: runId,
    listener: null,
    threadId: run.thread_id,
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
        goOn(`/runs/${startedRunId}`);
      }
    },
    onEdit: () => syncControls(),
    onCount: () => paintCapsule(),
  });
  // ⌘⇧Enter sends what the box holds the other way than the send does (`steers`): to the run now, past the queue, or
  // into the queue where a message steers; with the box empty, it sends the queue's next message to the run. Escape
  // ends the edit of a queued message. A key the box already took (a pick of the slash menu, or of an input method's
  // composition) is left to it.
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
  // The choices of the next follow-up (item 133): the mode, the model with its reasoning,
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
    modes: MESSAGE_MODES,
    kept: {
      // Task was every chat's mode before Auto (item 495), which settles a task unless the words ask only to look.
      mode: start.mode === "task" ? AUTO_MODE.id : start.mode,
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
      once: null,
    },
    optionsTitle: "Approvals",
    more: () => [],
    onMode: () => input.focus(),
    onChange: keepChoices,
  });
  frame.options.append(choices.pills.mode, choices.pills.model, choices.pills.reasoning, choices.pills.options);
  const stopStatus = onChange(choices.sync);
  // The chat's commands at work (item 229), between the queue and the composer.
  const background = backgroundWork({ threadId: run.thread_id });
  // The chat's loops (item 281), beside its commands at work.
  const loops = chatLoops({ threadId: run.thread_id });
  // The bar over the composer (items 297, 352, 360 and 401): the run's time, its helpers and the messages queued at its
  // left, and the way down at its right end, all in one pane of glass.
  const capsule = createCapsule({
    onWork: () => inspector.openTab("run"),
    onHelpers: () => inspector.openTab("agents"),
    onQueued: () => {
      queue.expand();
      queue.element.scrollIntoView({ block: "nearest" });
    },
  });
  const statusBar = el("div", { class: "status-bar", hidden: true }, capsule.element);
  const dock = el("div", { class: "dock" }, statusBar, resumeLine, queue.element, loops.element, background.element, frame.element, foot);
  // The plan over the composer (item 644): its part in the capsule opens it as a bubble above the dock.
  bubble = planBubble({ anchor: capsule.plan, frame: dock, onOpenTab: () => inspector.openTab("plan") });
  bubble.paint(planNow.steps, planNow.targets);

  // Steer messages that are sent but not read yet; each one leaves when the run reports reading it, at once while its
  // model is at a step, after the tool at work otherwise (item 509).
  const waitingMessages = [];
  const waitingList = el("div", { class: "stack tight", hidden: true });

  function holdMessage(text) {
    const label = el("span", null, `Sending to Dot: “${clip(text, QUOTE_CHARS)}”`);
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
      entry.label.textContent = `The task ended before Dot read: “${clip(entry.text, QUOTE_CHARS)}”`;
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
    // A message of files alone goes too (item 445): a pasted text or a picture sent without a word typed.
    const text = messageText(input.value, attachments);
    if (text === "") {
      input.focus();
      return;
    }
    if (queue.editing() !== null) {
      // The edit's files go as a new message's do: once uploaded, and none that failed (item 376).
      if (!readyToSend(attachments)) {
        return;
      }
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
    if (LOOP_WORD.test(text)) {
      await startLoop(text, other);
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
          goOn(`/runs/${next}`);
        }
      });
    } finally {
      sending = false;
      send.disabled = false;
    }
  }

  // "/loop 30m <message>" (item 281): the chat asks the message again on that rhythm, from a schedule of its own, until
  // it is stopped or a run of it ends it; the message itself goes now, as any message does.
  async function startLoop(text, other) {
    const loop = loopOf(text);
    if (loop === null) {
      toast("error", LOOP_HOW);
      input.focus();
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    const message = loop.message;
    const files = attachments.ids();
    sending = true;
    send.disabled = true;
    try {
      await attempt(async () => {
        await api.post(`/runs/${runId}/loop`, { every: loop.every, text: message });
        loops.refresh();
        const next = await deliver(message, files, other);
        frame.clear();
        if (next !== null) {
          goOn(`/runs/${next}`);
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
    if (holding) {
      return true;
    }
    return !queueing || (settingValue(BUSY_SEND) === "steer") !== other;
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
        ...frame.picks.body(),
      };
      await queue.add({ ...body, key: keys.of("queue", run.thread_id, body) });
      keys.sent();
      choices.sent();
      frame.picks.sent();
      return null;
    }
    if (active) {
      // A steer joins the run under way, which keeps its level and its tools: an armed choice for one message and the
      // picks (item 318) wait for a message that starts a run, and the person is told (item 259).
      if (choices.value().once !== null) {
        toast("ok", "Steered: the task under way keeps its level. Ultrathink and Ultracode wait for a message that starts a new task.");
      }
      if (frame.picks.count() > 0) {
        toast("ok", "Steered: the task under way keeps what it has. Plugins, skills and Plan first wait for a message that starts a new task.");
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
      ...frame.picks.body(),
    };
    const next = await api.post("/runs", { ...body, key: keys.of("run", body) });
    keys.sent();
    choices.sent();
    frame.picks.sent();
    return next.id;
  }

  // Edit from here (item 282): the chat goes back to before the message of the run `past`, and the message
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
      // A chat the rewind emptied leaves for a new one; one that keeps a run goes on at it.
      if (cut.back_to === null) {
        ctx.navigate("/runs");
      } else {
        goOn(`/runs/${cut.back_to}`);
      }
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
    // Short enough for one line of a phone's composer, which holds about thirty characters (items 111 and 437).
    const working = holding
      ? narrowOr("Write to let it go on", "The task is on hold: write to let it go on")
      : steers(false)
        ? narrowOr("Steer this task (or /btw)", "Steer this task, or /btw to ask on the side")
        : narrowOr("Queue a message (or /btw)", "Queue a message, or /btw to ask on the side");
    // A messaging app's few words (item 833); what the at sign and the slash open is the box's tooltip.
    input.placeholder = active ? working : "Follow up";
    keepLook();
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
    const { steps, done } = planNow;
    capsule.paint({
      status: active ? status : null,
      since: run.created_at,
      now: Date.now() / 1000,
      plan: steps.length === 0 ? null : { done, total: steps.length },
      helpers: timeline.helperStatuses(),
      queued: queue.count(),
    });
    // The plan's bubble goes with its part: once the run no longer works, it is not left open over nothing (item 644).
    if (capsule.plan.hidden) {
      bubble?.close();
    }
    paintBar();
  }

  // The bar stands while one of its parts has something to say or the way down is offered in it (item 401).
  let jumpVisible = false;

  function paintBar() {
    statusBar.hidden = capsule.element.hidden && !jumpVisible;
  }

  // ------------------------------------------------------------------------------------------------ scroll
  // The way down (item 96): an arrow at the right end of the bar over the composer while the end, or the answer, is
  // below the window (item 401).
  const jump = el(
    "button",
    {
      class: "btn icon-only jump",
      type: "button",
      hidden: true,
      title: "Jump to latest",
      "aria-label": "Jump to latest",
      onclick: () => {
        jumpVisible = false;
        jump.hidden = true;
        paintBar();
        if (answerNode !== null) {
          answerNode.scrollIntoView({ block: "start", behavior: "smooth" });
          return;
        }
        following = true;
        toEnd("smooth");
      },
    },
    icon("arrowDown"),
  );
  statusBar.append(jump);

  // What scrolls the conversation (item 352): on a phone the conversation itself, between the top bar and the composer,
  // which stay put (`html.chat-shell`, run.css); on a wider window the window, as before. The conversation's box is
  // known once it is built.
  let threadBox = null;

  function scroller() {
    return PHONE.matches && threadBox !== null ? threadBox : document.scrollingElement;
  }

  /** Where what shows of the conversation ends, in the window's coordinates. */
  function seenEnd() {
    return PHONE.matches && threadBox !== null ? threadBox.getBoundingClientRect().bottom : window.innerHeight;
  }

  function toEnd(behavior) {
    const box = scroller();
    box.scrollTo({ top: box.scrollHeight, behavior });
  }

  function nearBottom() {
    const box = scroller();
    return box.clientHeight + box.scrollTop >= box.scrollHeight - NEAR_BOTTOM_PX;
  }

  /** The button leads to the answer once there is one that is still below the window, else to the end of a live run. */
  function paintJump() {
    const below = answerNode !== null ? answerNode.getBoundingClientRect().top > seenEnd() : active && !following;
    // The arrow shows while the end, or the answer, is below the window and the answer is not on its way into view.
    const show = below && !landing;
    jumpVisible = show;
    jump.hidden = !show;
    const words = answerNode !== null ? "Jump to the answer" : "Jump to latest";
    jump.title = words;
    jump.setAttribute("aria-label", words);
    paintBar();
  }

  // The reader leaves the end by scrolling up, not because the end moved on while a scroll of the page's own was on its
  // way: a scroll event that comes after the conversation grew reads as far from the end without the reader having
  // moved (item 274).
  let lastScrollY = scroller().scrollTop;

  function onScroll() {
    const y = scroller().scrollTop;
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
        toEnd("auto");
      }
    });
  }

  function onAnswer(node) {
    answerNode = node;
    // What the run changed on this Mac, under its answer, with the way back (item 597).
    const changed = runChangesCard({ runId, onReview: () => showChanges(), onUndone: () => refreshChanges() });
    node.querySelector(".answer-foot")?.before(changed.element);
    changed.load();
    if (openedFinished) {
      // A chat that is over when it opens starts at its foot, on the newest message (item 440), and keeps there while
      // what is above it settles (pictures, a table's width) until the reader scrolls up.
      following = true;
      landing = true;
      requestAnimationFrame(() => {
        landing = false;
        if (!signal.aborted) {
          toEnd("auto");
          paintJump();
        }
      });
      return;
    }
    if (following) {
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
      drawRecord(fresh);
      paintCost();
    } catch (error) {
      readFailed("The task", error);
    }
  }

  /** Draw the run's record as read: its state, its lessons, its questions and the rows of its helpers. */
  function drawRecord(fresh) {
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
    paintLessons(fresh.lessons, fresh.disliked ?? null);
    paintQuestions(fresh.questions);
    // The rows of the delegated runs follow the store where the run's events did not bring them (item 285).
    timeline.settle(fresh.children, Date.now() / 1000);
  }

  // ----------------------------------------------------------------------------------------------- goes on
  // The chat goes on at another of its runs once, whichever way learns of it first (item 451): a message sent here, the
  // queue's next one, an answer, Continue, a rewind, or a run started in the chat from another screen.
  function goOn(target) {
    if (signal.aborted || location.hash === `#${target}`) {
      return;
    }
    ctx.goOnAt(target);
  }

  // A message sent to this chat from another screen (a phone, another window) starts its next run there, which this
  // page did not know of until it was opened again (item 451). A page that shows the chat's newest run goes on at the
  // newer one, as the screen it was sent from does: when a run of the chat is queued, and once the lifecycle stream is
  // back after a break (it replays nothing: a phone put away). What was typed here and not sent goes with it, as the
  // newer run's draft. A page whose run was not the chat's newest when it first read the chat stays where it is: an
  // earlier run opened from the sidebar's Activity (a question it left, a lesson it drew), from a link or by its
  // address, or a delegated run; and a page with a question its run left open stays until none is (the last one closed,
  // by an answer here or on another screen or a skip, it looks again). `newestKnown` is the chat's newest run as the
  // page last read it, null until then; `shownKey` keeps, in this tab, the run the page showed as the chat's newest
  // until it leaves it, so that a page the browser loads again (a phone's, put away) goes on from where it stood.
  const shownKey = `dot.chatShown.${run.thread_id}`;
  let newestKnown = sessionStorage.getItem(shownKey) === runId ? runId : null;

  async function catchUp() {
    if (run.parent_run_id !== null || sending || (newestKnown !== null && newestKnown !== runId)) {
      return;
    }
    let runs;
    try {
      runs = await api.get(`/runs?thread_id=${encodeURIComponent(run.thread_id)}&limit=${NEWEST_READ}`);
    } catch (error) {
      readFailed("The chat", error);
      return;
    }
    const newest = runs.find((each) => each.parent_run_id === null);
    if (signal.aborted || sending || newest === undefined) {
      return;
    }
    if (newestKnown === runId && newest.id !== runId) {
      // A question this run left stays answerable here while it is open: the answer to one starts the chat's next run,
      // and the page opens it once no other question of the run is open (questionDecided).
      if (questionCards.size > 0) {
        return;
      }
      moveDraft(runId, newest.id);
      goOn(`/runs/${newest.id}`);
      return;
    }
    newestKnown = newest.id;
    if (newestKnown === runId) {
      sessionStorage.setItem(shownKey, runId);
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
      // The replayed end of a run that was over at the open: the record read then stands for it, its end among it.
      const replayed = endAtOpen !== null && update.endedAt !== null && update.endedAt <= endAtOpen;
      const endedAt = replayed ? endAtOpen : update.endedAt;
      // The tick is for an end that happens under the reader's eyes; a replayed end of a run opened later rests.
      if (active) {
        setActorState(face, faceStateAtEnd(update.status));
      }
      active = false;
      // The snapshot kept for a return to this page is of a run that is over: it draws at rest, and its replayed end
      // gives no tick.
      kept.detail = { ...kept.detail, active: false, elsewhere: false, run: { ...kept.detail.run, status: update.status, finished_at: endedAt } };
      computer.setActive(false);
      workStatus.setActive(false);
      messagesMissed();
      finishedAt = endedAt;
      paintWorking();
      syncControls();
      if (!replayed) {
        reconcile();
        refreshChanges();
      }
    }
    paintStats();
    paintHeading();
    paintCapsule();
  }

  /** A phone's bar under the chat's title: the agent, or Helper on a helper's page, and the model the run uses now. */
  function paintHeading() {
    headingSub.textContent = `${parentId === null ? chatWith : "Helper"} · ${modelName(model)}`;
    keepLook();
  }

  // What a chat's own page shows in its bar and its composer, for the curtain of a phone that opens Dot on it again (item
  // 328); a helper's page has no composer.
  function keepLook() {
    if (ownsMessages) {
      keepChatLook(runId, headingTitle.textContent, headingSub.textContent, input.placeholder);
    }
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
  // The Screen tab with nothing yet (item 401): Dot's computer is quiet, and what it shows and the screenshots it takes
  // stand there once it works. The empty panel is the page's own, in Dot's voice; the card of the page takes its place
  // as soon as there is a page.
  const screenQuiet = emptyPanel(
    "monitor",
    "Dot's computer is quiet",
    "The screenshots it takes while it works will appear here. Settings › Browser opens Dot's computer when a task starts its browser.",
  );
  let screenContent = detail.held;
  function paintScreen() {
    screenQuiet.hidden = screenContent;
    computer.element.hidden = !screenContent;
  }
  // The side panel (item 84), in tabs (item 203), each with its icon (item 227): the run (its recap, its context and,
  // under More, its facts and its cost, item 544), the files it changed, the plan, the agents of the chat and the screen
  // (Dot's computer and the screenshots). The run's state is the top bar's face; no card at the head of the
  // conversation (item 113). The conversation's files open from their chips and paths in it.
  // The file beside the conversation stays as the chat goes on at its next run (a message sent, an answer that came;
  // item 406): the browser's tab keeps it by the thread, and the next run's page shows it again as it opens.
  const fileKey = `dot.panelFile.${run.thread_id}`;
  const inspector = createInspector({
    layout,
    tabs: [
      { id: "run", label: "Task", icon: "info", sections: [recap.element, ...context.elements, more.element] },
      { id: "changes", label: "Changes", icon: "fileDiff", sections: [changes.element] },
      { id: "plan", label: "Plan", icon: "squareCheck", sections: [plan.element] },
      { id: "agents", label: "Agents", icon: "users", sections: [agents.element] },
      { id: "screen", label: "Screen", icon: "monitor", sections: [screenQuiet, computer.element] },
    ],
    screensIn: "screen",
    onFile: (source) => {
      if (source === null) {
        sessionStorage.removeItem(fileKey);
      } else {
        sessionStorage.setItem(fileKey, JSON.stringify(source));
      }
    },
  });
  showChanges = () => inspector.openTab("changes");
  paintScreen();

  /** Read the chat's agents again; their tab counts them, and so does a phone's bar. */
  async function paintAgents() {
    try {
      const count = await agents.refresh();
      if (!signal.aborted) {
        inspector.setCount("agents", count === 0 ? "" : String(count));
        helpersButton.hidden = count === 0;
        setText(helpersCount, String(count));
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
  // The reactions of the chat on Dot's messages (item 586): read once the page stands, and again when the daemon tells that
  // one was put or taken back, here or on another screen.
  const reactions = createReactions({ threadId: run.thread_id, signal });
  const timeline = createTimeline({
    rootRunId: runId,
    actorId: agent !== null ? agent.id : run.parent_run_id === null ? "dot" : run.id,
    startedAt: run.created_at,
    // A run that was at work when the page opened ends under the reader's eyes; one that had ended is read again.
    revealing: () => active,
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
    onSetup: (line) => {
      setup.add(line);
      paintMore();
    },
    onCalls: ({ calls, failed }) => {
      seen.calls = calls;
      seen.failed = failed;
      paintStats();
    },
    onTool: (call) => context.onTool(call),
    onMemory: (change) => context.onMemory(change),
    onCompacted: ({ count, flagged }) => {
      seen.compactions = count;
      seen.flagged = flagged;
      paintStats();
    },
    onRunStatus,
    onAnswer,
    onImage: (name) => {
      screenContent = true;
      paintScreen();
      inspector.addImage(name);
    },
    onQuestionLeft: debounce(() => reconcile(), QUESTIONS_SETTLE_MS),
    // A hand-off of a browser step offers to use the page from this screen (item 475).
    onUseHere: () => computer.useHere(),
    onHeld: (held) => {
      computer.setHeld(held);
      if (held) {
        screenContent = true;
        paintScreen();
      }
    },
    onBrowserStep: () => {
      screenContent = true;
      paintScreen();
      computer.notePage();
      screenButton.hidden = false;
      // A run at work that opens its browser shows its page beside the chat (item 354) unless Settings › Browser turned
      // that off; a phone keeps the button, which opens it over the page.
      if (active && !elsewhere && settingValue(AUTO_COMPUTER) === "shown") {
        inspector.peekTab("screen");
      }
    },
    onGoalFiles: goalMessage === null ? null : (files) => showMessageFiles(goalMessage, files),
    onGoalPicks: goalMessage === null ? null : (picks) => showMessagePicks(goalMessage, picks),
    // Dot's reaction stands on the latest message of yours on the page as the event comes (item 664): the run's own or one
    // you sent it since.
    onDotReaction: (reaction) => {
      const mine = threadBox?.querySelectorAll(".user-message") ?? [];
      if (mine.length > 0) {
        showDotReaction(mine[mine.length - 1], reaction);
      }
    },
    reactions,
    onReactionHold: (held) => {
      holding = held;
      syncControls();
    },
  });
  // The line of what the run does now, for a reader who chose it over the steps (item 663; run.css shows it then).
  const workStatus = createWorkStatus({
    rootRunId: runId,
    active,
    onToggle: (shown) => threadBox?.classList.toggle("steps-shown", shown),
  });
  const problem =
    historyProblem === null
      ? null
      : el("div", { class: "note warn" }, icon("alert"), el("span", null, `The earlier part of this conversation could not be loaded: ${historyProblem.message}`));
  // Dot's face and name, or a delegated run's breadcrumb, name the page in the top bar (item 78), and the side panel's
  // switch is the bar's one tool (item 120): the run's state, start, agent, model, approvals and campaign are in the
  // Details of the panel's Run tab. The goal, the first message, is the page's heading for a reader of the structure.
  // On a phone the bar names the chat instead, its title over the agent and the model (item 295); a helper's page names
  // its goal and says it is a helper.
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
  const earlier = earlierTurns(history, (past) => (past.rewindable ? () => editFromHere(past) : null), signal, reactions);
  const thread = el(
    "div",
    { class: "run-thread" },
    el("h1", { class: "visually-hidden" }, run.goal),
    connectionSlot,
    problem,
    earlier,
    goalMessage,
    timeline.element,
    workStatus.element,
    waitingList,
    questionsList,
    lessonsCard,
  );
  paintWorking();
  const main = el("div", { class: "run-main" }, thread, dock);
  // On a phone the dock floats over the conversation's foot (item 374), a pane of glass with the messages going by
  // under it: its height is kept on the column that holds them both, where the conversation's padding reads it. The
  // dock is measured once it stands in the page and whenever it changes (its first height, the bar over the composer, a
  // queued message, a draft of several lines), and a reader at the end stays there as the foot moves with it (item 446).
  threadBox = thread;
  const dockWatch = new ResizeObserver(() => {
    main.style.setProperty("--dock-h", `${dock.offsetHeight}px`);
    if (following) {
      toEnd("auto");
    }
  });
  dockWatch.observe(dock);
  // The marks of the conversation's messages stand at the layout's left edge (item 201).
  const outline = messageOutline({ layout, main, thread, dock });
  layout.append(outline.element, main, stage.handle, inspector.element, inspector.scrim);
  root.replaceChildren(layout);
  stage.paint();
  inspector.arrange();
  const keptFile = storedFile(fileKey);
  if (keptFile !== null) {
    inspector.restoreFile(keptFile);
  }
  paintStats();
  paintCost();
  syncControls();
  // The words in the box are read again when the window crosses the phone's width.
  PHONE.addEventListener("change", syncControls);
  // A chat opens at its end, where its next words come (item 403) or where its newest message stands (item 440); before,
  // the page stood at its top until its first event moved it, which read as a jump up after every message sent.
  toEnd("auto");
  // What is above the foot settles after the page opens (pictures, a table's width, a folded group's height, the work
  // of the earlier turns read in): the foot is held for a moment, a live run's as a finished one's, until the reader's
  // own scroll moves it (items 440 and 446).
  const settling = new ResizeObserver(() => {
    if (following) {
      toEnd("auto");
    }
  });
  settling.observe(timeline.element);
  if (earlier !== null) {
    settling.observe(earlier);
  }
  const settled = setTimeout(() => settling.disconnect(), SETTLE_OPEN_MS);
  paintLessons(detail.lessons, detail.disliked ?? null);
  reactions.load().catch((error) => readFailed("The reactions", error));
  attempt(changes.refresh);
  paintQuestions(detail.questions);
  paintAgents();
  window.addEventListener("scroll", onScroll, { passive: true });
  // A choice tapped in a page of the conversation (item 658) is written into the box, for the user to send.
  // A delegated run has no box (syncControls): the page is told so rather than filling one nobody sees.
  const onPageAnswer = (/** @type {Event} */ event) => {
    if (frame.element.hidden) {
      return;
    }
    frame.fill(/** @type {CustomEvent} */ (event).detail.text);
    event.preventDefault();
  };
  window.addEventListener(PAGE_ANSWER_EVENT, onPageAnswer);
  thread.addEventListener("scroll", onScroll, { passive: true });
  // On a phone the chat is one screen while the page is open (item 352, run.css); the frame stays while the chat goes on
  // at its next run, and goes with the page (main.js).
  document.documentElement.classList.add("chat-shell");
  // Lessons change after the run, and a question may be answered or skipped in another tab, as a draft may be sent or
  // discarded on another screen (item 314). A helper that starts or ends is read from the store too.
  const helpersChanged = debounce(() => reconcile(), HELPERS_SETTLE_MS);
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "draft_decided") {
      timeline.draft(event.data);
    } else if ((LESSON_KINDS.has(event.kind) || event.kind === "approval_decided") && event.run_id === runId) {
      reconcile();
    } else if (HELPER_MOMENTS.has(event.kind) && event.run_id !== runId && (event.data.parent_run_id === runId || timeline.knows(event.run_id))) {
      helpersChanged();
    } else if (REACTION_EVENTS.has(event.kind) && event.data.thread_id === run.thread_id) {
      reactions.refresh();
    } else if (event.kind === "run_queued" && event.data.parent_run_id === null && event.run_id !== runId) {
      catchUp();
    }
  });
  // The run as it stands, its lessons and its questions among it, once the lifecycle stream is back after a break, and
  // whether the chat went on meanwhile (item 451).
  const stopBack = onStreamBack(() => {
    reconcile();
    catchUp();
    reactions.refresh();
  });
  catchUp();

  // While the events read before the page was drawn are put in (`kept`): the changes the open read stand for theirs.
  let replaying = false;
  const take = (message) => {
    if (message.data.kind === "steer" && message.data.run_id === runId) {
      // A queued follow-up of a reaction sent on with Steer (item 586) was read when the note around its words was.
      messageRead(message.data.data.owner_text ?? message.data.data.text);
    }
    timeline.push(message.data);
    workStatus.push(message.data);
    recap.onEvent(message.data);
    if (!replaying && message.data.kind === "tool_result" && CHANGING_TOOLS.has(message.data.data.tool)) {
      refreshChanges();
    }
    if (message.data.run_id !== runId && RUN_ENDS.has(message.data.kind)) {
      agentsChanged();
      paintCapsule();
    }
    // A delegated run's row says how long it has worked as soon as its events are in, not at the next second.
    if (active) {
      timeline.tick(Date.now() / 1000);
    }
  };
  // What was read of the stream (before the page left, or whole up to now when it opened, item 734) goes in at once; the
  // stream goes on after its last message. An event the page fails to show is told, as on the stream, and the next one
  // goes in.
  replaying = true;
  for (const message of kept.messages) {
    try {
      take(message);
    } catch (error) {
      eventNotShown(`/runs/${runId}/stream`, message, error);
    }
  }
  replaying = false;
  keepRun(runId, kept);
  if (kept.messages.length > 0) {
    if (readNow) {
      // What the replay drew of the run's state gives way to its record, which this open read just now (item 646: it
      // was read a second time, with its bill), once the rest of the page is set up.
      queueMicrotask(() => {
        if (!signal.aborted) {
          drawRecord(detail);
        }
      });
    } else {
      // A run drawn as it was kept, over when it was kept: its lessons, its questions and its title are read again.
      attempt(reconcile);
    }
  }
  const finished = (message) => message.data.kind === "run_finished" && message.data.run_id === runId;
  const ended = kept.messages.some(finished);
  // The stream goes on after the last stored event read (item 568). A live piece, of a thought or of a message being
  // written, has no id (0): taken for the last event, it made the stream start again from the first, and a chat left
  // and come back to while its run worked drew every message again, once more at each return.
  const lastStored = kept.messages.reduce((highest, message) => Math.max(highest, message.id), 0);
  (ended ? Promise.resolve() : follow({
    path: `/runs/${runId}/stream`,
    after: lastStored,
    signal,
    isFinished: finished,
    onState: (state) => {
      showConnectionNote(state === "reconnecting");
    },
    onMessage: (message) => {
      kept.messages.push(message);
      take(message);
    },
  })).catch((error) => {
    if (signal.aborted) {
      return;
    }
    console.error("the run stream ended", error);
    if (error.status === 404) {
      root.replaceChildren(backLink(), emptyPanel("circleDashed", "Task not found", `No task has the ID ${runId}.`));
      return;
    }
    toast("error", "Live updates stopped. Reload the page to get them again.", null, error.message);
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
    if (sessionStorage.getItem(shownKey) === runId) {
      sessionStorage.removeItem(shownKey);
    }
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener(PAGE_ANSWER_EVENT, onPageAnswer);
    thread.removeEventListener("scroll", onScroll);
    lessonCuts.disconnect();
    settling.disconnect();
    clearTimeout(settled);
    dockWatch.disconnect();
    PHONE.removeEventListener("change", syncControls);
    outline.dispose();
    stopLifecycle();
    stopBack();
    computer.dispose();
    reactions.dispose();
    inspector.dispose();
    stage.dispose();
    stopStatus();
    choices.dispose();
    workStatus.dispose();
    queue.dispose();
    background.dispose();
    loops.dispose();
    meter.dispose();
    bubble.dispose();
    frame.dispose();
    // An answer being read aloud is not read on over the page the reader went to: the speaker that stops it is on this
    // page, and goes with it (item 612).
    stopReading();
    if (ticker !== null) {
      clearInterval(ticker);
      clearInterval(costTicker);
      ticker = null;
      costTicker = null;
    }
  };
}


/** The file a page of the chat kept under `key` as the chat went on (item 406): `{ path }` or `{ attachment, name }`, as
 * `fileSource` makes it; null when there is none, or what is kept is not one. */
function storedFile(key) {
  const kept = sessionStorage.getItem(key);
  if (kept === null) {
    return null;
  }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(kept);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) {
    return null;
  }
  if ("path" in parsed && typeof parsed.path === "string") {
    return { path: parsed.path };
  }
  if ("attachment" in parsed && typeof parsed.attachment === "string" && "name" in parsed && typeof parsed.name === "string") {
    return { attachment: parsed.attachment, name: parsed.name };
  }
  return null;
}

export function mount(root, ctx) {
  const runId = ctx.params[0];
  // Whether this page is the chat the page opened at (item 810), until a read finds it.
  const opening = openingOf(`/runs/${runId}`);
  const controller = new AbortController();
  let stopRun = () => undefined;

  async function open(again = false) {
    stopRun();
    stopRun = () => undefined;
    const kept = again ? undefined : keptRuns.get(runId);
    if (kept !== undefined && !kept.detail.active && !kept.detail.elsewhere) {
      // A run that was over when it was kept is drawn at once as it stood; its lessons and questions are read again below.
      opening.found();
      stopRun = showRun({ root, ctx, signal: controller.signal, detail: kept.detail, readNow: false, history: kept.history, historyProblem: null, reload: () => attempt(() => open(true)), kept });
      shown();
      return;
    }
    if (kept === undefined) {
      keptRuns.delete(runId);
    }
    // What stands here (the chat that goes on here from another of its runs, main.js's `goOn`; this page read again, or
    // the error it failed with) stays until this one is drawn over it, only to be seen: a word typed into it or a click
    // on it would reach a page that is gone.
    if (root.firstChild === null) {
      root.replaceChildren(chatLoading());
    }
    for (const child of root.children) {
      child.inert = true;
    }
    let detail;
    let messages = [];
    try {
      detail = await api.get(`/runs/${runId}`);
      // Found: a later read that finds nothing (a Reload after it was deleted) says Task not found (item 810).
      opening.found();
      // A run is read whole before it is drawn (item 454), not filled in as its stream comes; one at work up to now
      // (item 734), so that its record, put over what was read, has the last word: a chat opened again while its
      // helpers worked drew their rows from thousands of earlier events as they came, steps behind and a helper that
      // had ended still at work, and nothing set them right.
      if (kept === undefined) {
        messages = await storedMessages();
      }
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      if (error.status === 404 && openedGone(runId, opening)) {
        shown();
        return;
      }
      const problem = error.status === 404 ? emptyPanel("circleDashed", "Task not found", `No task has the ID ${runId}.`) : errorPanel(error, () => attempt(open));
      root.replaceChildren(backLink(), problem);
      shown();
      return;
    }
    if (kept !== undefined) {
      if (controller.signal.aborted) {
        return;
      }
      // A run that was at work when it was kept may have ended since: drawn from what was read of its stream, with its
      // state read again, an end the stream brings now came while the reader was on another page, and the chat opens at
      // its foot with its answer drawn at once (item 446), as it does for a run that was over when its page opened.
      stopRun = showRun({ root, ctx, signal: controller.signal, detail, readNow: true, history: kept.history, historyProblem: null, reload: () => attempt(() => open(true)), kept: { ...kept, detail } });
      shown();
      return;
    }
    let history = { earlier: [], read: new Map() };
    let historyProblem = null;
    try {
      history = await loadHistory(detail.run, controller.signal);
    } catch (error) {
      console.warn("the conversation of this run could not be listed", error);
      historyProblem = error;
    }
    if (controller.signal.aborted) {
      return;
    }
    const fresh = { detail, history, messages };
    stopRun = showRun({ root, ctx, signal: controller.signal, detail, readNow: true, history, historyProblem, reload: () => attempt(() => open(true)), kept: fresh });
    shown();
  }

  /** The stored events of the run and of the runs it delegated to: read whole before the chat is drawn, so a chat opens
   * as it stands instead of filling in as its stream is read (the stream ends after them, item 734). */
  async function storedMessages() {
    const read = [];
    for await (const message of sse(`/runs/${runId}/stream${query({ after: 0, stored: true })}`, controller.signal, () => undefined)) {
      read.push(message);
    }
    return read;
  }

  // The page shows its chat, scrolled where it opens, or why it cannot (item 328): sent once the frame that scrolls it
  // has run.
  function shown() {
    requestAnimationFrame(() => root.dispatchEvent(new CustomEvent(PAGE_READY_EVENT, { bubbles: true })));
  }

  attempt(open);
  return () => {
    controller.abort();
    stopRun();
  };
}
