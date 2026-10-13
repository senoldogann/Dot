// Activity: everything in motion in one list. What waits for you, what works now and what ended today, from
// `GET /activity`; a row opens the run, the campaign or the schedules. Reloaded on the lifecycle events and with the
// status refresh while something works.

import { activityRow, LIFECYCLE_KINDS } from "../activityrow.js";
import { api } from "../api.js";
import { debounce, el } from "../dom.js";
import { nestRows } from "../nesting.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead, segmented } from "../panels.js";
import { onChange, onLifecycle, onStreamBack, readFailed } from "../state.js";

const SECTIONS = [
  { key: "waiting", title: "Waiting for you", empty: "Nothing waits for you", hint: "A run that asks a question or an approval shows here." },
  { key: "working", title: "Working", empty: "Nothing is working right now", hint: "A run you start shows here while it works." },
  { key: "recent", title: "Ended today", empty: "No run ended today", hint: "A run that ends shows here with what it took and cost." },
];
// The filter over the kinds of rows: every kind, or one family of them.
const FILTERS = [
  { id: "all", label: "All", kinds: null },
  { id: "runs", label: "Runs", kinds: new Set(["run", "delegated", "approval", "question", "takeover"]) },
  { id: "campaigns", label: "Campaigns", kinds: new Set(["campaign"]) },
  { id: "schedules", label: "Schedules", kinds: new Set(["schedule"]) },
];
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
      ...SECTIONS.map(({ key, title, empty, hint }) => {
        const items = shown(view[key]);
        return el(
          "section",
          { class: "card activity-section", "data-section": key },
          el("div", { class: "card-head" }, el("span", null, title), el("span", { class: "small muted" }, String(items.length))),
          items.length === 0
            ? emptyPanel(empty, hint)
            : el(
                "div",
                { class: "activity-list" },
                ...nestRows(items).map(({ row: item, children }) => [
                  activityRow(item, false),
                  children.length === 0 ? null : el("div", { class: "activity-children" }, children.map((kid) => activityRow(kid, true))),
                ]),
              ),
        );
      }),
    );
  }

  // The newest load answers for the page: an older answer that comes after it is dropped, not painted over it.
  let ticket = 0;

  async function load() {
    const mine = ++ticket;
    try {
      const fresh = await api.get("/activity");
      if (disposed || mine !== ticket) {
        return;
      }
      view = fresh;
      failure = null;
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (view === null) {
        failure = error;
      } else {
        readFailed("The activity", error);
        return;
      }
    }
    paint();
  }

  // An event and the status refresh it starts come together: they make one load.
  const loadSoon = debounce(() => {
    if (!disposed) {
      load();
    }
  }, 250);
  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      loadSoon();
    }
  });
  const stopBack = onStreamBack(loadSoon);
  const stopChange = onChange(() => {
    if (view !== null && view.working.length > 0) {
      loadSoon();
    }
  });
  paint();
  load();
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
    stopChange();
  };
}
