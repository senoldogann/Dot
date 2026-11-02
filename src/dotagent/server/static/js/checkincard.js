// The Evening check-in of Schedules (item 930), the morning brief's twin: a switch and the time of day. On, Dot writes
// at that time, as a schedule of its own (`GET` and `PUT /api/checkin`), what got done today, what still waits for the
// owner and the next step it proposes for tomorrow, a run that only reads, whose first line goes to the phone and which
// opens in the chat "Evening check-in". The card is one line: its name, when it writes, its time and its switch.

import { api } from "./api.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { attempt, toast } from "./ui.js";

/** The card's line under its name: when the check-in is written, or that it is off. */
function summaryOf(view) {
  if (!view.enabled) {
    return "Off: at the end of the day, what got done, what waits for you and the next step";
  }
  // When it fires next is in the list of schedules above, where its schedule is.
  return `${view.days === "weekdays" ? "Weekdays" : "Every day"} at ${view.time}`;
}

/**
 * The card. `onSaved()` runs after the daemon took a setting, for the page to read its list of schedules again. Returns
 * the element, `reload()` and `dispose()`.
 */
export function mountCheckin(onSaved) {
  /** The check-in as the daemon last said it, or null before it did. */
  let view = null;
  let disposed = false;
  let busy = false;
  // Grows with every read, so a slow answer never overwrites a newer one.
  let ticket = 0;

  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Evening check-in on", disabled: true });
  const time = el("input", { class: "input checkin-time", type: "time", "aria-label": "Check-in at", required: true, disabled: true });
  const summary = el("div", { class: "hint brief-summary" }, "Reading the check-in…");
  const element = el(
    "section",
    { class: "card card-pad stack brief-card", dataset: { checkin: "" } },
    el(
      "div",
      { class: "brief-head" },
      el("span", { class: "brief-mark", "aria-hidden": "true" }, icon("moon")),
      el("div", { class: "grow" }, el("h2", { class: "panel-title" }, "Evening check-in"), summary),
      time,
      toggle,
    ),
  );

  function paint(fresh) {
    view = fresh;
    toggle.setAttribute("aria-checked", String(view.enabled));
    summary.textContent = summaryOf(view);
    if (document.activeElement !== time) {
      time.value = view.time;
    }
    toggle.disabled = busy;
    time.disabled = busy;
  }

  async function reload() {
    const mine = ++ticket;
    const fresh = await api.get("/checkin");
    if (!disposed && mine === ticket) {
      paint(fresh);
    }
  }

  /** Save the check-in as `enabled` at the time in the field; `said(saved)` gives the words of the toast. */
  async function put(enabled, said) {
    if (view === null || time.value === "") {
      return;
    }
    busy = true;
    toggle.disabled = true;
    time.disabled = true;
    ticket += 1;
    try {
      const saved = await api.put("/checkin", { enabled, time: time.value, days: view.days });
      busy = false;
      paint(saved);
      toast("ok", said(saved));
      await onSaved();
    } finally {
      busy = false;
      if (view !== null && !disposed) {
        toggle.disabled = false;
        time.disabled = false;
      }
    }
  }

  toggle.addEventListener("click", () =>
    attempt(() => put(toggle.getAttribute("aria-checked") !== "true", (saved) => (saved.enabled ? `Evening check-in on, ${saved.time}.` : "Evening check-in switched off."))),
  );
  time.addEventListener("change", () => attempt(() => put(view?.enabled === true, (saved) => `Evening check-in at ${saved.time}.`)));
  attempt(reload);

  return {
    element,
    reload,
    dispose() {
      disposed = true;
    },
  };
}
