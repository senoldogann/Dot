// Activity: everything in motion in one list. What waits for you, what works now and what ended today, from
// `GET /activity`; a row opens the run, the campaign or the schedules. Reloaded on the lifecycle events and with the
// status refresh while something works.

import { api } from "../api.js";
import { ago, compact, duration, el } from "../dom.js";
import { icon } from "../icons.js";
import { nestRows } from "../nesting.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead, segmented, statusBadge } from "../panels.js";
import { onChange, onLifecycle } from "../state.js";

const LIFECYCLE_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided", "takeover", "handback"]);
const SECTIONS = [
  { key: "waiting", title: "Waiting for you", empty: "Nothing waits for you." },
  { key: "working", title: "Working", empty: "Nothing is working right now." },
  { key: "recent", title: "Ended today", empty: "No run ended today." },
];
// The filter over the kinds of rows: every kind, or one family of them.
const FILTERS = [
  { id: "all", label: "All", kinds: null },
  { id: "runs", label: "Runs", kinds: new Set(["run", "delegated", "approval", "question", "takeover"]) },
  { id: "campaigns", label: "Campaigns", kinds: new Set(["campaign"]) },
  { id: "schedules", label: "Schedules", kinds: new Set(["schedule"]) },
];
const KIND_ICON = {
  run: "bolt",
  delegated: "branch",
  campaign: "target",
  approval: "shield",
  question: "help",
  takeover: "hand",
  schedule: "calendar",
  reflection: "sparkle",
};
const KIND_WORD = {
  run: "run",
  delegated: "delegated run",
  campaign: "campaign session",
  approval: "approval",
  question: "question",
  takeover: "taken over",
  schedule: "schedule",
  reflection: "reflection",
};

function row(item, child) {
  const facts = [KIND_WORD[item.kind] ?? item.kind, item.channel, item.detail].filter((part) => part !== "");
  if (item.steps !== null && item.steps > 0 && item.minutes === null) {
    facts.push(item.steps === 1 ? "1 step" : `${item.steps} steps`);
  }
  if (item.minutes !== null) {
    // A run that ended: how long it took and what it cost in tokens.
    facts.push(`${duration(item.minutes * 60)} · ${compact(item.tokens)} tokens`);
  }
  return el(
    "a",
    { class: child ? "activity-row activity-child" : "activity-row", href: item.href, "aria-label": `${item.title} (${facts.join(", ")})` },
    el("span", { class: "activity-icon" }, icon(KIND_ICON[item.kind] ?? "bolt")),
    el(
      "span",
      { class: "activity-main" },
      el("span", { class: "activity-title" }, item.title),
      el("span", { class: "small muted" }, facts.join(" · ")),
    ),
    el("span", { class: "activity-side" }, item.status === "scheduled" ? null : statusBadge(item.status), el("span", { class: "small muted" }, ago(item.since))),
  );
}

export function mount(root, ctx) {
  let view = null;
  let failure = null;
  let filter = "all";
  let disposed = false;
  const body = el("div", { class: "stack" });
  const filters = segmented(
    FILTERS.map(({ id, label }) => ({ id, label, hint: `Show ${label.toLowerCase()}` })),
    filter,
    (id) => {
      filter = id;
      paint();
    },
  );
  root.replaceChildren(pageHead("Activity", "What waits for you, what works now, what ended today.", filters.element), body);

  function shown(items) {
    const kinds = FILTERS.find((option) => option.id === filter)?.kinds ?? null;
    return kinds === null ? items : items.filter((item) => kinds.has(item.kind));
  }

  function paint() {
    if (failure !== null) {
      body.replaceChildren(errorPanel(failure, () => load()));
      return;
    }
    if (view === null) {
      body.replaceChildren(loadingPanel());
      return;
    }
    body.replaceChildren(
      ...SECTIONS.map(({ key, title, empty }) => {
        const items = shown(view[key]);
        return el(
          "section",
          { class: "card activity-section", "data-section": key },
          el("div", { class: "card-head" }, el("span", null, title), el("span", { class: "small muted" }, String(items.length))),
          items.length === 0
            ? emptyPanel("clock", empty, "")
            : el(
                "div",
                { class: "activity-list" },
                ...nestRows(items).map(({ row: item, children }) => [
                  row(item, false),
                  children.length === 0 ? null : el("div", { class: "activity-children" }, children.map((kid) => row(kid, true))),
                ]),
              ),
        );
      }),
    );
  }

  async function load() {
    try {
      const fresh = await api.get("/activity");
      if (disposed) {
        return;
      }
      view = fresh;
      failure = null;
    } catch (error) {
      if (disposed) {
        return;
      }
      if (view === null) {
        failure = error;
      } else {
        console.warn("the activity could not be refreshed", error);
        return;
      }
    }
    paint();
  }

  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      load();
    }
  });
  const stopChange = onChange(() => {
    if (view !== null && view.working.length > 0) {
      load();
    }
  });
  paint();
  load();
  return () => {
    disposed = true;
    stopLifecycle();
    stopChange();
  };
}
