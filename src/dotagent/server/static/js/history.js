// What a conversation holds outside one run: the runs before it in the thread, each as the question and its answer, so a
// follow-up reads as the next turn of one chat.

import { api, query } from "./api.js";
import { el, plural, repeatsAnswer } from "./dom.js";
import { icon } from "./icons.js";
import { foldSteps } from "./layout.js";
import { answerMessage, showMessageFiles, showMessagePicks, userMessage } from "./messages.js";
import { pageCard } from "./pageframe.js";
import { prose } from "./prose.js";
import { countKinds, flowIcon, flowWords, toolRow } from "./tools.js";

const THREAD_LIMIT = 200;
// The last turns whose tool calls are read with the page; older ones read them as they come near it (item 374).
const EAGER_TURNS = 6;
// The last of them is read before the page is drawn: its work stands right above the run's own, where a chat opens and
// where the chat goes on from when a message is sent, so it is there whole instead of growing in under the reader.
const READ_FIRST = 1;
const OPS_MARGIN = "800px";
// The events of a run are read this many at a time (the most `GET /runs/{id}/events` gives).
const EVENTS_PAGE = 2000;
// Whether the browser keeps the reader's place itself when something above it grows (CSS scroll anchoring).
const ANCHORS = CSS.supports("overflow-anchor", "auto");
const FINISH_TOOL = "finish";
// A long conversation shows its latest turns and says how many it leaves out.
const SHOWN_TURNS = 40;
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
 * The runs before `run` in its thread, as `{ earlier, read, cut }` (oldest first). A run the thread does not list, such
 * as a delegated one, stands alone: the list is empty. `read` holds what the last turn did (`READ_FIRST`), a run's id to
 * `{ events }`, or to `{ error }`, why they could not be read. `cut` says that the chat has runs before those read,
 * which the page does not show (item 559): it reads the newest `THREAD_LIMIT` of them, the most `GET /runs` gives.
 * Nothing more is read once `signal` aborted.
 */
export async function loadHistory(run, signal) {
  const found = await api.get(`/runs?thread_id=${encodeURIComponent(run.thread_id)}&limit=${THREAD_LIMIT}`);
  const full = found.length === THREAD_LIMIT;
  const runs = conversation(found);
  const at = runs.findIndex((candidate) => candidate.id === run.id);
  if (at === -1) {
    // A run of the chat older than those read has its earlier turns among the runs not read.
    return { earlier: [], read: new Map(), cut: full && run.parent_run_id === null };
  }
  const earlier = runs.slice(0, at);
  const last = earlier.slice(-READ_FIRST).filter((past) => FINISHED.has(past.status));
  const settled = await Promise.allSettled(last.map((past) => allEvents(past.id, signal)));
  const read = new Map();
  last.forEach((past, index) => {
    const outcome = settled[index];
    if (outcome.status === "rejected") {
      read.set(past.id, { error: outcome.reason });
    } else if (outcome.value !== null) {
      read.set(past.id, { events: outcome.value });
    }
  });
  return { earlier, read, cut: full };
}

/**
 * What opens a run that tells its chat that a command in the background ended (item 229): Dot wrote it, not the
 * owner, so it reads as a note, its first line, which says which command ended and how.
 */
export function backgroundNote(goal) {
  return el("div", { class: "note background-note" }, icon("terminal"), el("span", null, goal.split("\n")[0]));
}

/** A stretch of work as a turn's page shows it: one folded line — "Ran 2 commands and read 1 file" — under the mark
 * of the kind its words start with (item 402), that opens to every call and its result, and says beside it, as the
 * run's own page does, how many failed or were cut off. */
