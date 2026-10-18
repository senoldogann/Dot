// The conversation of one run, built from its events as they arrive: what the agent said, which tools it used, what it
// asked, what the delegated runs did and, at the end, the answer. Events of a delegated run land in a panel nested in the
// tool call that started it, closed until the reader opens it or the run asks the reader something (item 123). The
// tool calls between two messages sit in one group that says what they did, "Ran 2 commands and read 1 file", with the
// time it took beside it (item 374), that the reader can fold away. A group is timed from the moment the work began (the
// start of the run or the last message) to the moment it ended, so the groups of a run add up to the time the run took.

import { AUTO_LINES, valueOf } from "./appearance/settings.js";
import { approvalCard, fromEvent, LEFT_IN_RUN } from "./approvals.js";
import { sentFiles } from "./attachments.js";
import { clip, clock, compact, dateTime, duration, el, plural, setText } from "./dom.js";
import { draftCard, draftsBar } from "./drafts.js";
import { actorFace } from "./actors.js";
import { faceState } from "./face.js";
import { helperCard } from "./helpercard.js";
import { icon } from "./icons.js";
import { foldSteps } from "./layout.js";
import { parseSourcedBlocks } from "./markdown.js";
import { answerMessage, userMessage } from "./messages.js";
import { pageCard } from "./pageframe.js";
import { sentPicks } from "./picks.js";
import { blockNode, prose } from "./prose.js";
import { callKind, describeArguments, flowWords, toolRow } from "./tools.js";

const GOAL_CHARS = 160;
const LATEST_CHARS = 140;
// The answer of a run that ends under the reader's eyes is written in (item 366): at least REVEAL_MIN_CHARS a frame,
// and in REVEAL_MOST_FRAMES frames at most, about two seconds, however long it is.
const REVEAL_MIN_CHARS = 4;
const REVEAL_MOST_FRAMES = 120;
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
// The tool that ends a run. Its answer is shown as the answer, so the call itself stays out of the steps unless it failed.
const FINISH_TOOL = "finish";
const VERDICT_TEXT = { ask: "Needs approval", handoff: "Handed to you", deny: "Blocked by policy" };
// The ways a model turn ends that need no word of their own.
const ORDINARY_ENDS = new Set(["stop", "tool_calls", ""]);
// The words of a delegated run's status ring, for its tooltip and for a screen reader.
const RING_LABELS = { blocked: "waiting" };
// The faces the head of a group of helpers shows, the rest counted (item 245), and how much of a helper's answer its row
// keeps for its last line.
const FLOCK_FACES = 3;
const PREVIEW_CHARS = 240;
// The memory tools' results that changed the memory, "Saved: [mem_x] …", and what their line in the conversation says
// (item 313); a note saved again ("Already saved: …") changed nothing. The line shows this much of the note.
const MEMORY_RESULT = /^(Saved|Updated|Forgot): \[([^\]\s]+)\]/;
const MEMORY_CHANGES = { Saved: "Saved to memory", Updated: "Updated memory", Forgot: "Removed from memory" };
const MEMORY_WORDS_CHARS = 120;
// How the helpers of a group stand, in the order a reader scans them: those under way first, then how
// the others ended.
const FLOCK_WORDS = [
  ["working", ["queued", "running"]],
  ["waiting for you", ["blocked"]],
  ["done", ["done"]],
  ["failed", ["failed"]],
  ["stopped", ["cancelled"]],
];

/** Show the status of a delegated run on the ring of its chip. */
function paintRing(ring, status) {
  const label = RING_LABELS[status] ?? status;
  ring.className = `status-ring s-${status}`;
  ring.title = label;
  ring.setAttribute("aria-label", label);
}

/** Whether two texts read the same, whatever their line breaks and spacing. */
function sameText(first, second) {
  return first.replace(/\s+/g, " ").trim() === second.replace(/\s+/g, " ").trim();
}

/** A markdown text as one plain line (item 245): without the marks of its headings, lists, bold and code, its links as
 * their words. */
