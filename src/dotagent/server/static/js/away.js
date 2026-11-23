// While you were away (item 466): what your schedules and campaigns did since you last marked it read, on any of your
// devices (item 1356: Dot keeps the moment, `GET` and `POST /away/read`), a card over the Home's Today strip, read from
// `GET /away` and kept fresh by the lifecycle stream. The first visit from any device marks the moment and shows
// nothing, so the card never pours out history; Mark as read moves the moment on for every device.

import { LIFECYCLE_KINDS } from "./activityrow.js";
import { api, query } from "./api.js";
import { ago, el } from "./dom.js";
import { awayLine, decidedBy } from "./awaywords.js";
import { readPref, removePref } from "./prefs.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt } from "./ui.js";

// The moment this browser marked the card read before Dot kept it (item 1356), in seconds since the epoch: the first
// mark Dot makes starts from it, and the browser forgets it.
const OLD_READ_KEY = "dot.away.read";
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

  /**
   * The moment the card reads from: Dot's, as any device last marked it. The first visit from any device marks it
   * (from what this browser kept before Dot did, else now), so that what came before is not news.
   */
  async function readMoment() {
    const marked = await api.get("/away/read");
    if (marked.read_at !== null) {
      return marked.read_at;
    }
    const kept = Number(readPref(OLD_READ_KEY));
    const first = Number.isFinite(kept) && kept > 0 ? kept : Date.now() / 1000;
    const set = await api.post("/away/read", { until: first });
    removePref(OLD_READ_KEY);
    return set.read_at;
  }

  async function markRead() {
    if (view === null) {
      return;
    }
    const until = view.until;
    // An answer still on its way was asked from the old moment: it is stale, and must not open the card again.
    ticket += 1;
    await api.post("/away/read", { until });
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
        el("div", { class: "row" }, el("span", { class: "spacer" }), el("button", { class: "btn sm", type: "button", onclick: () => attempt(markRead) }, "Mark as read")),
      ),
    );
  }

  async function load() {
    const mine = ++ticket;
    try {
      const since = await readMoment();
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
