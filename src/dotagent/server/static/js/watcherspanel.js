// The Watchers panel of Schedules (item 571): what Dot keeps an eye on for you and tells you of when it happens. A
// watcher is new mail in a Google or Microsoft account you signed in, an event about to start in its calendar, or your
// GitHub notifications, with your own words for what you want to hear of; it looks every few minutes (the daemon
// polls, nothing reaches it from outside), tells you on your phone and keeps a line in the chat Watchers, and, when
// you ask, starts a run with your goal for what it found. Each row says how its last look went and, when it failed,
// why. Everything is `GET`, `POST`, `PATCH` and `DELETE /api/watchers`, and `POST /api/watchers/<id>/try` (Check now).

import { ago, clip, el, joined, plural } from "./dom.js";
import { api } from "./api.js";
import { icon } from "./icons.js";
import { field, inlineError, loadingPanel, paintOpener, segmented } from "./panels.js";
import { onLifecycle } from "./state.js";
import { attempt, confirmDialog, toast } from "./ui.js";

const KINDS = [
  { id: "mail", label: "New mail", hint: "A new mail in a Google or Microsoft account you signed in", mark: "mail", what: "new mail" },
  { id: "calendar", label: "Calendar", hint: "An event about to start", mark: "calendar", what: "an event about to start" },
  { id: "github", label: "GitHub", hint: "Your GitHub notifications", mark: "branch", what: "a GitHub notification" },
];
const ACTIONS = [
  { id: "tell", label: "Tell me", hint: "A push to your phone, and a line kept in the chat Watchers" },
  { id: "run", label: "Tell me and start a run", hint: "As that, and a run of your goal for what came in" },
];
// The panel reads again this often, so that "Looked 2m ago" and a failure that mended stay true, and when Dot tells you.
const REFRESH_MS = 30_000;
const RELOAD_KINDS = new Set(["owner_told"]);
const WORDS_PREVIEW_CHARS = 200;
const ERROR_CHARS = 400;

function kindOf(id) {
  return KINDS.find((kind) => kind.id === id) ?? KINDS[0];
}

/** What a watcher's minutes mean: how often a mail or GitHub watcher looks, how long before an event a calendar one tells. */
function minutesText(view) {
  return view.kind === "calendar" ? `${plural(view.minutes, "minute", "minutes")} before an event` : `every ${plural(view.minutes, "minute", "minutes")}`;
}

/** When something will happen, as words: "now", "in 3m", "in 2h". */
function soon(ts) {
  const seconds = ts - Date.now() / 1000;
  if (seconds < 45) {
    return "now";
  }
  if (seconds < 3600) {
    return `in ${Math.round(seconds / 60)}m`;
  }
  return `in ${Math.round(seconds / 3600)}h`;
}

/** The line of a row that says how its looks go: the last, the next and what the last came to. */
function whenLine(view) {
  const parts = [];
  if (view.looked_at !== null) {
    parts.push(el("span", { title: "When it last looked" }, `Looked ${ago(view.looked_at)}`));
  } else if (view.error === null) {
    parts.push(el("span", null, "Has not looked yet"));
  }
  if (view.enabled && view.next_look_at !== null) {
    parts.push(el("span", { title: "When it looks next" }, `Next ${soon(view.next_look_at)}`));
  }
  if (view.last !== "") {
    // What the last look said of itself, its full stop left off: its parts are kept by dots.
    parts.push(el("span", null, view.last.replace(/\.$/, "")));
  }
  if (view.told > 0) {
    parts.push(el("span", { title: "Tells in the last hour" }, `${view.told} told this hour`));
  }
  return el("div", { class: "row-meta when-line" }, ...joined(parts));
}

