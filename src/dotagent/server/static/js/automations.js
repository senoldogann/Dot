// The Automations of a chat (item 1334), a section of the run's side panel: the schedules and loops that belong to this
// chat, as a developer tool's Automations list shows them. A row is a calendar-clock mark with a green dot while the
// schedule is on, its title and, under it, how it repeats or when it runs and how it stands ("Every 6 hours · paused");
// a pencil that opens the schedule's editor on the Schedules page, a play that runs it now, and a switch that pauses it or puts
// it on again, which the scheduler reads at the schedule's next firing. The head has a way to all the schedules. The
// daemon tells of no change of a schedule, so the rows are read again when the chat's runs come and go and as the page
// comes back, as the loops over the composer are (loops.js). A chat with none shows no section.

import { api } from "./api.js";
import { automationWords } from "./automationwords.js";
import { clip, debounce, el, setText } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { onLifecycle, onStreamBack } from "./state.js";
import { attempt, toast } from "./ui.js";

const TITLE_CHARS = 80;
const RUN_MOMENTS = new Set(["run_queued", "run_finished"]);
const READ_AGAIN_MS = 400;

/**
 * The section of the chat `threadId`. Returns `{ element, refresh(), dispose() }`; `refresh()` reads the list again and
 * resolves to how many automations the chat has.
 */
export function automationsPanel({ threadId }) {
  const list = el("ul", { class: "automation-list" });
  const problem = el("p", { class: "note warn", hidden: true });
  const count = el("span", { class: "automation-count", hidden: true });
  const manage = el(
    "a",
    { class: "icon-btn automation-manage", href: "#/schedules", title: t("automations.manage"), "aria-label": t("automations.manage") },
    icon("sliders"),
  );
  const element = el(
    "section",
    { class: "panel-section automations", "aria-label": t("automations.region"), hidden: true },
    el("div", { class: "panel-section-head" }, el("h3", { class: "panel-section-title" }, t("automations.title"), count), manage),
    el("div", { class: "panel-section-body" }, problem, list),
  );
  let disposed = false;
  // The schedules a request is out for, so that a second press waits for the first.
  const busy = new Set();

  function row({ schedule: view, loop }, now) {
    const record = view.record;
    const words = automationWords(view, loop, now);
    const dot = el("span", { class: "automation-dot", "data-state": words.failed ? "failed" : words.active ? "on" : "off", "aria-hidden": "true" });
    const mark = el("span", { class: "automation-mark" }, icon("calendarClock"), dot);
    const title = el("span", { class: "automation-title" }, clip(words.title, TITLE_CHARS));
    const line = el("span", { class: "automation-line" }, words.line);
    const edit = el(
      "a",
      {
        class: "icon-btn automation-edit",
        href: `#/schedules?schedule=${encodeURIComponent(record.id)}&edit=1`,
        title: t("automations.edit"),
        "aria-label": t("automations.editLabel", { name: words.title }),
      },
      icon("pencil"),
    );
    const play = el(
      "button",
      {
        class: "icon-btn automation-run",
        type: "button",
        title: t("automations.run"),
        "aria-label": t("automations.runLabel", { name: words.title }),
        disabled: words.running,
        onclick: () => attempt(() => runNow(view, words.title)),
      },
      icon("play"),
    );
    const toggle = el("button", {
      class: "switch automation-switch",
      type: "button",
      role: "switch",
      "aria-checked": String(record.enabled),
      "aria-label": t(record.enabled ? "automations.switchOff" : "automations.switchOn", { name: words.title }),
      title: words.done ? t("automations.doneHint") : t(record.enabled ? "automations.pause" : "automations.resume"),
      disabled: words.done,
      onclick: () => attempt(() => flip(view)),
    });
    return el(
      "li",
      { class: "automation-row", "data-schedule": record.id, "data-loop": String(loop), title: record.name },
      mark,
      el("span", { class: "automation-text" }, title, line),
      edit,
      play,
      toggle,
    );
  }

  function paint(items) {
    const now = Date.now() / 1000;
    list.replaceChildren(...items.map((item) => row(item, now)));
    setText(count, items.length === 0 ? "" : String(items.length));
    count.hidden = items.length === 0;
    problem.hidden = true;
    element.hidden = items.length === 0;
  }

  async function load() {
    try {
      const items = await api.get(`/threads/${encodeURIComponent(threadId)}/automations`);
      if (!disposed) {
        paint(items);
      }
      return items.length;
    } catch (error) {
      if (!disposed) {
        setText(problem, t("automations.unread", { problem: error.message }));
        problem.hidden = false;
        element.hidden = false;
      }
      return 0;
    }
  }

  /** Switch the schedule off or on; the scheduler reads it at the schedule's next firing. */
  async function flip(view) {
    const id = view.record.id;
    if (busy.has(id)) {
      return;
    }
    busy.add(id);
    try {
      await api.post(`/schedules/${encodeURIComponent(id)}/${view.record.enabled ? "disable" : "enable"}`, null);
    } finally {
      busy.delete(id);
      await load();
    }
  }

  /** Start a run of the schedule now, as its time would; its time and its switch stay as they are. */
  async function runNow(view, name) {
    const id = view.record.id;
    if (busy.has(id)) {
      return;
    }
    busy.add(id);
    try {
      await api.post(`/schedules/${encodeURIComponent(id)}/run`, null);
      toast("ok", t("automations.started", { name: clip(name, TITLE_CHARS) }));
    } finally {
      busy.delete(id);
      await load();
    }
  }

  const readAgain = debounce(() => load(), READ_AGAIN_MS);
  const stopLifecycle = onLifecycle((event) => {
    if (RUN_MOMENTS.has(event.kind)) {
      readAgain();
    }
  });
  const stopBack = onStreamBack(load);
  load();
  return {
    element,
    refresh: load,
    dispose: () => {
      disposed = true;
      stopLifecycle();
      stopBack();
    },
  };
}
