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
import { t } from "./i18n.js";

const DAYS = [
  { id: "every", label: t("briefcard.everyDay"), hint: t("briefcard.theBriefIsWrittenEach") },
  { id: "weekdays", label: t("briefcard.weekdays"), hint: t("briefcard.mondayToFriday") },
];
const LINES_PLACEHOLDER = t("briefcard.oneThingALineThe");
const LAST_STATUS_CHARS = 60;

/** The card's line under its name (item 609): when the brief is written, or that it is off. A schedule set elsewhere at
 * times the card cannot show (`custom`) is said so, not given the card's time, which is not its own. */
function briefSummary(view) {
  if (!view.enabled) {
    return t("briefcard.off");
  }
  if (view.custom) {
    return t("briefcard.onAtTimesThisCard");
  }
  return `${view.days === "weekdays" ? t("briefcard.weekdays") : t("briefcard.everyDay")} at ${view.time}`;
}

/** The words that say what a saved brief does: "on, 08:00 every day". */
function saidAbout(view) {
  if (!view.enabled) {
    return t("briefcard.morningBriefSwitchedOff");
  }
  return t("briefcard.morningBriefOnAt", { time: view.time, days: view.days === "weekdays" ? t("briefcard.onWeekdays") : t("briefcard.everyDay2") });
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

  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": t("briefcard.morningBriefOn"), disabled: true });
  const time = el("input", { class: "input", type: "time", "aria-label": t("briefcard.time"), required: true });
  const days = segmented(DAYS, "every", () => {
    dirty = true;
  });
  days.element.setAttribute("aria-label", t("briefcard.days"));
  const lines = el("textarea", { class: "textarea", rows: "3", placeholder: LINES_PLACEHOLDER, "aria-label": t("briefcard.alsoTellMeAbout"), maxlength: "6200" });
  const zone = el("span", { class: "hint" });
  const customNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span"));
  const status = el("div", { class: "hint brief-status", role: "status" }, t("briefcard.readingTheBrief"));
  const summary = el("div", { class: "hint brief-summary" }, t("briefcard.readingTheBrief"));
  const settingsButton = el(
    "button",
    {
      class: "btn sm brief-opener",
      type: "button",
      "aria-haspopup": "dialog",
      "aria-label": t("briefcard.morningBriefSettings"),
      title: t("briefcard.theTimeTheDaysAnd"),
      disabled: true,
    },
    icon("sliders"),
    el("span", { class: "label" }, t("briefcard.settings")),
  );
  const now = el("button", { class: "btn", type: "button", disabled: true, title: t("briefcard.writeABriefNowIt") }, icon("play"), t("briefcard.writeOneNow"));
  // The fields of the dialog, made once: they hold the saved brief while the dialog is shut and what is typed while it is
  // open.
  const settings = el(
    "div",
    { class: "stack brief-settings" },
    el(
      "div",
      { class: "form-grid brief-form" },
      el("div", { class: "field" }, el("span", { class: "field-label" }, t("briefcard.time")), time, zone),
      el("div", { class: "field" }, el("span", { class: "field-label" }, t("briefcard.days")), days.element),
      (() => {
        const wide = field(t("briefcard.alsoTellMeAbout2"), lines, t("briefcard.yourOwnLinesForThe"));
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
      el("div", { class: "grow" }, el("h2", { class: "panel-title" }, t("briefcard.morningBrief")), summary),
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
      customNote.querySelector("span").textContent = t("briefcard.itsScheduleRunsAtA", { cron: view.cron });
    } else {
      customNote.hidden = true;
    }
    parts.push(view.enabled ? (view.next_local === null ? t("briefcard.willNotRunAgain") : t("briefcard.nextNextLocal", { next_local: view.next_local })) : t("briefcard.switchedOff"));
    if (view.last_local !== null) {
      parts.push(view.last_status === "" ? t("briefcard.lastAt", { at: view.last_local }) : t("briefcard.lastAtWith", { at: view.last_local, status: clip(view.last_status, LAST_STATUS_CHARS) }));
    }
    if (view.last_run_id !== null) {
      parts.push(el("a", { href: `#/runs/${view.last_run_id}` }, t("briefcard.openTheLastBrief")));
    }
    if (view.accounts === 0) {
      parts.push(el("span", null, t("briefcard.noMailOrCalendarAccount"), el("a", { href: "#/settings/connectors" }, t("briefcard.addOne"))));
    }
    status.replaceChildren(...joined(parts));
  }

  /** Show the brief the daemon said; the fields only when nobody is editing them. */
  function paint(fresh, force) {
    view = fresh;
    toggle.setAttribute("aria-checked", String(view.enabled));
    summary.textContent = briefSummary(view);
    zone.textContent = t("briefcard.readInZoneTheTime", { zone: view.zone });
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
    summary.replaceChildren(t("briefcard.readingTheBrief"));
    status.replaceChildren(t("briefcard.readingTheBrief"));
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
      toast("ok", t("briefcard.writingABriefNow"));
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
      title: t("briefcard.morningBrief"),
      description: t("briefcard.dotWritesItAtThe"),
      body: settings,
      submitLabel: t("briefcard.save"),
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