function watcherRow(view, handlers) {
  const kind = kindOf(view.kind);
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(view.enabled),
    "aria-label": `${view.enabled ? "Switch off" : "Switch on"} ${view.name}`,
    title: view.enabled ? "Switch this watcher off" : "Switch this watcher on",
    onclick: () => handlers.onToggle(view),
  });
  const failure =
    view.error === null
      ? null
      : el("div", { class: "note bad watcher-error", role: "alert" }, icon("alert"), el("span", null, `${view.looked_at === null ? "It has not looked yet: " : "Its last look failed: "}${clip(view.error, ERROR_CHARS)}`));
  const row = el(
    "div",
    { class: "list-row", dataset: { watcher: view.id } },
    toggle,
    el("span", { class: "watcher-mark", "aria-hidden": "true" }, icon(kind.mark)),
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("span", { class: "row-title" }, view.name), el("span", { class: "chip", title: kind.hint }, kind.label)),
      el(
        "div",
        { class: "row-meta" },
        view.account === "" ? null : el("span", { class: "chip", title: "The account it looks in" }, view.account),
        el("span", { class: "chip", title: view.kind === "calendar" ? "How long before an event it tells you" : "How often it looks" }, minutesText(view)),
        el("span", { class: "chip", title: ACTIONS.find((action) => action.id === view.action)?.hint ?? "" }, view.action === "run" ? "Starts a run" : "Tells you"),
      ),
      view.words === "" ? null : el("div", { class: "row-text muted", title: view.words }, clip(view.words, WORDS_PREVIEW_CHARS)),
      whenLine(view),
      failure,
    ),
    el(
      "div",
      { class: "row-actions" },
      el(
        "button",
        {
          class: "btn ghost sm icon-only",
          type: "button",
          title: view.enabled ? "Check now: look at once instead of waiting" : "Switch it on to check it",
          "aria-label": `Check ${view.name} now`,
          disabled: !view.enabled,
          onclick: () => handlers.onCheck(view),
        },
        icon("refresh"),
      ),
      el("button", { class: "btn ghost sm icon-only", type: "button", title: "Edit this watcher", "aria-label": `Edit ${view.name}`, onclick: () => handlers.onEdit(view, row) }, icon("pencil")),
      el("button", { class: "btn ghost sm icon-only", type: "button", title: "Delete this watcher", "aria-label": `Delete ${view.name}`, onclick: () => handlers.onDelete(view) }, icon("trash")),
    ),
  );
  return row;
}

/** The body of a request that sets a watcher as `view` has it, with what `changes` says otherwise. */
function bodyOf(view, changes) {
  return { name: view.name, kind: view.kind, enabled: view.enabled, account: view.account, words: view.words, minutes: view.minutes, action: view.action, goal: view.goal, ...changes };
}

/**
 * The form that makes a watcher (`view` null) or changes one. `options` is what the daemon offers (`GET
 * /api/watchers/options`): the accounts signed in, whether GitHub's token is kept, the minutes each kind starts with.
 * `onSaved()` runs after the daemon took it, `onCancel()` on Cancel.
 */
