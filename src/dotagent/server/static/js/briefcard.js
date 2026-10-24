// The Morning brief of Schedules (item 570): a switch, the time of day, every day or the weekdays, and the owner's own
// lines ("Also tell me about…"). On, Dot writes the brief at that time as a schedule of its own (`GET` and `PUT
// /api/brief`), a run that only reads, whose first line goes to the phone and which opens in the chat "Morning brief".
// The schedule shows in the list above with its label and switches off there too, which the card follows. The card is a
// line of its own (item 609): its name, when it writes, its switch and a Settings button, which opens the time, the days
// and the owner's lines in a dialog (item 615) with Write one now among its actions.

import { api } from "./api.js";
import { clip, el, joined } from "./dom.js";
import { icon } from "./icons.js";
import { field, inlineError, segmented } from "./panels.js";
import { attempt, formDialog, toast } from "./ui.js";

const DAYS = [
  { id: "every", label: "Every day", hint: "The brief is written each morning" },
  { id: "weekdays", label: "Weekdays", hint: "Monday to Friday" },
];
const LINES_PLACEHOLDER = "One thing a line: the roof repair, anything from the visa office";
const LAST_STATUS_CHARS = 60;

/** The card's line under its name (item 609): when the brief is written, or that it is off. A schedule set elsewhere at
 * times the card cannot show (`custom`) is said so, not given the card's time, which is not its own. */
function briefSummary(view) {
  if (!view.enabled) {
    return "Off";
  }
  if (view.custom) {
    return "On, at times this card cannot show: open its settings to see";
  }
  return `${view.days === "weekdays" ? "Weekdays" : "Every day"} at ${view.time}`;
}

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
 * fields a person is typing in are left as they are until they save), `open()`, which opens its settings, and
 * `dispose()`.
 */
export function mountBrief(ctx, onSaved) {
  /** The brief as the daemon last said it, or null before it did. */
  let view = null;
  let dirty = false;
  let busy = false;
  let disposed = false;
  // Grows with every read, so a slow answer never overwrites a newer one.
  let ticket = 0;
  /** The dialog of the settings while it is open. */
  let dialog = null;

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
  const summary = el("div", { class: "hint brief-summary" }, "Reading the brief…");
  const settingsButton = el(
    "button",
    {
      class: "btn sm brief-opener",
      type: "button",
      "aria-haspopup": "dialog",
      "aria-label": "Morning brief settings",
      title: "The time, the days and your own lines",
      disabled: true,
    },
    icon("sliders"),
    el("span", { class: "label" }, "Settings"),
  );
  const now = el("button", { class: "btn", type: "button", disabled: true, title: "Write a brief now: it reads, as at its time" }, icon("play"), "Write one now");
  // The fields of the dialog, made once: they hold the saved brief while the dialog is shut and what is typed while it is
  // open.
  const settings = el(
    "div",
    { class: "stack brief-settings" },
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
  );
  const element = el(
    "section",
    { class: "card card-pad stack brief-card", dataset: { brief: "" } },
    el(
      "div",
      { class: "brief-head" },
      el("span", { class: "brief-mark", "aria-hidden": "true" }, icon("sun")),
      el("div", { class: "grow" }, el("h2", { class: "panel-title" }, "Morning brief"), summary),
      toggle,
      settingsButton,
    ),
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
    summary.textContent = briefSummary(view);
    zone.textContent = `Read in ${view.zone}, the time zone of your schedules.`;
    if (force || !dirty) {
      time.value = view.time;
      days.set(view.days);
      lines.value = view.lines.join("\n");
      dirty = false;
    }
    toggle.disabled = busy;
    settingsButton.disabled = false;
    now.disabled = view.schedule_id === null;
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
        // On the card's own line, where it is seen, with a way to try again; the settings button stays off until the
        // brief is read.
        summary.replaceChildren(inlineError(error, () => attempt(retry)));
        status.replaceChildren();
      } else {
        console.warn("the brief could not be read again", error);
      }
    }
  }

  async function retry() {
    summary.replaceChildren("Reading the brief…");
    status.replaceChildren("Reading the brief…");
    await reload();
  }

  /** Save the brief as the fields say, with the switch `enabled`; false when no time is set (the field takes the focus). */
  async function put(enabled) {
    if (time.value === "") {
      time.focus();
      return false;
    }
    busy = true;
    toggle.disabled = true;
    ticket += 1;
    try {
      const saved = await api.put("/brief", wanted(enabled));
      busy = false;
      paint(saved, true);
      toast("ok", saidAbout(saved));
      await onSaved();
      return true;
    } finally {
      busy = false;
      if (view !== null) {
        toggle.disabled = false;
      }
    }
  }

  toggle.addEventListener("click", () => attempt(() => put(view === null ? true : toggle.getAttribute("aria-checked") !== "true")));
  time.addEventListener("input", () => {
    dirty = true;
  });
  lines.addEventListener("input", () => {
    dirty = true;
  });
  now.addEventListener("click", async () => {
    if (view === null || view.schedule_id === null) {
      return;
    }
    try {
      // Changed settings are saved first, so that the brief written now is the one they say and leaving the page drops
      // nothing (the review of item 615); a time left empty keeps the dialog open on it.
      if (dirty && !(await put(view.enabled))) {
        return;
      }
      const run = await api.post(`/schedules/${view.schedule_id}/run`, null);
      toast("ok", "Writing a brief now.");
      ctx.navigate(`/runs/${run.id}`);
    } catch (error) {
      // The dialog is over the page, and a toast would stand under it: the reason is shown in the dialog.
      if (dialog === null) {
        toast("error", error.message);
      } else {
        dialog.fail(error);
      }
    }
  });

  /** Open the settings in a dialog: the fields as the daemon last said the brief is, and Write one now beside Save. What
   * is typed and not saved is dropped with the dialog. */
  function open() {
    if (view === null || dialog !== null) {
      return;
    }
    paint(view, true);
    const opened = formDialog({
      title: "Morning brief",
      description: "Dot writes it at the time you set and sends the first line to your phone. It only reads: it never acts.",
      body: settings,
      submitLabel: "Save",
      leading: [now],
      returnFocus: settingsButton,
      onSubmit: () => put(view?.enabled === true),
    });
    dialog = opened;
    opened.closed.then(() => {
      dialog = null;
      if (!disposed && view !== null) {
        paint(view, true);
      }
    });
  }

  settingsButton.addEventListener("click", open);
  attempt(reload);

  return {
    element,
    reload,
    open,
    dispose() {
      disposed = true;
    },
  };
}