function stepsBlock(batch) {
  const kinds = countKinds(batch.tools);
  const troubles = [batch.failed > 0 ? `${batch.failed} failed` : "", batch.cut > 0 ? `${batch.cut} cut off` : ""];
  const said = troubles.filter((words) => words !== "").join(" · ");
  const head = el(
    "summary",
    { class: "steps-head" },
    el("span", { class: "steps-state" }, icon(flowIcon(kinds) ?? "wrench")),
    el("span", { class: "steps-title" }, flowWords(kinds)),
    el("span", { class: "steps-count" }),
    said === "" ? null : el("span", { class: "badge b-warn" }, said),
    el("span", { class: "chev" }, icon("chevron")),
  );
  return el("details", { class: "steps", open: !foldSteps() }, head, el("div", { class: "steps-body" }, batch.rows));
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

/** What a run said and ran, read and drawn into the turn's `flow`; nothing is drawn once `signal` aborted. */
async function fillFlow(past, flow, asked, signal) {
  let events;
  try {
    events = await allEvents(past.id, signal);
  } catch (error) {
    flowNotShown(past, flow, error);
    return;
  }
  if (events !== null && !signal.aborted) {
    drawRead(past, flow, asked, events);
  }
}

/** Draws what was read of a run into the turn's `flow`. A failure to read or to draw it says why, with the way to the
 * run: the turn keeps its question and its answer. */
function drawRead(past, flow, asked, events) {
  try {
    drawFlow(past, flow, asked, events);
  } catch (error) {
    flowNotShown(past, flow, error);
  }
}

function flowNotShown(past, flow, error) {
  console.error("what an earlier run did could not be shown", { run: past.id, error });
  flow.replaceChildren(
    el(
      "div",
      { class: "note" },
      icon("alert"),
      el("span", null, `What this run did could not be shown: ${error.message}`),
      el("a", { class: "small", href: `#/runs/${past.id}` }, "Open run"),
    ),
  );
  flow.hidden = false;
}

/** What a run said and ran, in the order it happened (item 374): its words, the calls of the stretch that followed,
 * its words again — the answer itself is the turn's message, so a line that only repeats it is left out. `events`
 * are all its events, so a call without a result was cut off. The files and picks its message came with go under
 * `asked`, the turn's message, and a message that steered the run stands where it came (item 403), as on the run's
 * own page. */
function drawFlow(past, flow, asked, events) {
  const answer = (past.result ?? "").trim();
  const blocks = [];
  let batch = null;
  // The calls waiting for their results, with the stretch they are in; a finish waits apart, and shows only when it
  // was refused, as on the run's own page.
  const open = new Map();
  const finishing = new Map();
  const place = (tool, args) => {
    if (batch === null) {
      batch = { tools: [], rows: [], failed: 0, cut: 0 };
      blocks.push(batch);
    }
    const row = toolRow({ tool, args });
    batch.tools.push(tool);
    batch.rows.push(row.node);
    return { row, batch };
  };
  const message = asked.classList.contains("user-message") ? asked : null;
  // The files and picks of the message go in with the flow, inside keepPlace's change: a browser without scroll
  // anchoring would otherwise see the reader's text pushed down by their height.
  let files = null;
  let picks = null;
  for (const event of events) {
    if (event.kind === "assistant") {
      const said = (event.data.text ?? "").trim();
      if (said !== "" && !repeatsAnswer(said, answer)) {
        batch = null;
        blocks.push({ say: said });
      }
    } else if (event.kind === "attachments" && message !== null) {
      files = event.data.files;
    } else if (event.kind === "picks" && message !== null) {
      picks = event.data;
    } else if (event.kind === "steer") {
      batch = null;
      // Dot's words that you did a step kept for you (item 482) are no message of yours.
      blocks.push(
        event.data.vouches === false
          ? { kept: event.data.text }
          : { steer: { text: event.data.text, files: event.data.files ?? [], sentAt: event.ts } },
      );
    } else if (event.kind === "page_shown") {
      // A page the run showed (`show_page`, item 193) stands where it came, in its frame, as on the run's own page.
      batch = null;
      blocks.push({ page: { id: event.data.page_id, title: event.data.title } });
    } else if (event.kind === "tool_call" && event.data.tool === FINISH_TOOL) {
      finishing.set(event.data.call_id, event.data.args);
    } else if (event.kind === "tool_call") {
      open.set(event.data.call_id, place(event.data.tool, event.data.args));
    } else if (event.kind === "tool_result") {
      const refused = finishing.get(event.data.call_id);
      if (refused !== undefined) {
        finishing.delete(event.data.call_id);
        if (event.data.ok) {
          continue;
        }
        open.set(event.data.call_id, place(FINISH_TOOL, refused));
      }
      const entry = open.get(event.data.call_id);
      if (entry !== undefined) {
        entry.row.finish(event.data);
        if (!event.data.ok) {
          entry.batch.failed += 1;
        }
        open.delete(event.data.call_id);
      }
    }
  }
  for (const entry of open.values()) {
    if (entry.row.settle()) {
      entry.batch.cut += 1;
    }
  }
  const nodes = blocks.map((block) =>
    block.page !== undefined
      ? pageCard(block.page.id, block.page.title)
      : block.steer !== undefined
      ? userMessage({ text: block.steer.text, sentAt: block.steer.sentAt, files: block.steer.files, editFromHere: null })
      : block.kept !== undefined
      ? el("div", { class: "note" }, icon("hand"), el("span", null, block.kept))
      : block.say === undefined
        ? stepsBlock(block)
        : el("div", { class: "turn-say" }, prose(block.say)),
  );
  keepPlace(flow, () => {
    if (files !== null) {
      showMessageFiles(message, files);
    }
    if (picks !== null) {
      showMessagePicks(message, picks);
    }
    flow.replaceChildren(...nodes);
    flow.hidden = nodes.length === 0;
  });
}

/** The box the conversation scrolls in: its own on a phone (`html.chat-shell`, run.css), else null for the window. */
function scrollBox(node) {
  return document.documentElement.classList.contains("chat-shell") ? node.closest(".run-thread") : null;
}

/** `change()` fills the turn's `flow`. A browser that keeps the reader's place by itself does (scroll anchoring);
 * elsewhere, when the work goes in above what is seen, the conversation scrolls by as much as what follows it moved,
 * the turn's answer, so what is read stays where it was. Work that goes in where it is seen grows in place. */
function keepPlace(flow, change) {
  if (ANCHORS) {
    change();
    return;
  }
  const box = scrollBox(flow);
  const seenFrom = box === null ? 0 : box.getBoundingClientRect().top;
  const follows = flow.nextElementSibling ?? flow.parentElement;
  const before = follows.getBoundingClientRect().top;
  change();
  if (before > seenFrom) {
    return;
  }
  const moved = follows.getBoundingClientRect().top - before;
  if (moved === 0) {
    return;
  }
  if (box === null) {
    window.scrollBy(0, moved);
  } else {
    box.scrollTop += moved;
  }
}

/** Fills a turn's operations once it comes near what is seen, once: near the window, or on a phone near the box the
 * conversation scrolls in, which the turn is in once the page stands. The watch ends with the page (`signal`). */
function whenNear(element, ops, asked, run, signal) {
  requestAnimationFrame(() => {
    if (signal.aborted) {
      return;
    }
    const watch = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          watch.disconnect();
          void fillFlow(run, ops, asked, signal);
        }
      },
      { root: scrollBox(element), rootMargin: OPS_MARGIN },
    );
    watch.observe(element);
    signal.addEventListener("abort", () => watch.disconnect(), { once: true });
  });
}