function watcherForm({ view, options, onSaved, onCancel }) {
  const editing = view !== null;
  const name = el("input", { class: "input", type: "text", maxlength: "80", autocomplete: "off", placeholder: "Landlord", "aria-label": "Name" });
  const account = el("select", { class: "select", "aria-label": "Account" });
  const words = el("textarea", { class: "textarea", rows: "2", "aria-label": "What you want to hear of" });
  const minutes = el("input", { class: "input", type: "number", min: "1", max: "1440", step: "1", inputmode: "numeric", "aria-label": "Minutes" });
  const goal = el("textarea", { class: "textarea", rows: "3", placeholder: "Draft a reply in my voice and keep it as a draft.", "aria-label": "Goal of the run" });
  let touchedMinutes = editing;
  const kinds = segmented(KINDS, editing ? view.kind : "mail", () => paintKind());
  const actions = segmented(ACTIONS, editing ? view.action : "tell", () => paintAction());
  kinds.element.setAttribute("aria-label", "Kind");
  actions.element.setAttribute("aria-label", "What it does");
  const accountField = field("Account", account, "");
  // Their label and hint are the kind's, written by `paintKind`.
  const wordsField = field("What you want to hear of", words, "-");
  const minutesField = field("Minutes", minutes, "-");
  const goalField = field("Goal of the run", goal, "");
  const noAccounts = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "No Google or Microsoft account is signed in. "), el("a", { href: "#/customize/connectors" }, "Add one"));
  const noGithub = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "GitHub is not set up: add it on Customize › Connectors with a personal access token. GitHub's notifications need a classic token with the notifications scope; a fine-grained token cannot read them. "), el("a", { href: "#/customize/connectors" }, "Add it"));
  const runNote = el(
    "div",
    { class: "note", hidden: true },
    icon("shield"),
    el("span", null, "What came in was written by someone else, so the run starts marked untrusted: what reaches other people or lasts asks first, in the approval mode you chose as the default. At most a few runs an hour."),
  );
  const save = el("button", { class: "btn primary", type: "button" }, icon(editing ? "check" : "plus"), editing ? "Save changes" : "Add watcher");
  const cancel = el("button", { class: "btn ghost", type: "button", onclick: onCancel }, "Cancel");

  function paintKind() {
    const kind = kinds.value();
    accountField.hidden = kind === "github";
    noAccounts.hidden = kind === "github" || options.accounts.length > 0;
    noGithub.hidden = kind !== "github" || options.github;
    const labels = {
      mail: ["What you want to hear of", "In your own words: my bank, my landlord, anything about the visa. The fast model reads each new mail's sender, subject and first lines against it.", "my bank, my landlord, anything about the visa"],
      calendar: ["Only events with these words (optional)", "Leave it empty to be told of every timed event; the words are looked for in an event's title, place and notes.", "stand-up"],
      github: ["Only what you want to hear of (optional)", "Empty: a review asked of you, a mention, an assignment and a failing check of yours. With words, the fast model judges each new notification.", "anything about the visa repository"],
    }[kind];
    wordsField.querySelector(".field-label").textContent = labels[0];
    wordsField.querySelector(".hint").textContent = labels[1];
    words.placeholder = labels[2];
    minutesField.querySelector(".field-label").textContent = kind === "calendar" ? "Tell me this many minutes before an event" : "Look every this many minutes";
    minutesField.querySelector(".hint").textContent =
      kind === "calendar" ? "It looks at least every five minutes, and again when an event comes inside this." : "From 1 to 1440. GitHub asks to be looked at no oftener than it says, a minute at least.";
    if (!touchedMinutes) {
      minutes.value = String(options.minutes[kind]);
    }
  }

  function paintAction() {
    const running = actions.value() === "run";
    goalField.hidden = !running;
    runNote.hidden = !running;
  }

  function paintAccounts() {
    const known = options.accounts.map((choice) => choice.address);
    const keep = editing && view.account !== "" && !known.includes(view.account) ? [view.account] : [];
    account.replaceChildren(...[...known, ...keep].map((address) => el("option", { value: address }, keep.includes(address) ? `${address} (not signed in now)` : address)));
    account.value = editing && view.account !== "" ? view.account : (known[0] ?? "");
  }

  minutes.addEventListener("input", () => {
    touchedMinutes = true;
  });
  if (editing) {
    name.value = view.name;
    words.value = view.words;
    minutes.value = String(view.minutes);
    goal.value = view.goal;
  }
  paintAccounts();
  paintKind();
  paintAction();

  async function submit() {
    const kind = kinds.value();
    const nameText = name.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    if ((kind === "mail" || kind === "calendar") && account.value === "") {
      account.focus();
      toast("error", "Sign in a Google or Microsoft account first (Customize › Connectors).");
      return;
    }
    if (kind === "mail" && words.value.trim() === "") {
      words.focus();
      return;
    }
    if (actions.value() === "run" && goal.value.trim() === "") {
      goal.focus();
      return;
    }
    const wanted = Number(minutes.value);
    if (!Number.isInteger(wanted) || wanted < 1 || wanted > 1440) {
      minutes.focus();
      return;
    }
    const body = {
      name: nameText,
      kind,
      enabled: editing ? view.enabled : true,
      account: kind === "github" ? "" : account.value,
      words: words.value.trim(),
      minutes: wanted,
      action: actions.value(),
      goal: actions.value() === "run" ? goal.value.trim() : "",
    };
    save.disabled = true;
    try {
      if (editing) {
        await api.patch(`/watchers/${view.id}`, body);
        toast("ok", "Watcher saved.");
      } else {
        await api.post("/watchers", body);
        toast("ok", "Watcher added: its first look only notes what is there, and it tells what comes after.");
      }
      await onSaved();
    } finally {
      save.disabled = false;
    }
  }

  save.addEventListener("click", () => attempt(submit));
  const element = el(
    "section",
    { class: editing ? "stack grow" : "card card-pad stack", dataset: { watcherForm: editing ? view.id : "new" } },
    el(
      "div",
      { class: "form-grid" },
      field("Name", name, ""),
      el("div", { class: "field" }, el("span", { class: "field-label" }, "Kind"), kinds.element),
      accountField,
      minutesField,
      wordsField,
      el("div", { class: "field wide" }, el("span", { class: "field-label" }, "What it does"), actions.element),
      goalField,
    ),
    noAccounts,
    noGithub,
    runNote,
    el("div", { class: "row" }, el("span", { class: "spacer" }), cancel, save),
  );
  for (const wide of [wordsField, goalField]) {
    wide.classList.add("wide");
  }
  return { element };
}

