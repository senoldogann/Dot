// The Watchers panel of Schedules (item 571): what Dot keeps an eye on for you and tells you of when it happens. A
// watcher is new mail in a Google or Microsoft account you signed in, an event about to start in its calendar, or your
// GitHub notifications, or your own check script (item 1855), with your own words for what you want to hear of; it looks every few minutes (the daemon
// polls, nothing reaches it from outside), tells you on your phone and keeps a line in the chat Watchers, and, when
// you ask, starts a run with your goal for what it found. Each row says how its last look went and, when it failed,
// why. Everything is `GET`, `POST`, `PATCH` and `DELETE /api/watchers`, and `POST /api/watchers/<id>/try` (Check now).
// New watcher and a watcher's Edit open the form in a dialog (item 615).

import { ago, clip, el, joined, } from "./dom.js";
import { api } from "./api.js";
import { icon } from "./icons.js";
import { field, inlineError, loadingPanel, markOpener, segmented } from "./panels.js";
import { onLifecycle } from "./state.js";
import { attempt, confirmDialog, formDialog, settingFold, toast } from "./ui.js";
import { countsLine, rowsOf, sumCounts } from "./tellwords.js";
import { t, tCount } from "./i18n.js";

const KINDS = [
  { id: "mail", label: t("watcherspanel.newMail"), hint: t("watcherspanel.aNewMailInA"), mark: "mail", what: t("watcherspanel.newMail2") },
  { id: "calendar", label: t("watcherspanel.calendar"), hint: t("watcherspanel.anEventAboutToStart"), mark: "calendar", what: t("watcherspanel.anEventAboutToStart2") },
  { id: "github", label: "GitHub", hint: t("watcherspanel.yourGithubNotifications"), mark: "branch", what: t("watcherspanel.aGithubNotification") },
  // Item 1855: the owner's own script in Dot's checks folder, run without a model, is the look.
  { id: "check", label: t("watcherspanel.check"), hint: t("watcherspanel.yourOwnScriptRunWithout"), mark: "terminal", what: t("watcherspanel.whatYourCheckFound") },
];
const ACTIONS = [
  { id: "tell", label: t("watcherspanel.tellMe"), hint: t("watcherspanel.aPushToYourPhone") },
  { id: "run", label: t("watcherspanel.tellMeAndStartA"), hint: t("watcherspanel.asThatAndATask") },
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
  return view.kind === "calendar" ? t("watcherspanel.partBeforeAnEvent", { part: tCount(view.minutes, "watcherspanel.countMinute", "watcherspanel.countMinutes") }) : t("watcherspanel.everyPart", { part: tCount(view.minutes, "watcherspanel.countMinute", "watcherspanel.countMinutes") });
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
    parts.push(el("span", { title: t("watcherspanel.whenItLastLooked") }, t("watcherspanel.lookedAgo", { ago: ago(view.looked_at) })));
  } else if (view.error === null) {
    parts.push(el("span", null, t("watcherspanel.hasNotLookedYet")));
  }
  if (view.enabled && view.next_look_at !== null) {
    parts.push(el("span", { title: t("watcherspanel.whenItLooksNext") }, t("watcherspanel.nextSoon", { soon: soon(view.next_look_at) })));
  }
  if (view.last !== "") {
    // What the last look said of itself, its full stop left off: its parts are kept by dots.
    parts.push(el("span", null, view.last.replace(/\.$/, "")));
  }
  if (view.told > 0) {
    parts.push(el("span", { title: t("watcherspanel.tellsInTheLastHour") }, t("watcherspanel.toldToldThisHour", { told: view.told })));
  }
  return el("div", { class: "row-meta when-line" }, ...joined(parts));
}

/** What you did with this watcher's tells in the last 30 days (item 1743), or null before the first one goes out. */
function reactionLine(view, tells) {
  const counts = sumCounts(rowsOf(tells, "watcher", view.id));
  if (counts.sent === 0) {
    return null;
  }
  return el("div", { class: "row-meta tell-line", title: t("watcherspanel.whatYouDidWithIts") }, countsLine(counts));
}

