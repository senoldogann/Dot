// Dot's own initiative on Schedules (item 715): a switch, and the list of what Dot means to do for you on its own while
// the Mac is idle, each in a chat of its own (`GET` and `PUT /api/initiative`). What a run put on the list, what you put
// on it yourself (the box at the foot), what is being done and what was done or let go lately; a thing to do can be
// taken off, one that was let go put back.

import { api } from "./api.js";
import { clip, el } from "./dom.js";
import { icon } from "./icons.js";
import { inlineError } from "./panels.js";
import { attempt, toast } from "./ui.js";

const TITLE_CHARS = 200;
const WHY_CHARS = 140;
const NOTE_CHARS = 140;
const STATE = {
  doing: { label: "Doing now", mark: "play" },
  open: { label: "To do", mark: "list" },
  done: { label: "Done", mark: "check" },
  dropped: { label: "Let go", mark: "x" },
};
const WHO = { owner: "You put it on the list", run: "A run put it on the list" };

/** What a thing's row says under its title: why, and what came of it. */
function rowText(todo) {
  const parts = [];
  if (todo.why !== "") {
    parts.push(clip(todo.why, WHY_CHARS));
  }
  if (todo.note !== "") {
    parts.push(clip(todo.note, NOTE_CHARS));
  }
  return parts.join(" · ");
}

function todoRow(todo, handlers) {
  const state = STATE[todo.status] ?? STATE.open;
  const tries = todo.attempts > 0 && todo.status !== "done" ? `${todo.attempts} ${todo.attempts === 1 ? "try" : "tries"}` : null;
  const action =
    todo.status === "open"
      ? el("button", { class: "btn ghost sm", type: "button", title: "Take it off the list", "aria-label": `Take ${todo.title} off the list`, onclick: () => handlers.onDrop(todo) }, "Remove")
      : todo.status === "dropped"
        ? el("button", { class: "btn ghost sm", type: "button", title: "Put it back on the list", "aria-label": `Put ${todo.title} back on the list`, onclick: () => handlers.onAgain(todo) }, "Again")
        : null;
  const text = rowText(todo);
  return el(
    "div",
    { class: "list-row", dataset: { todo: todo.id, status: todo.status } },
    el("span", { class: "watcher-mark", "aria-hidden": "true" }, icon(state.mark)),
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row" },
        todo.run_id === null ? el("span", { class: "row-title" }, todo.title) : el("a", { class: "row-title", href: `#/runs/${todo.run_id}` }, todo.title),
        el("span", { class: "chip", title: WHO[todo.source] ?? "Dot put it on the list" }, state.label),
        tries === null ? null : el("span", { class: "chip" }, tries),
      ),
      text === "" ? null : el("div", { class: "row-text muted", title: `${todo.why}\n${todo.note}`.trim() }, text),
    ),
    action === null ? null : el("div", { class: "row-actions" }, action),
  );
}

