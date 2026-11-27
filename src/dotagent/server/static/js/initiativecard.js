// Dot's own initiative on Schedules (item 715): a switch, and the list of what Dot means to do for you on its own while
// the Mac is idle, each in a chat of its own (`GET` and `PUT /api/initiative`). What a run put on the list, what you put
// on it yourself (the box at the foot), what is being done and what was done or let go lately; a thing to do can be
// taken off, one that was let go put back. Its title folds the rest away, the switch and the count staying in sight, the
// choice kept per browser (`dot.fold.initiative`), open the first time.

import { api } from "./api.js";
import { clip, el, modelName } from "./dom.js";
import { icon } from "./icons.js";
import { foldState, inlineError } from "./panels.js";
import { attempt, toast } from "./ui.js";
import { t } from "./i18n.js";

const TITLE_CHARS = 200;
const WHY_CHARS = 140;
const NOTE_CHARS = 140;
const STATE = {
  doing: { label: t("initiativecard.doingNow"), mark: "play" },
  open: { label: t("initiativecard.toDo"), mark: "list" },
  done: { label: t("initiativecard.done"), mark: "check" },
  dropped: { label: t("initiativecard.letGo"), mark: "x" },
};
const WHO = { owner: t("initiativecard.youPutItOnThe"), run: t("initiativecard.aRunPutItOn") };
// Whether the card is folded to its head, kept as the folds of the pages are (panels.js `foldState`, `dot.fold.` and this).
const FOLD_ID = "initiative";

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
      ? el("button", { class: "btn ghost sm", type: "button", title: t("initiativecard.takeItOffTheList"), "aria-label": t("initiativecard.takeTitleOffTheList", { title: todo.title }), onclick: () => handlers.onDrop(todo) }, t("initiativecard.remove"))
      : todo.status === "dropped"
        ? el("button", { class: "btn ghost sm", type: "button", title: t("initiativecard.putItBackOnThe"), "aria-label": t("initiativecard.putTitleBackOnThe", { title: todo.title }), onclick: () => handlers.onAgain(todo) }, t("initiativecard.again"))
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
        el("span", { class: "chip", title: WHO[todo.source] ?? t("initiativecard.dotPutItOnThe") }, state.label),
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

  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": t("initiativecard.workOnItsOwnWhile"), disabled: true });
  const count = el("span", { class: "hint" });
  // The mode its runs start in, as the daemon says it (item 756).
  const mode = el("span", null, t("initiativecard.askingOnlyBeforeAMessage"));
  const lead = el(
    "p",
    { class: "hint watcher-lead" },
    t("initiativecard.whileTheMacIsIdle"),
    mode,
    t("initiativecard.withNothingOnItIt"),
  );
  // The weekday hours its runs wait in (item 826), told in the Mac's own time.
  const peak = el("p", { class: "hint initiative-peak", hidden: true });
  // The model that decides what to do and what a costly try comes to (item 916).
  const lookName = el("a", { href: "#/settings/models", title: t("initiativecard.chooseItInSettingsModels") }, t("initiativecard.theDefaultModel"));
  const costly = el("span");
  const judge = el("p", { class: "hint initiative-judge" }, t("initiativecard.itDecidesWhatIsWorth"), lookName, ". ", costly);
  const idleNote = el("div", { class: "note warn initiative-idle", hidden: true, role: "status" }, icon("alert"), el("span", null, t("initiativecard.macosDoesNotSayHow")));
  const holder = el("div", { class: "initiative-list" }, el("div", { class: "hint" }, t("initiativecard.readingTheList")));
  const input = el("input", { class: "input", type: "text", maxlength: String(TITLE_CHARS), placeholder: t("initiativecard.somethingDotCanDoFor"), "aria-label": t("initiativecard.aThingForDotS") });
  const add = el("button", { class: "btn sm", type: "submit", disabled: true, "aria-label": t("initiativecard.addToTheList"), title: t("initiativecard.addToTheList") }, icon("plus"), el("span", { class: "label" }, t("initiativecard.add")));
  const form = el("form", { class: "row initiative-add" }, input, add);
  const body = el("div", { class: "initiative-body", id: "initiative-body" }, lead, judge, peak, idleNote, holder, form);
  const folding = foldState(FOLD_ID, true);
  // The title is the fold's button; the count and the switch sit beside it, so using the switch never folds the card.
  const fold = el(
    "button",
    { class: "fold-toggle initiative-fold", type: "button", "aria-controls": body.id, onclick: () => flipFold() },
    el("span", { class: "chev" }, icon("chevron")),
    el("span", { class: "fold-title" }, t("initiativecard.onItsOwn")),
  );
  const element = el(
    "section",
    { class: "memory-panel notes-panel watcher-panel initiative-panel", dataset: { initiative: "" } },
    el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, fold), count, el("span", { class: "spacer" }), toggle),
    body,
  );

  /** Fold the card to its head or open it, and keep the choice. */
  function flipFold() {
    folding.flip();
    paintFold();
  }

  function paintFold() {
    const open = folding.isOpen();
    fold.setAttribute("aria-expanded", String(open));
    body.hidden = !open;
    element.dataset.open = String(open);
  }
  paintFold();

  function paint(fresh) {
    view = fresh;
    toggle.setAttribute("aria-checked", String(view.enabled));
    idleNote.hidden = !view.enabled || view.idle_readable !== false;
    mode.textContent = view.approvals === "yolo" ? t("initiativecard.askingOnlyBeforeAMessage") : t("initiativecard.inTheApprovalsApprovalMode", { approvals: view.approvals });
    const hours = view.peak_hours_utc ?? [];
    peak.hidden = hours.length !== 2;
    if (hours.length === 2) {
      peak.textContent = t("initiativecard.onWeekdaysByUtcIt", { localHour: localHour(hours[0]), localHour2: localHour(hours[1]) });
    }
    lookName.textContent = view.look_model === "" ? t("initiativecard.theDefaultModel") : modelName(view.look_model);
    costly.textContent =
      view.costly_tokens > 0
        ? t("initiativecard.workThatGetsDoneIs", { toLocaleString: (view.costly_tokens / 1_000_000).toLocaleString() })
        : "";
    toggle.title = view.enabled ? t("initiativecard.dotWorksOnItsOwn") : t("initiativecard.switchOnDotWorksOn");
    toggle.disabled = busy;
    add.disabled = busy;
    const waiting = view.todos.filter((todo) => todo.status === "open" || todo.status === "doing").length;
    count.textContent = view.enabled ? (waiting === 0 ? t("initiativecard.onNothingToDo") : t("initiativecard.onWaitingToDo", { waiting })) : t("initiativecard.off");
    holder.replaceChildren(
      view.todos.length === 0
        ? el("div", { class: "hint initiative-empty" }, t("initiativecard.nothingOnTheListYet"))
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
    onDrop: (todo) => attempt(() => change(() => api.post(`/initiative/todos/${todo.id}/drop`, null), t("initiativecard.takenOffTheList"))),
    onAgain: (todo) => attempt(() => change(() => api.post(`/initiative/todos/${todo.id}/again`, null), t("initiativecard.backOnTheList"))),
  };

  toggle.addEventListener("click", () => {
    const enabled = toggle.getAttribute("aria-checked") !== "true";
    attempt(() => change(() => api.put("/initiative", { enabled }), enabled ? t("initiativecard.dotWorksOnItsOwn2") : t("initiativecard.dotNoLongerWorksOn")));
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const title = input.value.trim();
    if (title.length < 3) {
      input.focus();
      return;
    }
    attempt(async () => {
      await change(() => api.post("/initiative/todos", { title, why: "" }), t("initiativecard.onDotSList"));
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