/** A row of the list. `looking` says that its Check now is at work: the button shows it and takes no second press. */
function watcherRow(view, handlers, looking, tells) {
  const kind = kindOf(view.kind);
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(view.enabled),
    "aria-label": `${view.enabled ? t("watcherspanel.switchOff") : t("watcherspanel.switchOn")} ${view.name}`,
    title: view.enabled ? t("watcherspanel.switchThisWatcherOff") : t("watcherspanel.switchThisWatcherOn"),
    onclick: () => handlers.onToggle(view),
  });
  const failure =
    view.error === null
      ? null
      : el("div", { class: "note bad watcher-error", role: "alert" }, icon("alert"), el("span", null, `${view.looked_at === null ? t("watcherspanel.itHasNotLookedYet") : t("watcherspanel.itsLastLookFailed")}${clip(view.error, ERROR_CHARS)}`));
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
        // An address longer than the row is cut with an ellipsis, so that it never runs past the row; the title has it whole.
        view.account === "" ? null : el("span", { class: "chip", title: t("watcherspanel.theAccountItLooksIn", { account: view.account }) }, el("span", { class: "chip-text" }, view.account)),
        view.check === "" ? null : el("span", { class: "chip mono", title: t("watcherspanel.theCheckItRunsIn") }, el("span", { class: "chip-text" }, view.check)),
        el("span", { class: "chip", title: view.kind === "calendar" ? t("watcherspanel.howLongBeforeAnEvent") : t("watcherspanel.howOftenItLooks") }, minutesText(view)),
        el("span", { class: "chip", title: ACTIONS.find((action) => action.id === view.action)?.hint ?? "" }, view.action === "run" ? t("watcherspanel.startsATask") : t("watcherspanel.tellsYou")),
      ),
      view.words === "" ? null : el("div", { class: "row-text muted", title: view.words }, clip(view.words, WORDS_PREVIEW_CHARS)),
      whenLine(view),
      reactionLine(view, tells),
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
          title: looking ? t("watcherspanel.lookingNowALookCan") : view.enabled ? t("watcherspanel.checkNowLookAtOnce") : t("watcherspanel.switchItOnToCheck"),
          "aria-label": t("watcherspanel.checkNameNow", { name: view.name }),
          "aria-busy": looking ? "true" : null,
          disabled: !view.enabled || looking,
          onclick: () => handlers.onCheck(view),
        },
        looking ? el("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh"),
      ),
      el(
        "button",
        { class: "btn ghost sm icon-only", type: "button", title: t("watcherspanel.editThisWatcher"), "aria-label": t("watcherspanel.editName", { name: view.name }), "aria-haspopup": "dialog", onclick: () => handlers.onEdit(view) },
        icon("pencil"),
      ),
      el("button", { class: "btn ghost sm icon-only", type: "button", title: t("watcherspanel.deleteThisWatcher"), "aria-label": t("watcherspanel.deleteName", { name: view.name }), onclick: () => handlers.onDelete(view) }, icon("trash")),
    ),
  );
  return row;
}

/** The body of a request that sets a watcher as `view` has it, with what `changes` says otherwise. */
function bodyOf(view, changes) {
  return { name: view.name, kind: view.kind, enabled: view.enabled, account: view.account, words: view.words, minutes: view.minutes, action: view.action, goal: view.goal, check: view.check, ...changes };
}

/**
 * The fields that make a watcher (`view` null) or change one, for the dialog that holds them (item 615). `options` is
 * what the daemon offers (`GET /api/watchers/options`): the accounts signed in, whether GitHub's token is kept, the
 * minutes each kind starts with. Returns the element and `submit()`, which asks the daemon and resolves to `false` when
 * a field the watcher needs is empty (the field takes the focus) and throws when the daemon refuses or no account is
 * signed in for a kind that reads one.
 */