/** An hour of the day in UTC as the Mac's clock shows it today, such as "14:00". */
function localHour(utcHour) {
  const moment = new Date();
  moment.setUTCHours(utcHour, 0, 0, 0);
  return moment.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * The card. Returns the element, `reload()`, which reads the list again (a run of it starting or ending), and
 * `dispose()`.
 */
export function mountInitiative() {
  let view = null;
  let busy = false;
  let disposed = false;
  let ticket = 0;

  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Work on its own while the Mac is idle", disabled: true });
  const count = el("span", { class: "hint" });
  // The mode its runs start in, as the daemon says it (item 756): in YOLO they ask nothing.
  const mode = el("span", null, "asking nothing (YOLO)");
  const lead = el(
    "p",
    { class: "hint watcher-lead" },
    "While the Mac is idle, Dot does on its own what is on this list, each in a chat of its own, ",
    mode,
    "; with nothing on it, it looks for work that helps you now and then. What Dot found in a mail or a page while at it is handled as untrusted: what would reach others asks you first, or in YOLO is refused unless your words call for it.",
  );
  // The weekday hours its runs wait in (item 826), told in the Mac's own time.
  const peak = el("p", { class: "hint initiative-peak", hidden: true });
  const idleNote = el("div", { class: "note warn initiative-idle", hidden: true, role: "status" }, icon("alert"), el("span", null, "macOS does not say how long the Mac has been idle, so Dot starts nothing on its own. The daemon's log has why."));
  const holder = el("div", { class: "initiative-list" }, el("div", { class: "hint" }, "Reading the list…"));
  const input = el("input", { class: "input", type: "text", maxlength: String(TITLE_CHARS), placeholder: "Something Dot can do for you on its own", "aria-label": "A thing for Dot's list" });
  const add = el("button", { class: "btn sm", type: "submit", disabled: true, "aria-label": "Add to the list", title: "Add to the list" }, icon("plus"), el("span", { class: "label" }, "Add"));
  const form = el("form", { class: "row initiative-add" }, input, add);
  const element = el(
    "section",
    { class: "memory-panel notes-panel watcher-panel initiative-panel", dataset: { initiative: "" } },
    el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "On its own"), count, el("span", { class: "spacer" }), toggle),
    lead,
    peak,
    idleNote,
    holder,
    form,
  );

  function paint(fresh) {
    view = fresh;
    toggle.setAttribute("aria-checked", String(view.enabled));
    idleNote.hidden = !view.enabled || view.idle_readable !== false;
    mode.textContent = view.approvals === "yolo" ? "asking nothing (YOLO)" : `in the ${view.approvals} approval mode`;
    const hours = view.peak_hours_utc ?? [];
    peak.hidden = hours.length !== 2;
    if (hours.length === 2) {
      peak.textContent = `On weekdays it starts nothing from ${localHour(hours[0])} to ${localHour(hours[1])}, the provider's dearer hours, but a thing you marked [URGENT].`;
    }
    toggle.title = view.enabled ? "Dot works on its own while the Mac is idle: switch it off" : "Switch on: Dot works on its own while the Mac is idle";
    toggle.disabled = busy;
    add.disabled = busy;
    const waiting = view.todos.filter((todo) => todo.status === "open" || todo.status === "doing").length;
    count.textContent = view.enabled ? (waiting === 0 ? "On · nothing to do" : `On · ${waiting} to do`) : "Off";
    holder.replaceChildren(
      view.todos.length === 0
        ? el("div", { class: "hint initiative-empty" }, "Nothing on the list yet.")
        : el(
            "div",
            { class: "watcher-list" },
            view.todos.map((todo) => todoRow(todo, handlers)),
          ),
    );
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const fresh = await api.get("/initiative");
      if (!disposed && mine === ticket) {
        paint(fresh);
      }
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (view === null) {
        holder.replaceChildren(inlineError(error, () => attempt(reload)));
      } else {
        console.warn("Dot's list could not be read again", error);
      }
    }
  }

  /** Send `act()`, whose answer is the list as it stands, and show it. */
  async function change(act, said) {
    busy = true;
    toggle.disabled = true;
    add.disabled = true;
    ticket += 1;
    try {
      const fresh = await act();
      busy = false;
      if (!disposed) {
        paint(fresh);
      }
      if (said !== null) {
        toast("ok", said);
      }
    } finally {
      busy = false;
      if (view !== null && !disposed) {
        toggle.disabled = false;
        add.disabled = false;
      }
    }
  }

  const handlers = {
    onDrop: (todo) => attempt(() => change(() => api.post(`/initiative/todos/${todo.id}/drop`, null), "Taken off the list.")),
    onAgain: (todo) => attempt(() => change(() => api.post(`/initiative/todos/${todo.id}/again`, null), "Back on the list.")),
  };

  toggle.addEventListener("click", () => {
    const enabled = toggle.getAttribute("aria-checked") !== "true";
    attempt(() => change(() => api.put("/initiative", { enabled }), enabled ? "Dot works on its own while the Mac is idle." : "Dot no longer works on its own."));
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const title = input.value.trim();
    if (title.length < 3) {
      input.focus();
      return;
    }
    attempt(async () => {
      await change(() => api.post("/initiative/todos", { title, why: "" }), "On Dot's list.");
      input.value = "";
    });
  });
  attempt(reload);

  return {
    element,
    reload,
    dispose() {
      disposed = true;
    },
  };
}
