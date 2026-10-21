// While you were away (item 466): what your schedules and campaigns did since this browser last marked it read, a card
// over the Home's Today strip, read from `GET /away` and kept fresh by the lifecycle stream. Its first visit marks the
// moment and shows nothing, so the card never pours out history; Mark as read moves the moment on.

import { LIFECYCLE_KINDS } from "./activityrow.js";
import { api, query } from "./api.js";
import { ago, el } from "./dom.js";
import { awayLine, decidedBy } from "./awaywords.js";
import { readPref, writePref } from "./prefs.js";
import { onLifecycle, onStreamBack } from "./state.js";

// The moment this browser last marked the card read, in seconds since the epoch.
const READ_KEY = "dot.away.read";
// The runs, and the runs with the most decisions, it shows before "Show N more", as the Today strip does (item 364).
const ROWS_SHOWN = 2;
const STATUS = {
  done: { word: "done", tone: "b-ok" },
  failed: { word: "failed", tone: "b-bad" },
  cancelled: { word: "stopped", tone: "b-muted" },
  queued: { word: "working", tone: "b-info" },
  running: { word: "working", tone: "b-info" },
  blocked: { word: "working", tone: "b-info" },
};

function runRow(run) {
  const status = STATUS[run.status] ?? { word: run.status, tone: "b-muted" };
  return el(
    "li",
    { class: "firing", dataset: { awayRun: run.run_id } },
    el(
      "div",
      { class: "row-meta" },
      el("span", { class: `badge ${status.tone}` }, status.word),
      el("a", { class: "firing-title", href: `#/runs/${run.run_id}` }, run.title),
      el("span", { class: "faint" }, ago(run.finished_at ?? run.created_at)),
    ),
    run.answer === "" ? null : el("div", { class: "row-text muted" }, run.answer),
  );
}

function decisionRow(run) {
  return el(
    "li",
    { class: "firing", dataset: { awayDecided: run.run_id } },
    el(
      "div",
      { class: "row-meta" },
      el("a", { class: "firing-title", href: `#/runs/${run.run_id}` }, run.title),
      el("span", { class: "faint" }, decidedBy(run.yolo, run.reviewer, run.denied)),
    ),
  );
}

/** A "Show N more" that opens the rest of the list `list` in place, or null when it has no more. */
function moreButton(list, rest, onOpen) {
  return rest === 0 ? null : el("button", { class: "btn ghost sm", type: "button", dataset: { awayMore: list }, onclick: onOpen }, `Show ${rest} more`);
}

/** Build the card: `element` (hidden while there is nothing to tell) and `dispose()`. */
export function buildAway() {
  const element = el("section", { class: "home-away", "aria-label": "While you were away", hidden: true });
  let disposed = false;
  let view = null;
  // Grows with every load, so a slow answer never overwrites a newer one (nor gives Mark as read an older moment).
  let ticket = 0;
  // The lists whose "Show N more" was pressed: they show all their rows until the page is left.
  const opened = new Set();
  // The first visit of this browser marks the moment: what came before it is not news here.
  if (readPref(READ_KEY) === null) {
    writePref(READ_KEY, String(Date.now() / 1000));
  }

  function markRead() {
    if (view !== null) {
      writePref(READ_KEY, String(view.until));
    }
    view = null;
    element.hidden = true;
    element.replaceChildren();
  }

  function paint() {
    const line = view === null ? "" : awayLine(view);
    element.hidden = line === "";
    if (line === "") {
      element.replaceChildren();
      return;
    }
    const open = (list) => {
      opened.add(list);
      paint();
    };
    const runs = opened.has("runs") ? view.runs : view.runs.slice(0, ROWS_SHOWN);
    const decided = opened.has("decisions") ? view.decision_runs : view.decision_runs.slice(0, ROWS_SHOWN);
    element.replaceChildren(
      el("h2", { class: "section-label" }, "While you were away"),
      el(
        "div",
        { class: "card card-pad stack" },
        el("p", { class: "away-line" }, line),
        view.runs.length === 0 ? null : el("ul", { class: "firings-list" }, ...runs.map(runRow)),
        moreButton("runs", view.runs.length - runs.length, () => open("runs")),
        view.decision_runs.length === 0
          ? null
          : el(
              "div",
              { class: "stack tight away-decisions" },
              el("div", { class: "hint" }, "Most decisions made for you"),
              el("ul", { class: "firings-list" }, ...decided.map(decisionRow)),
              moreButton("decisions", view.decision_runs.length - decided.length, () => open("decisions")),
            ),
        el("div", { class: "row" }, el("span", { class: "spacer" }), el("button", { class: "btn sm", type: "button", onclick: markRead }, "Mark as read")),
      ),
    );
  }

  async function load() {
    const mine = ++ticket;
    const since = Number(readPref(READ_KEY));
    try {
      const fresh = await api.get(`/away${query({ since })}`);
      if (!disposed && mine === ticket) {
        view = fresh;
        paint();
      }
    } catch (error) {
      if (!disposed && mine === ticket) {
        console.warn("what happened while you were away could not be read", error);
      }
    }
  }

  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      load();
    }
  });
  const stopBack = onStreamBack(load);
  load();
  return {
    element,
    dispose: () => {
      disposed = true;
      stopLifecycle();
      stopBack();
    },
  };
}