function watcherForm({ view, options }) {
  const editing = view !== null;
  const name = el("input", { class: "input", type: "text", maxlength: "80", autocomplete: "off", placeholder: "Landlord", "aria-label": t("watcherspanel.name") });
  const account = el("select", { class: "select", "aria-label": t("watcherspanel.account") });
  const check = el("input", { class: "input mono", type: "text", maxlength: "80", autocomplete: "off", spellcheck: "false", placeholder: "price-drop.sh", "aria-label": t("watcherspanel.check") });
  const words = el("textarea", { class: "textarea", rows: "2", "aria-label": t("watcherspanel.whatYouWantToHear") });
  const minutes = el("input", { class: "input", type: "number", min: "1", max: "1440", step: "1", inputmode: "numeric", "aria-label": t("watcherspanel.minutes") });
  const goal = el("textarea", { class: "textarea", rows: "3", placeholder: t("watcherspanel.draftAReplyInMy"), "aria-label": t("watcherspanel.goalOfTheTask") });
  let touchedMinutes = editing;
  const kinds = segmented(KINDS, editing ? view.kind : "mail", () => paintKind());
  const actions = segmented(ACTIONS, editing ? view.action : "tell", () => paintAction());
  kinds.element.setAttribute("aria-label", t("watcherspanel.kind"));
  actions.element.setAttribute("aria-label", t("watcherspanel.whatItDoes"));
  const accountField = field(t("watcherspanel.account"), account, "");
  const checkField = field(
    t("watcherspanel.check"),
    check,
    t("watcherspanel.theFileNameOfAn"),
  );
  // Their label and hint are the kind's, written by `paintKind`.
  const wordsField = field(t("watcherspanel.whatYouWantToHear"), words, "-");
  const minutesField = field(t("watcherspanel.minutes"), minutes, "-");
  const goalField = field(t("watcherspanel.goalOfTheTask"), goal, "");
  const noAccounts = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, t("watcherspanel.noGoogleOrMicrosoftAccount")), el("a", { href: "#/settings/connectors" }, t("watcherspanel.addOne")));
  const noGithub = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, t("watcherspanel.githubIsNotConnectedAdd")), el("a", { href: "#/settings/connectors" }, t("watcherspanel.addIt")));
  const runNote = el(
    "div",
    { class: "note", hidden: true },
    icon("shield"),
    el("span", null, t("watcherspanel.whatCameInWasWritten")),
  );
  function paintKind() {
    const kind = kinds.value();
    accountField.hidden = kind === "github" || kind === "check";
    checkField.hidden = kind !== "check";
    noAccounts.hidden = kind === "github" || kind === "check" || options.accounts.length > 0;
    noGithub.hidden = kind !== "github" || options.github;
    const labels = {
      mail: [t("watcherspanel.whatYouWantToHear"), t("watcherspanel.inYourOwnWordsMy"), t("watcherspanel.myBankMyLandlordAnything")],
      calendar: [t("watcherspanel.onlyEventsWithTheseWords"), t("watcherspanel.leaveItEmptyToBe"), "stand-up"],
      github: [t("watcherspanel.onlyWhatYouWantTo"), t("watcherspanel.emptyAReviewAskedOf"), t("watcherspanel.anythingAboutTheVisaRepository")],
      check: [t("watcherspanel.onlyWhatYouWantTo"), t("watcherspanel.emptyEveryTimeTheCheck"), t("watcherspanel.onlyADropBelow90")],
    }[kind];
    wordsField.querySelector(".field-label").textContent = labels[0];
    wordsField.querySelector(".hint").textContent = labels[1];
    words.placeholder = labels[2];
    minutesField.querySelector(".field-label").textContent = kind === "calendar" ? t("watcherspanel.tellMeThisManyMinutes") : t("watcherspanel.lookEveryThisManyMinutes");
    minutesField.querySelector(".hint").textContent =
      kind === "calendar"
        ? t("watcherspanel.itLooksAtLeastEvery")
        : kind === "github"
          ? t("watcherspanel.from1To1440Github")
          : t("watcherspanel.from1To1440");
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
    account.replaceChildren(...[...known, ...keep].map((address) => el("option", { value: address }, keep.includes(address) ? t("watcherspanel.addressNotSignedInNow", { address }) : address)));
    account.value = editing && view.account !== "" ? view.account : (known[0] ?? "");
  }

  minutes.addEventListener("input", () => {
    touchedMinutes = true;
  });
  if (editing) {
    name.value = view.name;
    words.value = view.words;
    check.value = view.check;
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
      return false;
    }
    if ((kind === "mail" || kind === "calendar") && account.value === "") {
      account.focus();
      // The dialog is over the page, and a toast would stand under it: the reason is shown in the dialog.
      throw new Error("Sign in a Google or Microsoft account first (Settings › Connectors).");
    }
    if (kind === "check" && check.value.trim() === "") {
      check.focus();
      return false;
    }
    if (kind === "mail" && words.value.trim() === "") {
      words.focus();
      return false;
    }
    if (actions.value() === "run" && goal.value.trim() === "") {
      goal.focus();
      return false;
    }
    const wanted = Number(minutes.value);
    if (!Number.isInteger(wanted) || wanted < 1 || wanted > 1440) {
      minutes.focus();
      return false;
    }
    const body = {
      name: nameText,
      kind,
      enabled: editing ? view.enabled : true,
      account: kind === "github" || kind === "check" ? "" : account.value,
      check: kind === "check" ? check.value.trim() : "",
      words: words.value.trim(),
      minutes: wanted,
      action: actions.value(),
      goal: actions.value() === "run" ? goal.value.trim() : "",
    };
    if (editing) {
      await api.patch(`/watchers/${view.id}`, body);
      toast("ok", t("watcherspanel.watcherSaved"));
    } else {
      await api.post("/watchers", body);
      toast("ok", kind === "check" ? t("watcherspanel.watcherAddedItRunsYour") : t("watcherspanel.watcherAddedItsFirstLook"));
    }
    return true;
  }

  const element = el(
    "div",
    { class: "stack", dataset: { watcherForm: editing ? view.id : "new" } },
    el(
      "div",
      { class: "form-grid" },
      field(t("watcherspanel.name"), name, ""),
      el("div", { class: "field" }, el("span", { class: "field-label" }, t("watcherspanel.kind")), kinds.element),
      accountField,
      checkField,
      minutesField,
      wordsField,
      el("div", { class: "field wide" }, el("span", { class: "field-label" }, t("watcherspanel.whatItDoes")), actions.element),
      goalField,
    ),
    noAccounts,
    noGithub,
    runNote,
  );
  for (const wide of [wordsField, goalField]) {
    wide.classList.add("wide");
  }
  return { element, submit };
}