/** One earlier run as what was asked, what it did and how it ended; a run still going keeps the foot of its message back.
 * What the run did is drawn at once when `known` holds it (`loadHistory`'s `read`: `{ events }`, or `{ error }`), else
 * read now for an `eager` turn and once the turn comes near what is seen for the others. */
function turn(past, editFromHereOf, eager, known, signal) {
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
    if (known !== undefined) {
      if ("error" in known) {
        flowNotShown(past, ops, known.error);
      } else {
        drawRead(past, ops, asked, known.events);
      }
    } else if (eager) {
      void fillFlow(past, ops, asked, signal);
    } else {
      whenNear(element, ops, asked, past, signal);
    }
  }
  return element;
}

/**
 * The earlier turns of the conversation (`loadHistory`'s `{ earlier, read, cut }`) as one block, or null when there
 * are none. `editFromHereOf(run)` gives what
 * rewinds the chat to before the message of an earlier run (item 282), or null where it cannot be; `signal` ends with
 * the page, and what the turns read with it.
 */
export function earlierTurns({ earlier, read, cut }, editFromHereOf, signal) {
  if (earlier.length === 0 && !cut) {
    return null;
  }
  const shown = earlier.slice(-SHOWN_TURNS);
  const left = earlier.length - shown.length;
  // A chat of more runs than the page reads says so, with no count and no first run (item 559): it knows neither.
  const note = cut
    ? el("div", { class: "note" }, icon("list"), el("span", null, `Earlier runs of this chat are not shown: the page reads its latest ${THREAD_LIMIT}.`))
    : left === 0
      ? null
      : el(
          "div",
          { class: "note" },
          icon("list"),
          el("span", null, `${plural(left, "earlier run is", "earlier runs are")} not shown.`),
          el("a", { class: "small", href: `#/runs/${earlier[0].id}` }, "Open the first run"),
        );
  return el(
    "div",
    { class: "history", role: "region", "aria-label": "Earlier in this conversation" },
    note,
    shown.map((past, index) => turn(past, editFromHereOf, index >= shown.length - EAGER_TURNS, read.get(past.id), signal)),
  );
}