/** The panel. Returns the element, `reload()` and `dispose()`. */
export function mountWatchers() {
  let disposed = false;
  let ticket = 0;
  let views = null;
  let options = null;
  // The id of the watcher whose form is open, or null: while one is, the list is not painted again over it.
  let editing = null;
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "hint" });
  const formSlot = el("div", { class: "watcher-form-slot", hidden: true });
  // The page's one primary button is New schedule: a watcher is made from a quieter one (item 609).
  const addButton = el("button", { class: "btn sm", type: "button" });
  paintOpener(addButton, "New watcher", false);
  const lead = el("p", { class: "hint watcher-lead" }, "Dot tells you when a mail, an event or a GitHub notification you care about comes: on your phone, and in the chat Watchers.");
  const element = el(
    "section",
    { class: "memory-panel notes-panel watcher-panel", dataset: { watchers: "" } },
    el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "Watchers"), count, el("span", { class: "spacer" }), addButton),
    lead,
    formSlot,
    holder,
  );

  function paint() {
    if (views === null || editing !== null) {
      return;
    }
    // With none, the head says so and the panel is its head and its line (item 609): no picture of an empty list.
    count.textContent = views.length === 0 ? "None yet" : plural(views.length, "watcher", "watchers");
    if (views.length === 0) {
      holder.replaceChildren();
      return;
    }
    const handlers = {
      onToggle: (view) => attempt(() => change(view, { enabled: !view.enabled })),
      onCheck: (view) => attempt(() => check(view)),
      onEdit: (view, row) => startEditing(view, row),
      onDelete: (view) => attempt(() => remove(view)),
    };
    holder.replaceChildren(el("div", { class: "watcher-list" }, views.map((view) => watcherRow(view, handlers))));
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const [fresh, offered] = await Promise.all([api.get("/watchers"), api.get("/watchers/options")]);
      if (disposed || mine !== ticket) {
        return;
      }
      views = fresh;
      options = offered;
      lead.textContent = offered.looking
        ? "Dot tells you when a mail, an event or a GitHub notification you care about comes: on your phone, and in the chat Watchers."
        : "The watchers are off in the configuration ([watchers] enabled): none of them looks.";
      paint();
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (views === null) {
        holder.replaceChildren(el("div", { class: "card" }, inlineError(error, () => attempt(retry))));
      } else {
        console.warn("the watchers could not be read again", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  async function change(view, changes) {
    await api.patch(`/watchers/${view.id}`, bodyOf(view, changes));
    await reload();
  }

  async function check(view) {
    const result = await api.post(`/watchers/${view.id}/try`, null);
    const { outcome } = result;
    if (outcome.error !== null) {
      toast("error", `${view.name} could not look: ${clip(outcome.error, ERROR_CHARS)}`);
    } else {
      toast("ok", `${view.name}: ${outcome.summary}`);
    }
    await reload();
  }

  async function remove(view) {
    const confirmed = await confirmDialog({
      title: "Delete this watcher?",
      message: `“${clip(view.name, 80)}” will not look again. What it told you stays in the chat Watchers.`,
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/watchers/${view.id}`);
    toast("ok", "Watcher deleted.");
    await reload();
  }

  // The row of the watcher turns into its form; saving or cancelling paints the list again.
  function startEditing(view, row) {
    if (options === null) {
      return;
    }
    editing = view.id;
    const form = watcherForm({
      view,
      options,
      onSaved: async () => {
        editing = null;
        await reload();
      },
      onCancel: () => {
        editing = null;
        paint();
      },
    });
    row.replaceChildren(form.element);
  }

  function newForm() {
    if (options === null) {
      return;
    }
    const form = watcherForm({
      view: null,
      options,
      onSaved: async () => {
        closeForm();
        await reload();
      },
      onCancel: closeForm,
    });
    formSlot.replaceChildren(form.element);
  }

  function closeForm() {
    formSlot.hidden = true;
    formSlot.replaceChildren();
    paintOpener(addButton, "New watcher", false);
  }

  addButton.addEventListener("click", () =>
    attempt(async () => {
      if (!formSlot.hidden) {
        closeForm();
        return;
      }
      // The form needs what the daemon offers; it is read again here, so that an account signed in meanwhile is there.
      options = await api.get("/watchers/options");
      newForm();
      formSlot.hidden = false;
      paintOpener(addButton, "New watcher", true);
      formSlot.scrollIntoView({ block: "nearest" });
      formSlot.querySelector("input")?.focus();
    }),
  );

  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      attempt(reload);
    }
  });
  const timer = setInterval(() => {
    if (!disposed && editing === null && formSlot.hidden) {
      attempt(reload);
    }
  }, REFRESH_MS);
  attempt(reload);

  return {
    element,
    reload,
    dispose() {
      disposed = true;
      clearInterval(timer);
      stopLifecycle();
    },
  };
}
