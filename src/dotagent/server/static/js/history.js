// What a conversation holds outside one run: the runs before it in the thread, each as the question and its answer, so a
// follow-up reads as the next turn of one chat, and the way on to the run that continued it.

import { api, query } from "./api.js";
import { clip, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { foldSteps } from "./layout.js";
import { answerMessage, userMessage } from "./messages.js";
import { prose } from "./prose.js";
import { countKinds, flowWords, toolRow } from "./tools.js";

const THREAD_LIMIT = 200;
// The last turns whose tool calls are read with the page; older ones read them as they come near it (item 374).
const EAGER_TURNS = 6;
const OPS_MARGIN = "800px";
// The events of a run are read this many at a time (the most `GET /runs/{id}/events` gives).
const EVENTS_PAGE = 2000;
const FINISH_TOOL = "finish";
// A long conversation shows its latest turns and says how many it leaves out.
const SHOWN_TURNS = 40;
const GOAL_CHARS = 90;
const FINISHED = new Set(["done", "failed", "cancelled"]);
// The source of a run that goes on from where the run before it was cut off (agent/resume.py): its goal is that run's
// goal, so it is not shown as asked again.
export const RESUME_SOURCE = "resume";
// The source of a run that tells the chat that its command in the background ended (item 229).
export const BACKGROUND_SOURCE = "background";

/** The runs of a thread that the user started, oldest first; delegated runs belong to the run that delegated them. */
function conversation(runs) {
  return runs.filter((candidate) => candidate.parent_run_id === null).toSorted((a, b) => a.created_at - b.created_at);
}

/**
 * The runs before and after `run` in its thread, as `{ earlier, later }` (oldest first). A run the thread does not list, such
 * as a delegated one, stands alone: both lists are empty.
 */
export async function loadHistory(run) {
  const found = await api.get(`/runs?thread_id=${encodeURIComponent(run.thread_id)}&limit=${THREAD_LIMIT}`);
  const runs = conversation(found);
  const at = runs.findIndex((candidate) => candidate.id === run.id);
  if (at === -1) {
    return { earlier: [], later: [] };
  }
  return { earlier: runs.slice(0, at), later: runs.slice(at + 1) };
}

/**
 * What opens a run that tells its chat that a command in the background ended (item 229): Dot wrote it, not the
 * owner, so it reads as a note, its first line, which says which command ended and how.
 */
export function backgroundNote(goal) {
  return el("div", { class: "note background-note" }, icon("terminal"), el("span", null, goal.split("\n")[0]));
}

/** A stretch of work as a turn's page shows it: one folded line — "Ran 2 commands and read 1 file" — that opens to
 * every call and its result. */
function stepsBlock(tools, rows) {
  const head = el(
    "summary",
    { class: "steps-head" },
    el("span", { class: "steps-state" }, icon("check")),
    el("span", { class: "steps-title" }, flowWords(countKinds(tools))),
    el("span", { class: "steps-count" }),
    el("span", { class: "chev" }, icon("chevron")),
  );
  return el("details", { class: "steps", open: !foldSteps() }, head, el("div", { class: "steps-body" }, rows));
}

/** Every event of the run `runId`, a page at a time after the last one read; null once `signal` aborted (the page
 * went), when what is read is not wanted any more. */
async function allEvents(runId, signal) {
  const events = [];
  let after = 0;
  for (;;) {
    if (signal.aborted) {
      return null;
    }
    const page = await api.get(`/runs/${runId}/events${query({ after, limit: EVENTS_PAGE })}`);
    events.push(...page);
    if (page.length < EVENTS_PAGE) {
      return events;
    }
    after = page[page.length - 1].id;
  }
}

/** What a run said and ran, in the order it happened (item 374): its words, the calls of the stretch that followed,
 * its words again — the answer itself is the turn's message, so a line that only repeats it is left out. All its
 * events are read, so a call without a result was cut off; nothing is drawn once `signal` aborted. */
async function fillFlow(past, flow, signal) {
  let events;
  try {
    events = await allEvents(past.id, signal);
  } catch (error) {
    // The turn keeps its question and its answer; what it did is on its own page.
    flow.replaceChildren(
      el(
        "div",
        { class: "note" },
        icon("alert"),
        el("span", null, `What this run did could not be read: ${error.message}`),
        el("a", { class: "small", href: `#/runs/${past.id}` }, "Open run"),
      ),
    );
    flow.hidden = false;
    return;
  }
  if (events === null || signal.aborted) {
    return;
  }
  const answer = (past.result ?? "").trim();
  const blocks = [];
  let batch = null;
  const open = new Map();
  for (const event of events) {
    if (event.kind === "assistant") {
      const said = (event.data.text ?? "").trim();
      if (said !== "" && said !== answer) {
        batch = null;
        blocks.push({ say: said });
      }
    } else if (event.kind === "tool_call" && event.data.tool !== FINISH_TOOL) {
      const row = toolRow({ tool: event.data.tool, args: event.data.args });
      open.set(event.data.call_id, row);
      if (batch === null) {
        batch = { tools: [], rows: [] };
        blocks.push(batch);
      }
      batch.tools.push(event.data.tool);
      batch.rows.push(row.node);
    } else if (event.kind === "tool_result") {
      const row = open.get(event.data.call_id);
      if (row !== undefined) {
        row.finish(event.data);
        open.delete(event.data.call_id);
      }
    }
  }
  for (const row of open.values()) {
    row.settle();
  }
  const nodes = blocks.map((block) =>
    block.say === undefined ? stepsBlock(block.tools, block.rows) : el("div", { class: "turn-say" }, prose(block.say)),
  );
  flow.replaceChildren(...nodes);
  flow.hidden = nodes.length === 0;
}

/** Fills a turn's operations once it comes near the window, once; the watch ends with the page (`signal`). */
function whenNear(element, ops, run, signal) {
  const watch = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        watch.disconnect();
        void fillFlow(run, ops, signal);
      }
    },
    { rootMargin: OPS_MARGIN },
  );
  watch.observe(element);
  signal.addEventListener("abort", () => watch.disconnect(), { once: true });
}