/** The panel. Returns the element, `reload()` and `dispose()`. */
export function mountWatchers() {
  let disposed = false;
  let ticket = 0;
  let views = null;
  let options = null;
  // What you did with the tells of the last 30 days, `rows` of `GET /api/tells`; none until it is read (item 1743).
  let tells = [];
  // The ids of the watchers whose Check now is at work. A look takes up to three minutes, and a second press would queue
  // another whole look behind it, so the row shows the first one and its button takes no more (item 612).
  const looking = new Set();
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "hint" });
  // The page's one primary button is New schedule: a watcher is made from a quieter one (item 609), and its form opens in
  // a dialog (item 615).
  const addButton = el("button", { class: "btn sm", type: "button" });
  markOpener(addButton, t("watcherspanel.newWatcher"));
  const lead = el("p", { class: "hint watcher-lead" }, t("watcherspanel.dotTellsYouWhenA"));
  const element = el(
    "section",
    { class: "memory-panel notes-panel watcher-panel", dataset: { watchers: "" } },
    el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, t("watcherspanel.watchers")), count, el("span", { class: "spacer" }), addButton),
    lead,
    holder,
  );

  function paint() {
    if (views === null) {
      return;
    }
    // With none, the head says so and the panel is its head and its line (item 609): no picture of an empty list.
    count.textContent = views.length === 0 ? t("watcherspanel.noneYet") : tCount(views.length, "watcherspanel.countWatcher", "watcherspanel.countWatchers");
    if (views.length === 0) {
      holder.replaceChildren();
      return;
    }
    const handlers = {
      onToggle: (view) => attempt(() => change(view, { enabled: !view.enabled })),
      onCheck: (view) => attempt(() => check(view)),
      onEdit: (view) => openForm(view),
      onDelete: (view) => attempt(() => remove(view)),
    };
    holder.replaceChildren(el("div", { class: "watcher-list" }, views.map((view) => watcherRow(view, handlers, looking.has(view.id), tells))));
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const [fresh, offered, told] = await Promise.all([api.get("/watchers"), api.get("/watchers/options"), api.get("/tells")]);
      if (disposed || mine !== ticket) {
        return;
      }
      views = fresh;
      options = offered;
      tells = told.rows;
      lead.replaceChildren(
        ...(offered.looking
          ? [t("watcherspanel.dotTellsYouWhenA")]
          : [t("watcherspanel.watchersAreSwitchedOffIn"), settingFold("[watchers] enabled")]),
      );
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
    if (looking.has(view.id)) {
      return;
    }
    looking.add(view.id);
    paint();
    try {
      const { outcome } = await api.post(`/watchers/${view.id}/try`, null);
      if (outcome.error !== null) {
        toast("error", t("watcherspanel.nameCouldNotLookClip", { name: view.name, clip: clip(outcome.error, ERROR_CHARS) }));
      } else {
        toast("ok", `${view.name}: ${outcome.summary}`);
      }
    } finally {
      looking.delete(view.id);
      if (!disposed) {
        paint();
      }
    }
    await reload();
  }

  async function remove(view) {
    const confirmed = await confirmDialog({
      title: t("watcherspanel.deleteThisWatcher2"),
      message: t("watcherspanel.clipWillNotLookAgain", { clip: clip(view.name, 80) }),
      confirmLabel: t("watcherspanel.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/watchers/${view.id}`);
    toast("ok", t("watcherspanel.watcherDeleted"));
    await reload();
  }

  /** The form that makes a watcher (`view` null) or changes `view`, in a dialog (item 615); the list is read again once the
   * daemon took it. The dialog holds the form, so the list behind it can be read again without touching what is typed. */
  function openForm(view) {
    if (options === null) {
      return;
    }
    const form = watcherForm({ view, options });
    formDialog({
      title: view === null ? t("watcherspanel.newWatcher") : t("watcherspanel.editWatcher"),
      description:
        view === null
          ? t("watcherspanel.dotLooksEveryFewMinutes")
          : t("watcherspanel.changeClipItLooksBy", { clip: clip(view.name, 60) }),
      body: form.element,
      submitLabel: view === null ? t("watcherspanel.addWatcher") : t("watcherspanel.saveChanges"),
      size: "wide",
      // The rows are painted again once a watcher is saved: its Edit button is found anew.
      returnFocus: () => (view === null ? addButton : holder.querySelector(`[data-watcher="${view.id}"] [aria-label^="Edit "]`)),
      onSubmit: async () => {
        if ((await form.submit()) === false) {
          return false;
        }
        await reload();
        return true;
      },
    });
  }

  addButton.addEventListener("click", () =>
    attempt(async () => {
      // The form needs what the daemon offers; it is read again here, so that an account signed in meanwhile is there.
      options = await api.get("/watchers/options");
      openForm(null);
    }),
  );

  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      attempt(reload);
    }
  });
  const timer = setInterval(() => {
    if (!disposed) {
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
