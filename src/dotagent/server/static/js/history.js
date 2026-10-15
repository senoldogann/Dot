// What a conversation holds outside one run: the runs before it in the thread, each as the question and its answer, so a
// follow-up reads as the next turn of one chat, and the way on to the run that continued it.

import { api } from "./api.js";
import { clip, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { answerMessage, userMessage } from "./messages.js";

const THREAD_LIMIT = 200;
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

/** One earlier run as what was asked and how it ended; a run still going keeps the foot of its message back. */
function turn(past, editFromHereOf) {
  const href = `#/runs/${past.id}`;
  const ended = FINISHED.has(past.status);
  const answer = ended
    ? answerMessage({ actorId: past.agent_id ?? "dot", status: past.status, text: past.result, endedAt: past.finished_at, startedAt: past.created_at, href })
    : el("div", { class: "note" }, icon("clock"), el("span", null, "This run is still going."), el("a", { class: "small", href }, "Open run"));
  const asked =
    past.source === RESUME_SOURCE
      ? el("div", { class: "note restart-note" }, icon("bolt"), el("span", null, "Resumed where the run above was cut off"))
      : past.source === BACKGROUND_SOURCE
        ? backgroundNote(past.goal)
        : userMessage({ text: past.goal, sentAt: past.created_at, files: [], editFromHere: editFromHereOf(past) });
  return el("section", { class: ended ? "turn" : "turn turn-working" }, asked, answer);
}

/**
 * The earlier turns of the conversation as one block, or null when there are none. `editFromHereOf(run)` gives what
 * rewinds the chat to before the message of an earlier run (item 282), or null where it cannot be.
 */
export function earlierTurns(earlier, editFromHereOf) {
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
    shown.map((past) => turn(past, editFromHereOf)),
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