/** One earlier run as what was asked, what it did and how it ended; a run still going keeps the foot of its message back. */
function turn(past, editFromHereOf, eager, signal) {
  const href = `#/runs/${past.id}`;
  const ended = FINISHED.has(past.status);
  const ops = el("div", { class: "turn-ops", hidden: true });
  const answer = ended
    ? answerMessage({ actorId: past.agent_id ?? "dot", status: past.status, text: past.result, endedAt: past.finished_at, startedAt: past.created_at, href: null })
    : el("div", { class: "note" }, icon("clock"), el("span", null, "This run is still going."), el("a", { class: "small", href }, "Open run"));
  const asked =
    past.source === RESUME_SOURCE
      ? el("div", { class: "note restart-note" }, icon("bolt"), el("span", null, "Resumed where the run above was cut off"))
      : past.source === BACKGROUND_SOURCE
        ? backgroundNote(past.goal)
        : userMessage({ text: past.goal, sentAt: past.created_at, files: [], editFromHere: editFromHereOf(past) });
  const element = el("section", { class: ended ? "turn" : "turn turn-working" }, asked, ops, answer);
  if (ended) {
    if (eager) {
      void fillFlow(past, ops, signal);
    } else {
      whenNear(element, ops, past, signal);
    }
  }
  return element;
}

/**
 * The earlier turns of the conversation as one block, or null when there are none. `editFromHereOf(run)` gives what
 * rewinds the chat to before the message of an earlier run (item 282), or null where it cannot be; `signal` ends with
 * the page, and what the turns read with it.
 */
export function earlierTurns(earlier, editFromHereOf, signal) {
  if (earlier.length === 0) {
    return null;
  }
  const shown = earlier.slice(-SHOWN_TURNS);
  const left = earlier.length - shown.length;
  return el(
    "div",
    { class: "history", role: "region", "aria-label": "Earlier in this conversation" },
    left === 0
      ? null
      : el(
          "div",
          { class: "note" },
          icon("list"),
          el("span", null, `${plural(left, "earlier run is", "earlier runs are")} not shown.`),
          el("a", { class: "small", href: `#/runs/${earlier[0].id}` }, "Open the first run"),
        ),
    shown.map((past, index) => turn(past, editFromHereOf, index >= shown.length - EAGER_TURNS, signal)),
  );
}

/** The line that leads on to the run that continued the conversation, or null when nothing came after. */
export function continuedIn(later) {
  if (later.length === 0) {
    return null;
  }
  const next = later[0];
  return el(
    "nav",
    { class: "continued", "aria-label": "Later in this conversation" },
    el(
      "a",
      { href: `#/runs/${next.id}`, title: next.goal },
      el("span", null, "The conversation continues"),
      el("span", { class: "muted" }, later.length === 1 ? clip(next.goal, GOAL_CHARS) : `${clip(next.goal, GOAL_CHARS)} · ${later.length - 1} more`),
      icon("arrowRight"),
    ),
  );
}