function plainLine(text) {
  return text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^[ \t]*(?:#{1,6}|[-*+]|\d+[.)])[ \t]+/gm, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** How the helpers of a group stand, from their statuses: "2 working · 1 done". The line over a phone's composer says it
 * of the run's helpers too (item 297). */
export function flockState(statuses) {
  return FLOCK_WORDS.map(([word, kinds]) => ({ word, count: statuses.filter((status) => kinds.includes(status)).length }))
    .filter(({ count }) => count > 0)
    .map(({ word, count }) => `${count} ${word}`)
    .join(" · ");
}

// A turn of the agent opens with its still face (item 103). A live one is drawn as the message it becomes (item 274):
// `drawn` holds the lines each of its blocks was drawn from, `queued` is set while a draw waits for the next frame and
// `ended` once the message is in or the step is asked again.
function newBubble(scope, step, live) {
  // The words of the flow wear no face of their own (item 374): the face belongs to the answer that closes the turn,
  // and the words line up with the answer's.
  const body = el("div", { class: live ? "prose stream" : "" });
  const node = el("div", { class: live ? "bubble live say" : "bubble say", role: "article", "aria-label": "Dot" }, body);
  scope.container.append(node);
  const caret = el("span", { class: "stream-caret", "aria-hidden": "true" });
  return { node, body, step, text: "", drawn: [], caret, queued: false, ended: false };
}

// What holds the last line of a message: a list's last item, a table's last cell.
const LINE_HOLDERS = new Set(["UL", "OL", "TABLE", "THEAD", "TBODY", "TR"]);

/** The element the last line of `node` (a `div.prose`) ends in: its last block, or the last item or cell in it. */
function lastLine(node) {
  let at = node.lastElementChild ?? node;
  while (LINE_HOLDERS.has(at.tagName) && at.lastElementChild !== null) {
    at = at.lastElementChild;
  }
  return at.tagName === "HR" ? node : at;
}

/**
 * The live message `live` drawn as it stands, formatted as the message will stay once it ends, with the caret at the
 * end of its last line (item 274). It is drawn at most once a frame however fast the model writes, and a block read
 * from the same lines as at the draw before stays as it was drawn: a draw changes the block being written and what
 * follows it alone. The message that ends it is drawn the same, so its end moves nothing on the page.
 */
function drawLive(live) {
  live.queued = false;
  if (live.ended) {
    return;
  }
  const blocks = parseSourcedBlocks(live.text);
  let kept = 0;
  while (kept < live.drawn.length && kept < blocks.length && live.drawn[kept] === blocks[kept].source) {
    kept += 1;
  }
  live.caret.remove();
  for (const stale of [...live.body.children].slice(kept)) {
    stale.remove();
  }
  live.body.append(...blocks.slice(kept).map((sourced) => blockNode(sourced.block)));
  live.drawn = blocks.map((sourced) => sourced.source);
  lastLine(live.body).append(live.caret);
}

/** Where the reveal of `text` stops after `at`: the end of the word it reaches, so that a word comes whole. */
function revealStop(text, at) {
  if (at >= text.length) {
    return text.length;
  }
  const space = text.slice(at).search(/\s/);
  return space === -1 ? text.length : at + space;
}

/**
 * The answer `answer` of a run that ended under the reader's eyes, its words `text` written in as a live message is
 * drawn (item 366): a few words a frame, formatted as they will stay, the caret at their end, and the answer's foot
 * once they are all in. The words keep the room they will take from the start, so the page lands on the answer as it
 * will stand and they are written into it in sight; once they are in, the answer is the one drawn at once, so their
 * end moves nothing. An answer the page let go of (the reader went elsewhere) is not written on.
 */
function revealAnswer(answer, text) {
  const body = answer.querySelector(":scope > .prose");
  if (body === null) {
    return;
  }
  const foot = answer.querySelector(":scope > .answer-foot");
  const live = { body, text: "", drawn: [], caret: el("span", { class: "stream-caret", "aria-hidden": "true" }), queued: false, ended: false };
  const room = body.offsetHeight;
  body.replaceChildren();
  body.classList.add("stream");
  body.style.minHeight = `${room}px`;
  if (foot !== null) {
    foot.style.visibility = "hidden";
  }
  const step = Math.max(REVEAL_MIN_CHARS, Math.ceil(text.length / REVEAL_MOST_FRAMES));
  let at = 0;
  function frame() {
    if (!answer.isConnected) {
      return;
    }
    at = revealStop(text, at + step);
    live.text = text.slice(0, at);
    drawLive(live);
    if (at < text.length) {
      requestAnimationFrame(frame);
      return;
    }
    body.replaceWith(prose(text));
    if (foot !== null) {
      foot.style.visibility = "";
    }
  }
  requestAnimationFrame(frame);
}

// How a call of the step a resumed run was in ended (agent/resume.py).
const CALL_ENDS = { succeeded: "finished", failed: "failed", cut_off: "cut off", not_started: "not started" };
// What cut off the run that a resumed run goes on from (agent/resume.py `CutOff`): as the resumed run's line says it
// after "Resumed this turn after", and in full.
const CUT_OFFS = {
  daemon_restarted: { after: "Dot restarted", how: "The daemon stopped while the run before this one worked: a crash, or a restart that did not wait for it." },
  daemon_shutdown: { after: "Dot was shut down", how: "The daemon was shut down while the run before this one worked." },
  process_ended: { after: "its dotx command was told to end", how: "The dotx command that drove the run before this one was told to end while it worked." },
  process_died: { after: "its dotx command died", how: "The dotx command that drove the run before this one died while it worked." },
};

/** One call of the step a resumed run was in, as "tool how it ended". */
function callEnd(call) {
  const end = `${call.tool} ${CALL_ENDS[call.end] ?? call.end}`;
  return call.end === "cut_off" && !call.repeatable ? `${end} (it may have changed something)` : end;
}

/** What a run spent of each of its limits that is set (agent/limits.py), e.g. "61.2 of 60 minutes and 512k of 500k tokens". */
function limitSpent(data) {
  const parts = [];
  if (data.max_minutes > 0) {
    parts.push(`${(data.seconds / 60).toFixed(1)} of ${data.max_minutes} minutes`);
  }
  if (data.max_tokens > 0) {
    parts.push(`${compact(data.tokens)} of ${compact(data.max_tokens)} tokens`);
  }
  return parts.join(" and ");
}

/** The one line a live group shows for the call that ran last. */
function describeCall(tool, args) {
  const detail = describeArguments(args);
  return clip(detail === "" ? tool : `${tool} · ${detail}`, LATEST_CHARS);
}

/** The line a compaction leaves in the conversation (item 312): "Context compacted", and the size of the context
 * before and after it where the event says them (an event of before item 312 does not). */
function compactionLine(data) {
  const sized = typeof data.tokens_before === "number" && typeof data.tokens_after === "number";
  const text = sized ? `Context compacted ${compact(data.tokens_before)} → ${compact(data.tokens_after)} tokens` : "Context compacted";
  return el("div", { class: "compaction-line" }, icon("minimize"), el("span", null, text));
}

/**
 * The line a change of the memory leaves in the conversation (item 313, after Grok's "Updated memory"): what changed and
 * the first words of the note, for a save and a change from the call's arguments, for a removal from the action's
 * summary, which holds the words the note had. The line of a saved or changed note opens it in Settings › Memory; a
 * removed note is gone. Null for a result that changed nothing: a failed call, a note saved again.
 */
function memoryLine(data, args) {
  const found = data.kind === "memory_write" && data.ok ? MEMORY_RESULT.exec(data.text) : null;
  if (found === null) {
    return null;
  }
  const [, change, id] = found;
  const words = change === "Forgot" ? data.summary.replace(/^Forget: /, "") : typeof args.content === "string" ? args.content : "";
  const parts = [icon("database"), el("span", { class: "memory-change" }, MEMORY_CHANGES[change])];
  if (words.trim() !== "") {
    parts.push(el("span", { class: "memory-words" }, `“${clip(words, MEMORY_WORDS_CHARS)}”`));
  }
  return change === "Forgot"
    ? el("div", { class: "note memory-line" }, ...parts)
    : el("a", { class: "note memory-line", href: `#/settings/memory?note=${encodeURIComponent(id)}` }, ...parts);
}

/** An empty group of tool steps that starts at `ts` (seconds); it is "working" until something ends it. */
function buildGroup(ts) {
  const state = el("span", { class: "steps-state" });
  const title = el("span", { class: "steps-title" });
  const count = el("span", { class: "steps-count" });
  const failed = el("span", { class: "badge b-warn" });
  const latest = el("span", { class: "steps-latest" });
  const body = el("div", { class: "steps-body" });
  const head = el("summary", { class: "steps-head" }, state, title, count, failed, el("span", { class: "chev" }, icon("chevron")), latest);
  const node = el("details", { class: "steps", open: !foldSteps() }, head, body);
  return { node, body, state, title, count, failed, latest, live: null, tail: true, pending: 0, calls: 0, kinds: {}, failedCalls: 0, cutCalls: 0, start: ts, end: ts, longest: 0, last: "" };
}

/** Show a group as it stands: "Working…" while it is the tail of the run or a call is out, what it did once it is over. */
function paintGroup(group) {
  const live = group.tail || group.pending > 0;
  if (group.live !== live) {
    group.live = live;
    group.state.replaceChildren(live ? el("span", { class: "spinner" }) : icon("check"));
  }
  // A stretch of work reads as what it ran (item 374), "Ran 2 commands and read 1 file", with the time it took beside
  // it, and while it runs, how much of it is behind it.
  const took = duration(Math.max(group.end - group.start, group.longest));
  const words = flowWords(group.kinds);
  setText(group.title, live ? "Working…" : words === "" ? `Worked for ${took}` : words);
  setText(group.count, live ? (words === "" ? "" : `${words[0].toLowerCase()}${words.slice(1)} so far`) : words === "" ? "" : took);
  const troubles = [group.failedCalls > 0 ? `${group.failedCalls} failed` : "", group.cutCalls > 0 ? `${group.cutCalls} cut off` : ""];
  group.failed.hidden = group.failedCalls === 0 && group.cutCalls === 0;
  setText(group.failed, troubles.filter((text) => text !== "").join(" · "));
  group.latest.hidden = !live;
  setText(group.latest, live ? group.last : "");
}

/**
 * The group of helpers that follow one another (item 245): a head with their faces,
 * how many they are and how they stand, the time from the first one's start to the last one's end and a chevron that
 * folds their rows away. It is open from the start.
 */
function buildFlock() {
  const faces = el("span", { class: "helpers-faces", "aria-hidden": "true" });
  const title = el("span", { class: "helpers-title" });
  const state = el("span", { class: "helpers-state" });
  const time = el("span", { class: "helpers-time" });
  const main = el("span", { class: "helpers-main" }, title, state);
  const head = el("summary", { class: "helpers-head" }, faces, main, time, el("span", { class: "chev" }, icon("chevron")));
  const body = el("div", { class: "helpers-body" });
  const node = el("details", { class: "helpers", open: true }, head, body);
  return { node, body, faces, title, state, time };
}

/**
 * The timeline of the run `rootRunId`, which was created at `startedAt` (seconds). `push(record)` takes events in order,
 * `settle(runs, now)` the records of its delegated runs, `knows(runId)` says whether a run has its place in it,
 * `helper(runId)` gives what its row keeps of a delegated run (see helperCard; null for a run without a row),
 * `helperStatuses()` the statuses of the delegated runs, and `draft(record)` paints a draft's card as the draft now
 * stands (item 314: one answered after the run's stream ended). `revealing()` says whether an answer that arrives now
 * ends a run under the reader's eyes, to be written in (item 366), rather than one read again; the
 * callbacks tell the page about what the timeline cannot show by itself: `onGrow()` after every event, `onPlan(steps, targets)`,
 * `onStats({ step, input, output })` after every model turn and every side call of the run (`step` 0: a compaction, a
 * review, a description of images), `onScaffold({ contextTokens, window, chosen, summariseAt })` for the run's scaffold
 * event (its context budget, its model's window, the size chosen for the model and the prompt past which the run
 * summarizes), `onSetup({ kind, short, iconName, text, tone })` for each line of how the run was set up (item 365; the
 * conversation keeps none), `onCalls({ calls, failed })` after every tool call of the run, `onCompacted({ count, flagged })` after
 * every compaction of the run (item 260; the conversation shows a quiet line where it happened, item 312; `flagged`
 * counts the checks that found a section missing or a name the conversation never had),
 * `onRunStatus({ status, model, endedAt })` (`endedAt` is null until the run is over), `onAnswer(node)` when the answer is
 * on the page, `onImage(name)` for every screenshot a tool returned and `onBrowserStep()` for the root run's first
 * browser tool call (Dot's computer starts its view on it). `onGoalFiles(files)` takes the files that came
 * with the run's goal, to show them under it, and `onGoalPicks(picks)` what the owner picked for it in the composer
 * (item 318); null where the page shows no goal, and they stand in the timeline.
 */
export function createTimeline({ rootRunId, actorId, startedAt, revealing, onGrow, onPlan, onStats, onScaffold, onSetup, onCalls, onCompacted, onRunStatus, onAnswer, onImage, onQuestionLeft, onHeld, onBrowserStep, onGoalFiles, onGoalPicks }) {
  const element = el("div", { class: "timeline" });
  // The card a delegated run's row shows when the pointer rests on it or the focus comes to it (item 226).
  const card = helperCard(element);
  const scopes = new Map();
  const cards = new Map();
  // The scopes of the delegated runs that have not ended: their rows count their time on (`tick`).
  const liveChildren = new Set();
  let browserSeen = false;
  const totals = { calls: 0, failed: 0 };
  // The cards of the drafts the runs wrote, by draft id, in the order they came (item 314): a decision repaints its card
  // wherever it is; while two or more wait, a bar after the last of them sends the ticked ones together.
  const drafts = new Map();
  const draftBar = draftsBar(() => [...drafts.values()]);

  // `began` is when the run started; `boundary` is when the work since the last message started.
  function makeScope({ runId, actorId: actor, container, panel, parent, began }) {
    return {
      runId,
      actorId: actor,
      container,
      panel,
      parent,
      began,
      boundary: began,
      calls: new Map(),
      finishing: new Map(),
      group: null,
      // The group that holds the calls of the model turn under way, open or ended by the rows of the helpers it started.
      turnGroup: null,
      groups: [],
      live: null,
      // The block of the latest step's thinking (item 246; see startThought).
      thought: null,
      reply: null,
      touched: false,
      // The cards that wait for the reader's answer in this scope and the delegated runs inside it.
      asking: 0,
      // While the run waits for its model to be reached again: the note that says so and the attempts it counts.
      unreachable: null,
      // The helpers whose rows follow one another at the end of the scope (item 245): `members` (their scopes),
      // `anchor` (the last element they make up) and `group` (null while there is only one).
      flock: null,
    };
  }

  scopes.set(rootRunId, makeScope({ runId: rootRunId, actorId, container: element, panel: null, parent: null, began: startedAt }));

  /** The group is over at `ts`: whatever comes next starts a new one. */
  function endGroup(scope, ts) {
    if (scope.group !== null) {
      liftThought(scope);
      scope.group.tail = false;
      scope.group.end = Math.max(scope.group.end, ts);
      paintGroup(scope.group);
      scope.group = null;
    }
  }

  /** Put something the reader must see at the end of the scope. It ends the group before it, so it never hides in a fold. */
  function place(scope, node, ts) {
    endGroup(scope, ts);
    scope.container.append(node);
    scope.boundary = ts;
  }

  /**
   * Put the row of the delegated run of `scope` at the end of its parent's scope, as `place` does. Rows that follow one
   * another gather in one group (item 245): the second one makes it and takes the first one in.
   */
  function placeHelper(scope, ts) {
    const parent = scope.parent;
    endGroup(parent, ts);
    const flock = parent.flock;
    if (flock !== null && parent.container.lastElementChild === flock.anchor) {
      if (flock.group === null) {
        flock.group = buildFlock();
        flock.anchor.replaceWith(flock.group.node);
        flock.group.body.append(flock.anchor);
        flock.anchor = flock.group.node;
      }
      flock.group.body.append(scope.panel.node);
      flock.members.push(scope);
      paintFaces(flock);
    } else {
      parent.container.append(scope.panel.node);
      parent.flock = { members: [scope], anchor: scope.panel.node, group: null };
    }
    scope.panel.flock = parent.flock;
    parent.boundary = ts;
    paintFlock(parent.flock, ts);
  }

  /** The faces in the head of a group of helpers: the first ones', the rest counted. */
  function paintFaces(flock) {
    const shown = flock.members.slice(0, FLOCK_FACES).map((member) => actorFace(member.actorId, "idle", null));
    const more = flock.members.length - FLOCK_FACES;
    flock.group.faces.replaceChildren(...shown, ...(more > 0 ? [el("span", { class: "helpers-more" }, `+${more}`)] : []));
  }

  /** The head of a group of helpers as it stands at `now` (seconds): how many they are, how they stand, and the time from
   * the first one's start to the last one's end, which counts on while one works. */
  function paintFlock(flock, now) {
    if (flock.group === null) {
      return;
    }
    const helpers = flock.members.map((member) => member.panel.helper);
    const live = helpers.some((helper) => helper.ended === null);
    const began = Math.min(...helpers.map((helper) => helper.began));
    const ended = live ? now : Math.max(...helpers.map((helper) => helper.ended));
    setText(flock.group.title, plural(helpers.length, "helper", "helpers"));
    setText(flock.group.state, flockState(helpers.map((helper) => helper.status)));
    flock.group.state.classList.toggle("bad", !live && helpers.some((helper) => helper.status === "failed"));
    setText(flock.group.time, duration(Math.max(ended - began, 0)));
  }

  /**
   * A turn that ends with a plain reply has that text twice: as its last message and as the result of the run. The result
   * is shown as the answer, so the message that repeats it goes, provided nothing came after that message.
   */
  function dropRepeatedReply(scope, result) {
    const reply = scope.reply;
    scope.reply = null;
    if (reply !== null && sameText(reply.text, result) && scope.container.lastElementChild === reply.node) {
      reply.node.remove();
      return true;
    }
    return false;
  }

  /** The run is over: no group works any more, and a call that never reported back was cut off. */
  function settleGroups(scope, ts) {
    endGroup(scope, ts);
    endThought(scope);
    for (const { row, group } of scope.calls.values()) {
      if (row.settle()) {
        group.cutCalls += 1;
      }
    }
    for (const group of scope.groups) {
      group.pending = 0;
      paintGroup(group);
    }
  }

  function groupFor(scope) {
    if (scope.group === null) {
      const group = buildGroup(scope.boundary);
      scope.group = group;
      scope.turnGroup = group;
      scope.groups.push(group);
      // The thinking of the step whose calls start the group, with nothing after it, is the group's first row (item 246);
      // a thought the reader opened stays in sight, the group opening with it.
      if (scope.thought !== null && scope.container.lastElementChild === scope.thought.node) {
        group.body.append(scope.thought.node);
        group.node.open = group.node.open || scope.thought.node.open;
        scope.thought.group = group;
      }
      scope.container.append(group.node);
    }
    return scope.group;
  }

  /**
   * The group a tool call of `scope` goes in, and the line of the decision on it: the open group, else the group of its
   * model turn when the rows of the helpers that turn started stand last in the scope, else a new one. A turn's helpers
   * run a few at a time (`[agent] parallel_tools`): the call of one that starts once another ended stays with the calls
   * of its turn, so its row joins the group of its turn's helpers (item 285).
   */
  function callGroup(scope) {
    const turn = scope.turnGroup;
    if (scope.group === null && turn !== null && scope.flock !== null && scope.container.lastElementChild === scope.flock.anchor) {
      return turn;
    }
    return groupFor(scope);
  }

  /** A delegated run asks the reader something (item 123): its row opens, and so do the rows it sits in and their
   * groups (item 245), so the card is in sight. */
  function reveal(scope) {
    for (let at = scope; at.panel !== null; at = at.parent) {
      at.asking += 1;
      at.panel.node.open = true;
      if (at.panel.flock.group !== null) {
        at.panel.flock.group.node.open = true;
      }
    }
  }

  /** The reader answered: a row with nothing left inside it that waits for them folds again, unless they opened or
   * closed it by hand. */
  function unreveal(scope) {
    for (let at = scope; at.panel !== null; at = at.parent) {
      at.asking -= 1;
      if (at.asking === 0 && !at.touched) {
        at.panel.node.open = false;
      }
    }
  }

  // The compactions of the run so far, and those whose check flagged something (item 260).
  const compactions = { count: 0, flagged: 0 };

  function note(scope, ts, iconName, text, tone) {
    place(scope, el("div", { class: tone === "" ? "note" : `note ${tone}` }, icon(iconName), el("span", null, text)), ts);
  }

  /**
   * A line of how the run was set up (item 365): the side panel lists it under Set up (`onSetup`), and the
   * conversation keeps none, so a line that comes mid-run splits no stretch of work. `kind` and `short` are what it
   * adds to that section's head ("words" for a notes' or skills' search by words alone, whose parts are said once).
   * A delegated run's lines are on its own page.
   */
  function setupLine(scope, kind, short, iconName, text, tone) {
    if (scope.runId === rootRunId) {
      onSetup({ kind, short, iconName, text, tone });
    }
  }

  function childScope(runId, data, ts) {
    const parent = scopes.get(data.parent_run_id) ?? scopes.get(rootRunId);
    // A run of an agent (item 87, a message to it) wears the agent's face; any other delegated run its own.
    const face = actorFace(data.agent_id ?? runId, "working", null);
    const status = el("span", { role: "img" });
    paintRing(status, "running");
    // The mode and, once it starts, the model of the delegated run: for a screen reader; the row shows none of it.
    const meta = el("span", { class: "visually-hidden" }, data.mode ?? "task");
    // The last line of the delegated run: its step and tool, then how it ended and how many steps it took.
    const progress = el("span", { class: "child-last small muted meta-facts child-progress" }, el("span", null, "starting"));
    const time = el("span", { class: "child-time" });
    const open = el(
      "a",
      { class: "child-open", href: `#/runs/${runId}`, title: "Open the delegated run's own page", "aria-label": "Open the delegated run" },
      icon("external"),
    );
    open.addEventListener("click", (event) => event.stopPropagation());
    const inner = el("div", { class: "timeline" });
    // One row (item 82): the face with its state at the corner, the goal over the last line, the time it has taken,
    // the way to its page and the chevron; opened, the run's steps hang under it. It starts closed (item 123).
    const summary = el(
      "summary",
      { class: "child-row" },
      el("span", { class: "child-face" }, face, status),
      el("span", { class: "visually-hidden" }, "Delegated run:"),
      el("span", { class: "child-main" }, el("span", { class: "child-title" }, clip(data.goal ?? "", GOAL_CHARS)), progress),
      meta,
      time,
      open,
      el("span", { class: "chev" }, icon("chevron")),
    );
    const node = el("details", { class: "child" }, summary, inner);
    summary.addEventListener("click", () => {
      scope.touched = true;
    });
    // What the row's card tells of the delegated run, kept up to date by its events (see helperCard).
    const helper = {
      goal: data.goal ?? "",
      model: data.model ?? null,
      effort: data.effort ?? null,
      speed: data.speed ?? "standard",
      status: "running",
      began: ts,
      ended: null,
      action: null,
      calls: 0,
    };
    card.attach(summary, helper, () => actorFace(data.agent_id ?? runId, faceState(helper.status), null));
    // `line`: the facts its last line shows (see paintChild).
    const panel = { node, face, status, meta, progress, line: "starting", time, steps: 0, helper, flock: null };
    const scope = makeScope({ runId, actorId: data.agent_id ?? runId, container: inner, panel, parent, began: ts });
    scopes.set(runId, scope);
    liveChildren.add(scope);
    placeHelper(scope, ts);
    return scope;
  }

  /** The row of a delegated run as it stands at `ts`: its last line as facts, drawn again only when they changed (item
   * 310), and the time it has taken. */
  function paintChild(scope, ts, facts) {
    const line = facts.join("\n");
    if (scope.panel.line !== line) {
      scope.panel.line = line;
      scope.panel.progress.replaceChildren(...facts.map((fact) => el("span", null, fact)));
    }
    setText(scope.panel.time, duration(Math.max(ts - scope.began, 0)));
  }

  /** The row of the delegated run of `scope` shows that the run ended with `status` at `ts` after `steps` steps, its
   * answer `result`. */
  function endChild(scope, status, steps, ts, result) {
    Object.assign(scope.panel.helper, { status, ended: ts, calls: 0 });
    paintRing(scope.panel.status, status);
    scope.panel.face.setAttribute("data-state", faceState(status));
    scope.panel.face.classList.add("ended");
    // Its last line is the start of its answer (item 245), after how it ended unless it is done;
    // a run with no answer says how it ended and in how many steps.
    const answer = clip(plainLine(result), PREVIEW_CHARS);
    const facts = answer === "" ? [status, plural(steps, "step", "steps")] : status === "done" ? [answer] : [status, answer];
    paintChild(scope, ts, facts);
    liveChildren.delete(scope);
    paintFlock(scope.panel.flock, ts);
    // Whatever it asked is moot now: a row the reader never opened or closed by hand is closed again.
    if (!scope.touched) {
      scope.panel.node.open = false;
    }
  }

  /**
   * The rows of the delegated runs as the store has them at `now` (seconds), from `runs`, their records (the `children`
   * of the run's detail), for what the page did not read of their events: a run with no row gets one, the row of a
   * run that ended shows how it ended, and the row of a run at work counts the steps the store counts, so the
   * conversation says what the sidebar says (item 285). Until item 197, the end of a stopped run was stored before the
   * ends of the runs it delegated, and the page reads the events of a run up to its own end.
   */
  function settle(runs, now) {
    for (const run of runs) {
      const known = scopes.get(run.id);
      const scope =
        known ??
        childScope(
          run.id,
          { goal: run.goal, parent_run_id: run.parent_run_id, agent_id: run.agent_id, mode: run.mode, model: run.model, effort: run.effort, speed: run.speed },
          run.created_at,
        );
      if (!liveChildren.has(scope)) {
        continue;
      }
      if (run.finished_at !== null) {
        settleGroups(scope, run.finished_at);
        endChild(scope, run.status, run.steps, run.finished_at, run.result);
      } else if (run.steps > scope.panel.steps) {
        scope.panel.steps = run.steps;
        paintChild(scope, now, [`step ${run.steps}`]);
      }
    }
  }

  /** The row of every delegated run that has not ended says how long it has worked at `now` (seconds): the page calls
   * this every second while the run works, so a folded row's time counts on between the events of its run (item 123),
   * and as an event comes. The card a row shows follows (item 226), and so does the head of the group the row is in
   * (item 245). What reads the same as before is not written again (item 310): the page calls this on every event of
   * the run and its helpers. */
  function tick(now) {
    const flocks = new Set();
    for (const scope of liveChildren) {
      setText(scope.panel.time, duration(Math.max(now - scope.began, 0)));
      flocks.add(scope.panel.flock);
    }
    for (const flock of flocks) {
      paintFlock(flock, now);
    }
    card.refresh(now);
  }

  /**
   * A folded block for the thinking of step `step` (item 246): a line with the brain, "Thinking…"
   * while the model thinks and "Thought" once it is done, the start of the thought beside it; opened, the whole thought.
   * It goes into the open group of steps, else at the end of the scope, where the group that the step's calls start
   * takes it in (`groupFor`); a group that ends before a call of the step comes leaves it out (`liftThought`).
   */
  function startThought(scope, step) {
    const label = el("span", { class: "thought-label" }, "Thinking…");
    const preview = el("span", { class: "tool-sum thought-preview" });
    const text = el("div", { class: "thought-text" });
    const head = el(
      "summary",
      { class: "tool-head" },
      el("span", { class: "tool-state" }),
      el("span", { class: "tool-kind" }, icon("brain")),
      label,
      preview,
      el("span", { class: "chev" }, icon("chevron")),
    );
    const node = el("details", { class: "thought", dataset: { live: "true" } }, head, el("div", { class: "tool-body" }, text));
    const group = scope.group;
    (group === null ? scope.container : group.body).append(node);
    // `line` is what the group said of its last call before: a folded group says that the model thinks until it is done.
    scope.thought = { node, label, preview, text, step, start: "", live: true, settled: false, group, line: group === null ? "" : group.last };
    if (group !== null) {
      group.last = "Thinking…";
      paintGroup(group);
    }
    return scope.thought;
  }

  /** The group the thought sits in says what it said of its last call again, not that the model thinks. */
  function restoreLine(thought) {
    if (thought.group !== null && thought.group.last === "Thinking…") {
      thought.group.last = thought.line;
      paintGroup(thought.group);
    }
  }

  /**
   * A thought that ends its group, no call of its step after it, stands after the group, out of the fold: before the
   * message its step writes or the answer the run ends with (item 246).
   */
  function liftThought(scope) {
    const thought = scope.thought;
    if (thought !== null && thought.group !== null && thought.group.body.lastElementChild === thought.node) {
      restoreLine(thought);
      thought.group.node.after(thought.node);
      thought.group = null;
    }
  }

  /** The block of step `step`'s thinking, which grows by `piece` as the model streams it. A step that thinks again
   * after it wrote goes on in the same block. */
  function growThought(scope, step, piece) {
    let thought = scope.thought;
    if (thought === null || thought.step !== step || thought.settled) {
      thought = startThought(scope, step);
    } else if (!thought.live) {
      thought.live = true;
      thought.node.dataset.live = "true";
      thought.label.textContent = "Thinking…";
    }
    thought.text.append(piece);
    if (thought.start.length < PREVIEW_CHARS) {
      thought.start += piece;
      thought.preview.textContent = clip(plainLine(thought.start), PREVIEW_CHARS);
    }
  }

  /** The model is done thinking: its block says "Thought", folded or open as the reader left it. */
  function endThought(scope) {
    const thought = scope.thought;
    if (thought !== null && thought.live) {
      thought.live = false;
      thought.node.dataset.live = "false";
      thought.label.textContent = "Thought";
      restoreLine(thought);
    }
  }

  /** The step is asked again from its start: what it thought goes with what it wrote. */
  function dropThought(scope, step) {
    const thought = scope.thought;
    if (thought !== null && thought.step === step && !thought.settled) {
      restoreLine(thought);
      thought.node.remove();
      scope.thought = null;
    }
  }

  /** The step's `assistant` event: the thinking it keeps, its secrets masked (agent/loop.py), stands for what streamed;
   * on a page that reads the run's events afterwards it makes the block. Events stored before item 246 keep none. */
  function settleThought(scope, data) {
    const kept = typeof data.thinking === "string" ? data.thinking : "";
    const current = scope.thought !== null && scope.thought.step === data.step && !scope.thought.settled ? scope.thought : null;
    if (current === null && kept === "") {
      return;
    }
    const thought = current ?? startThought(scope, data.step);
    endThought(scope);
    thought.settled = true;
    if (kept !== "") {
      thought.text.textContent = kept;
      thought.preview.textContent = clip(plainLine(kept.slice(0, PREVIEW_CHARS * 2)), PREVIEW_CHARS);
    }
  }

  function markWaiting(scope, waiting, ts) {
    if (scope.runId === rootRunId) {
      onRunStatus({ status: waiting ? "blocked" : "running", model: null, endedAt: null });
      return;
    }
    scope.panel.helper.status = waiting ? "blocked" : "running";
    paintRing(scope.panel.status, scope.panel.helper.status);
    paintFlock(scope.panel.flock, ts);
  }

  /** Add a tool call as a row to the group it belongs to; `call` is `{ id, tool, args }`. */
  function showCall(scope, call) {
    const group = callGroup(scope);
    const row = toolRow({ tool: call.tool, args: call.args });
    group.body.append(row.node);
    group.calls += 1;
    const kind = callKind(call.tool);
    group.kinds[kind] = (group.kinds[kind] ?? 0) + 1;
    group.pending += 1;
    group.last = describeCall(call.tool, call.args);
    const entry = { row, group, args: call.args };
    scope.calls.set(call.id, entry);
    if (scope.runId === rootRunId) {
      totals.calls += 1;
      onCalls({ calls: totals.calls, failed: totals.failed });
    }
    paintGroup(group);
    return entry;
  }

  const handlers = {
    run_started(scope, record) {
      if (scope.runId === rootRunId) {
        onRunStatus({ status: "running", model: record.data.model, endedAt: null });
        return;
      }
      scope.panel.meta.textContent = `${scope.panel.meta.textContent} · ${record.data.model}`;
      scope.panel.helper.model = record.data.model;
    },

    run_finished(scope, record) {
      const { status, result } = record.data;
      // What the run said, as it ended, it needs from the user (older runs have none).
      const needs = typeof record.data.needs === "string" ? record.data.needs.trim() : "";
      const isRoot = scope.runId === rootRunId;
      settleGroups(scope, record.ts);
      // A reply that streamed the same words was read as they came: its answer does not write them in again.
      const readAlready = dropRepeatedReply(scope, result);
      const answer = answerMessage({
        actorId: scope.actorId,
        status,
        text: result,
        endedAt: record.ts,
        startedAt: scope.began,
        href: isRoot ? null : `#/runs/${scope.runId}`,
      });
      place(scope, answer, record.ts);
      if (isRoot && needs !== "") {
        const head = el("div", { class: "aside-head" }, icon("hand"), el("span", null, "What Dot needs from you"));
        place(scope, el("div", { class: "needs", "aria-label": "What Dot needs from you" }, head, prose(needs)), record.ts);
      }
      if (isRoot) {
        if (!readAlready && revealing() && !REDUCED_MOTION.matches) {
          revealAnswer(answer, result);
        }
        onAnswer(answer);
        onRunStatus({ status, model: null, endedAt: record.ts });
        return;
      }
      endChild(scope, status, scope.panel.steps, record.ts, result);
      // The parent resumes when the delegated run is over; the time in between was spent on the delegation.
      scope.parent.boundary = record.ts;
    },

    assistant(scope, record) {
      const data = record.data;
      // A new turn: its calls go in the open group, or in the group its first call makes.
      scope.turnGroup = scope.group;
      settleThought(scope, data);
      if (scope.panel !== null) {
        scope.panel.steps = Math.max(scope.panel.steps, data.step);
        // A turn that calls a tool has its line painted by its first call, so the tool's name does not go and come back
        // at every step (item 310).
        if (!data.tool_calls.some((name) => name !== FINISH_TOOL)) {
          paintChild(scope, record.ts, [`step ${scope.panel.steps}`]);
        }
      }
      const bubble = scope.live !== null && scope.live.step === data.step ? scope.live : null;
      scope.live = null;
      if (bubble !== null) {
        bubble.ended = true;
      }
      const hasText = data.text.trim() !== "";
      if (!hasText) {
        if (bubble !== null) {
          bubble.node.remove();
        }
        if (scope.runId === rootRunId) {
          onStats({ step: data.step, input: data.input_tokens, output: data.output_tokens });
        }
        return;
      }
      // A message ends the group of steps before it: the steps that follow it make a new one.
      endGroup(scope, record.ts);
      scope.turnGroup = null;
      scope.boundary = record.ts;
      const target = bubble ?? newBubble(scope, data.step, false);
      target.node.className = "bubble say";
      target.body.replaceWith(prose(data.text));
      scope.reply = { node: target.node, text: data.text };
      if (!ORDINARY_ENDS.has(data.finish)) {
        target.node.append(el("div", { class: "small muted" }, `This message ended early: ${data.finish}`));
      }
      if (scope.runId === rootRunId) {
        onStats({ step: data.step, input: data.input_tokens, output: data.output_tokens });
      }
    },

    // A model call of the run (agent/loop.py and the router): a step's tokens came with its assistant event already, so
    // only the side calls add theirs to the run's numbers.
    model_call(scope, record) {
      const data = record.data;
      if (scope.runId === rootRunId && data.purpose !== "step") {
        onStats({ step: 0, input: data.input_tokens, output: data.output_tokens });
      }
    },

    delta(scope, record) {
      const data = record.data;
      if (data.kind === "thinking") {
        growThought(scope, data.step, data.text);
        return;
      }
      if (data.kind === "reset") {
        if (scope.live !== null) {
          scope.live.ended = true;
          scope.live.node.remove();
          scope.live = null;
        }
        dropThought(scope, data.step);
        return;
      }
      endThought(scope);
      if (scope.live === null || scope.live.step !== data.step) {
        // The step writes: its thought comes out of the group before its message, as on a page loaded later.
        liftThought(scope);
        scope.live = newBubble(scope, data.step, true);
      }
      const live = scope.live;
      live.text += data.text;
      if (!live.queued) {
        live.queued = true;
        requestAnimationFrame(() => drawLive(live));
      }
    },

    tool_call(scope, record) {
      const data = record.data;
      if (data.tool === FINISH_TOOL) {
        scope.finishing.set(data.call_id, data.args);
        return;
      }
      if (scope.runId === rootRunId && data.tool.startsWith("browser_") && !browserSeen) {
        browserSeen = true;
        onBrowserStep();
      }
      if (scope.panel !== null) {
        paintChild(scope, record.ts, [`step ${scope.panel.steps}`, data.tool]);
        scope.panel.helper.action = describeCall(data.tool, data.args);
        scope.panel.helper.calls += 1;
      }
      showCall(scope, { id: data.call_id, tool: data.tool, args: data.args });
    },

    tool_result(scope, record) {
      const data = record.data;
      const finishArgs = scope.finishing.get(data.call_id);
      if (finishArgs !== undefined) {
        scope.finishing.delete(data.call_id);
        if (data.ok) {
          return;
        }
        showCall(scope, { id: data.call_id, tool: data.tool, args: finishArgs });
      }
      const entry = scope.calls.get(data.call_id) ?? showCall(scope, { id: data.call_id, tool: data.tool, args: {} });
      const { row, group, args } = entry;
      row.finish(data);
      if (scope.panel !== null) {
        scope.panel.helper.calls = Math.max(0, scope.panel.helper.calls - 1);
      }
      group.pending = Math.max(0, group.pending - 1);
      group.end = Math.max(group.end, record.ts);
      group.longest = Math.max(group.longest, data.seconds);
      if (!data.ok) {
        group.failedCalls += 1;
        if (scope.runId === rootRunId) {
          totals.failed += 1;
          onCalls({ calls: totals.calls, failed: totals.failed });
        }
      }
      paintGroup(group);
      for (const name of data.images) {
        onImage(name);
      }
      const remembered = memoryLine(data, args);
      if (remembered !== null) {
        place(scope, remembered, record.ts);
      }
    },

    policy(scope, record) {
      const data = record.data;
      const verdict = VERDICT_TEXT[data.verdict] ?? data.verdict;
      note(
        scope,
        record.ts,
        "shield",
        `${verdict}: ${clip(data.summary, 200)} — ${clip(data.reason, 240)}`,
        data.verdict === "deny" ? "bad" : "warn",
      );
    },

    approval_requested(scope, record) {
      const card = approvalCard({ approval: fromEvent(record.run_id, record.data), heading: null, onSettled: () => undefined, leftNote: LEFT_IN_RUN });
      cards.set(record.data.approval_id, { card, scope });
      place(scope, card.node, record.ts);
      reveal(scope);
      markWaiting(scope, true, record.ts);
    },

    // A question the run left for the user and went on without; it waits in the run's "Dot asked you" list.
    question_left(scope, record) {
      const data = record.data;
      const question = typeof data.args.question === "string" ? data.args.question : data.summary;
      note(scope, record.ts, "help", `Dot asked you “${clip(question, 300)}” and went on without the answer`, "warn");
      if (scope.runId === rootRunId) {
        onQuestionLeft();
      }
    },

    approval_decided(scope, record) {
      const entry = cards.get(record.data.approval_id);
      if (entry !== undefined) {
        cards.delete(record.data.approval_id);
        entry.card.settle(record.data.status, record.data.note);
        // The time spent waiting for the decision was not spent working.
        entry.scope.boundary = record.ts;
        unreveal(entry.scope);
        markWaiting(entry.scope, false, record.ts);
      }
    },

    // An action that nobody was asked about (YOLO) or that the review model answered. The line goes into the group of the call
    // it belongs to, so a run in YOLO mode keeps its steps together; the stored approval has the same facts. The action and
    // the reason read once each: a record kept before item 253 ended its summary with its note, which is not said again.
    approval_auto(scope, record) {
      const data = record.data;
      const yolo = data.decided_by === "yolo";
      const denied = data.status === "denied";
      // Settings › General may keep such lines out of the chat (item 346); a refusal stays, as its action did not run.
      if (!denied && valueOf(AUTO_LINES) === "hidden") {
        return;
      }
      const verdict = yolo ? "YOLO, not asked" : denied ? "Reviewer denied" : "Reviewer approved";
      const said = data.note === "" || data.summary.endsWith(data.note);
      const reason = said ? "" : ` — ${clip(data.note, 240)}`;
      const text = `${verdict}: ${clip(data.summary, 200)}${reason}`;
      const tone = denied ? "auto-decision denied" : yolo ? "auto-decision yolo" : "auto-decision";
      callGroup(scope).body.append(el("div", { class: tone }, icon(yolo ? "bolt" : "shield"), el("span", null, text)));
    },

    // The checker of goal mode compared the answer with the goal: `final` says whether the run ends with this verdict,
    // and `limit` that it judged the finish of a run past its limit, which cannot be sent back.
    goal_check(scope, record) {
      const { decision, reason, check, patience, final, limit } = record.data;
      const because = reason === "" ? "" : ` — ${clip(reason, 300)}`;
      const stops = limit === true
        ? ["bad", "Goal check: not reached, and the run is at its limit, so it stops"]
        : ["bad", `Goal check: still not reached after ${patience} rounds of further work, so the run stops`];
      const lines = {
        achieved: ["ok", "Goal check: the goal is reached"],
        continue: final ? stops : ["warn", `Goal check ${check} of ${patience}: not reached yet, back to work`],
        blocked: ["bad", "Goal check: nothing more can be done without you"],
        error: ["bad", "Goal check: the check could not run"],
      };
      const [tone, text] = lines[decision];
      note(scope, record.ts, "target", `${text}${because}`, tone);
    },

    steer(scope, record) {
      const files = record.data.files ?? [];
      const text = record.data.text;
      place(scope, userMessage({ text, sentAt: record.ts, files, editFromHere: null }), record.ts);
    },

    // The files that came with the goal; they sit under the goal's bubble.
    attachments(scope, record) {
      if (scope.runId === rootRunId && onGoalFiles !== null) {
        onGoalFiles(record.data.files);
        return;
      }
      place(scope, sentFiles(record.data.files), record.ts);
    },

    // What the goal was sent with from the composer (item 318): its chips sit under the goal's bubble too.
    picks(scope, record) {
      if (scope.runId === rootRunId && onGoalPicks !== null) {
        onGoalPicks(record.data);
        return;
      }
      place(scope, sentPicks(record.data), record.ts);
    },

    // A page the run showed (`show_page`, item 193), in its frame.
    page_shown(scope, record) {
      place(scope, pageCard(record.data.page_id, record.data.title), record.ts);
    },

    plan(scope, record) {
      const steps = record.data.steps;
      if (scope.runId === rootRunId) {
        // Plans stored before targets existed have none.
        onPlan(steps, record.data.targets ?? "");
        return;
      }
      const done = steps.filter((step) => step.status === "done").length;
      const failed = steps.filter((step) => step.status === "failed").length;
      const failedText = failed > 0 ? `, ${failed} failed` : "";
      note(scope, record.ts, "list", `Plan: ${done} of ${steps.length} steps done${failedText}`, "");
    },

    plan_reminder(scope, record) {
      const numbers = record.data.open_steps.join(", ");
      note(scope, record.ts, "list", `Dot wanted to finish with plan steps ${numbers} still open and was asked to close them first`, "warn");
    },

    // A session of a campaign wanted to end while the campaign had no targets: it was sent back once to record them.
    campaign_targets_reminder(scope, record) {
      note(scope, record.ts, "target", "Dot wanted to end the session before the campaign had targets and was asked to record them first", "warn");
    },

    // The run reached a limit of [agent] run_max_minutes or run_max_tokens: it was told to finish, and its next reply ends it.
    limit_reached(scope, record) {
      note(scope, record.ts, "clock", `The run reached its limit (${limitSpent(record.data)}) and was told to finish now`, "warn");
    },

    // The final answer named paths that do not exist and that the run never touched ([agent] verify_answer_paths): the
    // run was sent back once to create them or to correct the answer.
    answer_paths(scope, record) {
      const missing = record.data.missing;
      const noun = missing.length === 1 ? "a path" : `${missing.length} paths`;
      const listed = clip(missing.join(", "), 300);
      note(scope, record.ts, "file", `Dot's answer named ${noun} that do not exist (${listed}) and was asked to correct it`, "warn");
    },

    // The run goes on from where the run before it was cut off ([agent] resume, Continue or dotx resume): a line with a
    // bolt that says Dot restarted and resumed the turn (item 299); opened, how that run was cut off, how each call of
    // the step it was in ended, and the way to that run.
    resumed(scope, record) {
      const data = record.data;
      const cutOff = CUT_OFFS[data.cut_off];
      const restarted = data.automatic && (data.cut_off === "daemon_restarted" || data.cut_off === "daemon_shutdown");
      const head = el(
        "summary",
        { class: "tool-head" },
        el("span", { class: "tool-kind restart-mark" }, icon("bolt")),
        el("span", { class: "restart-label" }, restarted ? "Dot restarted and resumed this turn" : `Resumed this turn after ${cutOff.after}`),
        el("span", { class: "chev" }, icon("chevron")),
      );
      const calls =
        data.calls.length === 0
          ? null
          : el("div", null, el("h4", null, "Its last step"), el("ul", { class: "restart-calls" }, ...data.calls.map((call) => el("li", null, callEnd(call)))));
      const body = el(
        "div",
        { class: "tool-body restart-body" },
        el("p", null, `${cutOff.how} ${data.automatic ? "Dot went on with it on its own when it started again." : "It was resumed by hand."}`),
        calls,
        el("a", { class: "small", href: `#/runs/${data.from_run_id}` }, "Open the run it resumes"),
      );
      place(scope, el("details", { class: "restart" }, head, body), record.ts);
    },

    // How much help the run's model gets. Shown only where it differs from the configuration: a context budget cut to
    // fit the model's window or set by the size chosen for the model (item 250), and reminders of the goal for a model
    // of the weak tier. The run's own budget and window go to the context meter (item 222). A scaffold event of before
    // item 250 names no chosen size and no point of summary.
    scaffold(scope, record) {
      const data = record.data;
      const chosen = typeof data.chosen === "number" ? data.chosen : null;
      if (scope.runId === rootRunId) {
        onScaffold({ contextTokens: data.context_tokens, window: data.window, chosen, summariseAt: typeof data.summarise_at === "number" ? data.summarise_at : null });
      }
      const output = `tool output up to ${compact(data.tool_output_chars)} characters`;
      const budget = `budget ${compact(data.context_tokens)}`;
      if (data.cut_to_window && chosen !== null) {
        setupLine(scope, "budget", budget, "sliders", `Context budget ${compact(data.context_tokens)} tokens, the ${compact(chosen)} chosen for the model in Settings cut to fit its window of ${compact(data.window)}, ${output}`, "");
      } else if (data.cut_to_window) {
        setupLine(scope, "budget", budget, "sliders", `Context budget ${compact(data.context_tokens)} tokens to fit the model's window of ${compact(data.window)}, ${output}`, "");
      } else if (chosen !== null) {
        setupLine(scope, "budget", budget, "sliders", `Context budget ${compact(data.context_tokens)} tokens, the size chosen for the model in Settings, ${output}`, "");
      }
      if (data.reminder_every !== null) {
        setupLine(scope, "cadence", `goal every ${data.reminder_every} steps`, "target", `The goal is repeated every ${data.reminder_every} steps: ${data.reason}`, "");
      }
    },

    reminder(scope, record) {
      setupLine(scope, "reminder", `goal at step ${record.data.step}`, "target", `Reminded of the goal at step ${record.data.step}`, "");
    },

    // The tool groups of a run whose tools come in groups ([agent] tool_surface): the ones it starts with, then each
    // group a step turns on.
    tool_groups(scope, record) {
      const data = record.data;
      if (data.step === 0) {
        const off = data.off.length === 0 ? "" : `; ${data.off.join(", ")} off until the run needs them`;
        setupLine(scope, "tools", "tools by group", "layers", `Tools offered by group: ${data.on.join(", ")} on${off} (${data.reason})`, "");
      } else {
        const noun = data.turned_on.length === 1 ? "group" : "groups";
        setupLine(scope, "tools", `${data.turned_on.join(", ")} on`, "layers", `Turned on the tool ${noun} ${data.turned_on.join(", ")} at step ${data.step}`, "");
      }
    },

    // The notes a run was handed. At its start the line shows only when the chat had not recalled one of them before
    // (`fresh`, item 335): a follow-up handed the same notes again says nothing. An event older than `fresh` shows.
    memories(scope, record) {
      const data = record.data;
      const fresh = typeof data.fresh === "number" ? data.fresh : data.ids.length;
      if (data.live !== true && typeof data.site !== "string" && fresh === 0) {
        return;
      }
      const count = plural(data.ids.length, "memory", "memories");
      const tokens = typeof data.tokens === "number" ? `, ${plural(data.tokens, "token", "tokens")}` : "";
      const newer = fresh < data.ids.length ? ` (${fresh} new)` : "";
      // Item 308: the lessons of a site the run just reached say where they were learned.
      const text =
        data.live === true
          ? `Picked up ${count} saved while the run worked`
          : typeof data.site === "string"
            ? `Recalled ${count} learned on ${data.site}`
            : `Recalled ${count}${newer}${tokens}`;
      const short = data.live === true ? `${count} picked up` : typeof data.site === "string" ? `${count} of ${data.site}` : `${count}${newer}`;
      setupLine(scope, "memories", short, "database", text, "");
    },

    // The run's start found its notes or its skills by their words alone: the embedding service failed (item 256).
    embedding_failed(scope, record) {
      const skills = record.data.purpose === "skills";
      const what = skills ? "Skills listed" : "Notes recalled";
      setupLine(scope, "words", skills ? "skills" : "notes", "database", `${what} by their words alone: the embedding service failed — ${clip(record.data.error, 240)}`, "warn");
    },

    // A compaction is a quiet line across the conversation where it happened, with the size of the context before and
    // after it (item 312, after Codex's); the Details panel counts the compactions and the checks that flagged
    // something (item 260), the Cost panel has the summaries' share, and the event keeps the model, the method and the
    // check.
    compacted(scope, record) {
      if (scope.runId !== rootRunId) {
        return;
      }
      const check = record.data.check;
      compactions.count += 1;
      if (check !== undefined && check !== null && !check.ok) {
        compactions.flagged += 1;
      }
      onCompacted({ ...compactions });
      place(scope, compactionLine(record.data), record.ts);
    },

    // A draft a run wrote for the owner (item 314): a card where it was written, and the owner's Send or Discard on it,
    // or a send that failed, which may come long after the run ended.
    draft_created(scope, record) {
      const made = draftCard(record.data, () => draftBar.paint());
      drafts.set(record.data.id, made);
      place(scope, made.node, record.ts);
      draftBar.paint();
      // An answer that came after the run's last event is not in the run's stream: the card reads the draft once.
      made.refresh();
    },

    draft_decided(scope, record) {
      drafts.get(record.data.id)?.update(record.data);
    },

    // A side question (/btw) and its answer: shown in the conversation, never part of the run's own context.
    aside_question(scope, record) {
      place(scope, el("div", { class: "aside-question", dataset: { aside: record.data.id } }, el("span", { class: "aside-tag" }, "/btw"), el("span", null, record.data.text)), record.ts);
    },

    aside_answer(scope, record) {
      const head = el("div", { class: "aside-head" }, icon("help"), el("span", null, `Side answer by ${record.data.model}; the run did not see this`));
      place(scope, el("div", { class: "aside-answer" }, head, prose(record.data.text)), record.ts);
    },

    aside_failed(scope, record) {
      note(scope, record.ts, "alert", `The side question got no answer from ${record.data.model}: ${record.data.error}`, "bad");
    },

    // The run's model could not answer (`reason`: a spent quota or rate, a refused or missing key, a refused access),
    // so the run goes on with a standby model ([models] standby) from this step on; the chip of the run, or the header
    // of a delegated run, names the new model.
    model_switched(scope, record) {
      const data = record.data;
      note(scope, record.ts, "refresh", `${data.from} cannot answer (${data.reason}); the run goes on with ${data.to}`, "warn");
      if (scope.runId === rootRunId) {
        onRunStatus({ status: "running", model: data.to, endedAt: null });
        return;
      }
      scope.panel.meta.textContent = scope.panel.meta.textContent.replace(` · ${data.from}`, ` · ${data.to}`);
      scope.panel.helper.model = data.to;
    },

    // The run's model could not be reached (`error`: the connection broke or timed out, or its server failed with a
    // status that retrying gets past): the run asks step `step` again in `retry_in_s` seconds, and keeps asking until
    // `until` ([agent] model_wait_s). One note stands for the whole wait, each attempt rewrites it; `model_back` says that
    // the model answered again after `waited_s` seconds (item 158).
    model_waiting(scope, record) {
      const data = record.data;
      if (scope.unreachable === null) {
        const text = el("span", null);
        place(scope, el("div", { class: "note warn" }, icon("clock"), text), record.ts);
        scope.unreachable = { text, tries: 0 };
      }
      const wait = scope.unreachable;
      wait.tries += 1;
      const tried = wait.tries === 1 ? "" : `tried ${wait.tries} times; `;
      wait.text.textContent = `${data.model} cannot be reached (${data.error}); ${tried}the run asks again in ${duration(data.retry_in_s)} and keeps asking until ${clock(data.until)}`;
    },
    model_back(scope, record) {
      const data = record.data;
      scope.unreachable = null;
      note(scope, record.ts, "refresh", `${data.model} answered again after ${duration(data.waited_s)}; the run goes on`, "");
    },

    // A helper model ([models] helpers) could not answer a question of the run; `until` (seconds) is when it may be
    // asked again, null for a missing key.
    helper_unavailable(scope, record) {
      const data = record.data;
      const until = data.until === null ? "" : ` until ${dateTime(data.until)}`;
      note(scope, record.ts, "key", `The helper ${data.model} cannot answer (${data.reason}${until})`, "warn");
    },

    // The typed judge ([judge]) could not answer (`reason`, `error`); until `until` (seconds) nothing is asked of it and
    // the policy's patterns and the goal checker decide alone.
    judge_unavailable(scope, record) {
      const data = record.data;
      const text = `The typed judge cannot answer (${data.reason}) until ${dateTime(data.until)}; the policy's patterns and the goal checker decide alone: ${data.error}`;
      note(scope, record.ts, "key", text, "warn");
    },

    // TypeSafe rejected a request of the typed judge or of judge_items, or answered outside its reference (`error`): an
    // error of the code or the model, logged with the request; nothing is paused.
    judge_error(scope, record) {
      note(scope, record.ts, "key", `TypeSafe rejected a request (the log has it): ${record.data.error}`, "warn");
    },

    // The user took the browser over (`url`: the tab they got) and the run waits at its next tool call; and gave it
    // back after `held_s` seconds, with a note to the model that the page may have changed.
    takeover(scope, record) {
      const where = record.data.url ? ` at ${record.data.url}` : "";
      note(scope, record.ts, "hand", `You took the browser over${where}; the run waits until you return control.`, "");
      onHeld(true);
    },
    handback(scope, record) {
      note(scope, record.ts, "hand", `You returned control after ${Math.round(record.data.held_s)} s; the run goes on.`, "");
      onHeld(false);
    },

    // A typed judgment sent a step to another model of the run (`model`, `option`, `probabilities`); or that model
    // could not answer the step, which is asked again on the run's own model.
    step_routed(scope, record) {
      const data = record.data;
      const odds = Object.entries(data.probabilities).map(([option, p]) => `${option} ${p.toFixed(2)}`).join(", ");
      setupLine(scope, "route", `step ${data.step} on ${data.model}`, "refresh", `Step ${data.step} goes to ${data.model} (${odds})`, "");
    },
    step_route_failed(scope, record) {
      const data = record.data;
      setupLine(scope, "route-back", `step ${data.step} back on the run's model`, "refresh", `${data.model} could not answer step ${data.step}; asked again on the run's model: ${data.error}`, "warn");
    },

    // A session of a campaign ranked the posts it could answer (`topic`, `weights`, `posts` with their scores, the
    // highest first).
    posts_ranked(scope, record) {
      const data = record.data;
      const top = data.posts.slice(0, 3).map((post) => `${post.id} ${post.score.toFixed(2)}`).join(", ");
      note(scope, record.ts, "key", `Ranked ${data.posts.length} posts on “${data.topic}”; the top: ${top}`, "");
    },

    // The progress watchdog saw that the run is not getting anywhere (`detail`) and answered with a rung of its ladder
    // (`action`); the consult rung brings the advice of the review model, or the `error` that ended the run. An event
    // without `action` comes from the older stall guard, which only counted its `warnings`.
    stall(scope, record) {
      const data = record.data;
      if (data.action === undefined) {
        note(scope, record.ts, "alert", `Dot repeated the same step and was told to change its approach (warning ${data.warnings})`, "warn");
        return;
      }
      const answers = {
        nudge: "Dot was told to change its approach.",
        replan: "Its next step may only plan again or finish.",
        consult: "The review model was asked for a second opinion.",
        ask: "Its next step may only ask you a question or finish.",
        stop: "The run stops; its result says what it tried.",
      };
      const parts = [`Not getting anywhere: ${clip(data.detail, 300)}`, answers[data.action]];
      if (data.error !== undefined) {
        parts.push(`It could not answer (${clip(data.error, 300)}), so the run stops.`);
      }
      if (data.diagnosis !== undefined) {
        parts.push(clip(data.diagnosis, 400));
      }
      const steps = data.next_steps ?? [];
      if (steps.length > 0) {
        parts.push(`Next: ${steps.map((step, index) => `${index + 1}. ${clip(step, 300)}`).join(" ")}`);
      }
      const ends = data.action === "stop" || data.error !== undefined;
      note(scope, record.ts, "alert", parts.join(" "), ends ? "bad" : "warn");
    },
  };

  function push(record) {
    let scope = scopes.get(record.run_id);
    if (scope === undefined) {
      scope = childScope(record.run_id, record.kind === "run_queued" ? record.data : { goal: "", parent_run_id: rootRunId }, record.ts);
    }
    const handler = handlers[record.kind];
    if (handler !== undefined) {
      handler(scope, record);
    }
    onGrow();
  }

  // What the row of the delegated run `runId` keeps up to date for its card, null for a run without a row here.
  const helper = (runId) => scopes.get(runId)?.panel?.helper ?? null;
  // The statuses of the run's delegated runs, in the order they started.
  const helperStatuses = () => [...scopes.values()].filter((scope) => scope.panel?.helper !== undefined).map((scope) => scope.panel.helper.status);
  return {
    element,
    push,
    tick,
    settle,
    knows: (runId) => scopes.has(runId),
    helper,
    helperStatuses,
    draft: (record) => drafts.get(record.id)?.update(record),
  };
}
