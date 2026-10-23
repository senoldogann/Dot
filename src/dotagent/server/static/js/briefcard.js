// The Morning brief card at the head of Schedules (item 570): a switch, the time of day, every day or the weekdays, and the
// owner's own lines ("Also tell me about…"). On, Dot writes the brief at that time as a schedule of its own (`GET` and
// `PUT /api/brief`), a run that only reads, whose first line goes to the phone and which opens in the chat "Morning
// brief". The schedule shows in the list below with its label and switches off there too, which the card follows.

import { api } from "./api.js";
import { clip, el, joined } from "./dom.js";
import { icon } from "./icons.js";
import { field, inlineError, segmented } from "./panels.js";
import { attempt, toast } from "./ui.js";

const DAYS = [
  { id: "every", label: "Every day", hint: "The brief is written each morning" },
  { id: "weekdays", label: "Weekdays", hint: "Monday to Friday" },
];
const LINES_PLACEHOLDER = "One thing a line: the roof repair, anything from the visa office";
const LAST_STATUS_CHARS = 60;

/** The words that say what a saved brief does: "on, 08:00 every day". */
function saidAbout(view) {
  if (!view.enabled) {
    return "Morning brief switched off.";
  }
  return `Morning brief on, ${view.time} ${view.days === "weekdays" ? "on weekdays" : "every day"}.`;
}

/**
 * The card. `onSaved()` runs after the daemon took a setting, for the page to read its list of schedules again. Returns
 * the element, `reload()`, which reads the brief again (a run starting or ending, the schedule switched in the list: the
 * fields a person is typing in are left as they are until they save), `focus()` and `dispose()`.
 */
export function mountBrief(ctx, onSaved) {
  /** The brief as the daemon last said it, or null before it did. */
  let view = null;
  let dirty = false;
  let busy = false;
  let disposed = false;
  // Grows with every read, so a slow answer never overwrites a newer one.
  let ticket = 0;

  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Morning brief on", disabled: true });
  const time = el("input", { class: "input", type: "time", "aria-label": "Time", required: true });
  const days = segmented(DAYS, "every", () => {
    dirty = true;
  });
  days.element.setAttribute("aria-label", "Days");
  const lines = el("textarea", { class: "textarea", rows: "3", placeholder: LINES_PLACEHOLDER, "aria-label": "Also tell me about", maxlength: "6200" });
  const zone = el("span", { class: "hint" });
  const customNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span"));
  const status = el("div", { class: "hint brief-status", role: "status" }, "Reading the brief…");
  const save = el("button", { class: "btn primary", type: "button", disabled: true }, icon("check"), "Save");
  const now = el("button", { class: "btn", type: "button", disabled: true, title: "Write a brief now: it reads, as at its time" }, icon("play"), "Write one now");
  const element = el(
    "section",
    { class: "card card-pad stack brief-card", dataset: { brief: "" } },
    el(
      "div",
      { class: "brief-head" },
      el("span", { class: "brief-mark", "aria-hidden": "true" }, icon("sun")),
      el(
        "div",
        { class: "grow" },
        el("h2", { class: "panel-title" }, "Morning brief"),
        el("div", { class: "hint" }, "Dot writes it at the time you set and sends the first line to your phone. It only reads: it never acts."),
      ),
      toggle,
    ),
    el(
      "div",
      { class: "form-grid brief-form" },
      el("div", { class: "field" }, el("span", { class: "field-label" }, "Time"), time, zone),
      el("div", { class: "field" }, el("span", { class: "field-label" }, "Days"), days.element),
      (() => {
        const wide = field("Also tell me about…", lines, "Your own lines for the brief, one thing a line. Dot adds what it knows of itself, your calendar and your mail.");
        wide.classList.add("wide");
        return wide;
      })(),
    ),
    customNote,
    status,
    el("div", { class: "row brief-actions" }, el("span", { class: "spacer" }), now, save),
  );

  function wanted(enabled) {
    return {
      enabled,
      time: time.value,
      days: days.value(),
      lines: lines.value.split("\n"),
    };
  }

  function paintStatus() {
    if (view === null) {
      return;
    }
    const parts = [];
    if (view.custom) {
      customNote.hidden = false;
      customNote.querySelector("span").textContent = `Its schedule fires at a time this card cannot show (${view.cron}). Saving here sets it to the time and days above.`;
    } else {
      customNote.hidden = true;
    }
    parts.push(view.enabled ? (view.next_local === null ? "No further run" : `Next ${view.next_local}`) : "Switched off");
    if (view.last_local !== null) {
      parts.push(`Last ${view.last_local}${view.last_status === "" ? "" : `: ${clip(view.last_status, LAST_STATUS_CHARS)}`}`);
    }
    if (view.last_run_id !== null) {
      parts.push(el("a", { href: `#/runs/${view.last_run_id}` }, "Open the last brief"));
    }
    if (view.accounts === 0) {
      parts.push(el("span", null, "No mail or calendar account is signed in: the brief covers what Dot knows of itself. ", el("a", { href: "#/customize/connectors" }, "Add one")));
    }
    status.replaceChildren(...joined(parts));
  }

  /** Show the brief the daemon said; the fields only when nobody is editing them. */
  function paint(fresh, force) {
    view = fresh;
    toggle.setAttribute("aria-checked", String(view.enabled));
    zone.textContent = `Read in ${view.zone}, the time zone of your schedules.`;
    if (force || !dirty) {
      time.value = view.time;
      days.set(view.days);
      lines.value = view.lines.join("\n");
      dirty = false;
    }
    toggle.disabled = busy;
    save.disabled = busy;
    now.disabled = busy || view.schedule_id === null;
    paintStatus();
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const fresh = await api.get("/brief");
      if (!disposed && mine === ticket) {
        paint(fresh, false);
      }
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (view === null) {
        status.replaceChildren(inlineError(error, () => attempt(retry)));
      } else {
        console.warn("the brief could not be read again", error);
      }
    }
  }

  async function retry() {
    status.replaceChildren("Reading the brief…");
    await reload();
  }

  async function put(enabled) {
    if (time.value === "") {
      time.focus();
      return;
    }
    busy = true;
    toggle.disabled = true;
    save.disabled = true;
    ticket += 1;
    try {
      const saved = await api.put("/brief", wanted(enabled));
      busy = false;
      paint(saved, true);
      toast("ok", saidAbout(saved));
      await onSaved();
    } finally {
      busy = false;
      if (view !== null) {
        toggle.disabled = false;
        save.disabled = false;
      }
    }
  }

  toggle.addEventListener("click", () => attempt(() => put(view === null ? true : toggle.getAttribute("aria-checked") !== "true")));
  save.addEventListener("click", () => attempt(() => put(view?.enabled === true)));
  time.addEventListener("input", () => {
    dirty = true;
  });
  lines.addEventListener("input", () => {
    dirty = true;
  });
  now.addEventListener("click", () =>
    attempt(async () => {
      if (view === null || view.schedule_id === null) {
        return;
      }
      const run = await api.post(`/schedules/${view.schedule_id}/run`, null);
      toast("ok", "Writing a brief now.");
      ctx.navigate(`/runs/${run.id}`);
    }),
  );

  attempt(reload);

  return {
    element,
    reload,
    /** Bring the card into view and put the cursor in its time, for the Edit of the brief's row in the list. */
    focus() {
      element.scrollIntoView({ block: "start" });
      time.focus();
    },
    dispose() {
      disposed = true;
    },
  };
}
