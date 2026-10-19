// The sidebar: a new chat, a search over the chats, the ways to the agents, the activity, the campaigns, the schedules and Customize,
// what waits and what works (folded or not, as the owner left them), the chats themselves (several of them can be
// deleted at once) and, in the foot, the way to the settings; a run stops from its composer (item 289). A row whose
// run works says Working and for how long (item 122). A chat's menu (item 130) pins it,
// names it, copies its link, starts choosing chats with it and deletes it; the pinned chats come first, under Pinned.
// A chat whose latest run ended after it was last seen is unread (item 131): an accent dot, until it is opened, or Mark
// read. Each chat stands once (item 202): a chat at work stands under Working, with the
// runs it delegated folded under it, and goes back among the days once it rests; a pinned one stays under Pinned, its
// delegated runs under it.

import { ApiError, api, request } from "./api.js";
import { age, clip, clipWords, copyText, dateTime, debounce, el, foldSearch, keepChildren, plural, setText, workingFor } from "./dom.js";
import { icon } from "./icons.js";
import { actorFace, lookOf } from "./actors.js";
import { dotName, faceState } from "./face.js";
import { chatLook } from "./hues.js";
import { PHONE } from "./layout.js";
import { closeMenu, menuOpenedBy, openMenu } from "./menu.js";
import { nestRows, progressFacts } from "./nesting.js";
import { readFolds, writePref } from "./prefs.js";
import { requestCompose } from "./shortcuts.js";
import { onLifecycle, onStreamBack, readFailed } from "./state.js";
import { attempt, confirmDialog, toast } from "./ui.js";

// The chats listed at first, the step by which "Show older chats" lists more, and the most the API lists at once.
const LIST_LIMIT = 200;
const LIST_CAP = 500;
// The moments a run changes status; a run that waits for an approval is "blocked" until it is answered.
const RELOAD_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided", "takeover", "handback", "chat_rewound"]);
// The sections above the chats: what waits for you and what works now (`GET /activity`), each hidden while empty.
const ACTIVITY_SECTIONS = [
  { key: "waiting", label: "Waiting for you" },
  { key: "working", label: "Working" },
];
const DAY_LABELS = ["Today", "Yesterday", "Previous 7 days", "Older"];
// A row of the activity sections shows this many words of its title at most, on one line (item 95).
const ROW_WORDS = 7;
const ROW_CHARS = 56;
// Which sections the owner folded (`waiting`, `working`, `chats`), and the runs whose delegated runs they folded
// (`runs`, item 198), kept between visits.
const FOLDS_KEY = "dot.sideFolds";
// What a status dot means, for screen readers; a chat whose latest run is done or cancelled shows no dot and needs no word.
const STATUS_WORD = { queued: "queued", running: "running", blocked: "waiting for you", failed: "failed" };
// The states of a run that is over: only such a chat can be marked unread (item 131).
const ENDED = new Set(["done", "failed", "cancelled"]);
const BUSY_MESSAGE = "A chat with a run under way cannot be deleted. Wait for the run to end, or cancel it, and try again.";
// How often the ages at the right of the chats are read again, so a "5m" does not stay while the page rests.
const AGE_REFRESH_MS = 60_000;
// The states of a run that works; one that waits for you is "blocked" and keeps its dot and its age.
const WORKING = new Set(["queued", "running"]);
// How often the time of a row that works is read again: its seconds count.
const WORKING_REFRESH_MS = 1000;

function startOfDay(now, daysBack) {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack).getTime() / 1000;
}

function dayLabel(ts, now) {
  if (ts >= startOfDay(now, 0)) {
    return DAY_LABELS[0];
  }
  if (ts >= startOfDay(now, 1)) {
    return DAY_LABELS[1];
  }
  return ts >= startOfDay(now, 7) ? DAY_LABELS[2] : DAY_LABELS[3];
}

/** The chats under "Today", "Yesterday", "Previous 7 days" and "Older", latest activity first; a day without chats is left out. */
function groupByDay(chats, now) {
  const latestFirst = [...chats].sort((a, b) => b.updated_at - a.updated_at);
  return DAY_LABELS.map((label) => ({ label, chats: latestFirst.filter((chat) => dayLabel(chat.updated_at, now) === label) })).filter(
    (group) => group.chats.length > 0,
  );
}

/** The pinned chats under "Pinned", the last pinned first, then the others by day (item 130). */
function groupChats(chats, now) {
  const pinned = chats.filter((chat) => chat.pinned_at !== null).sort((a, b) => b.pinned_at - a.pinned_at);
  const days = groupByDay(
    chats.filter((chat) => chat.pinned_at === null),
    now,
  );
  return pinned.length === 0 ? days : [{ label: "Pinned", chats: pinned }, ...days];
}

function matchesQuery(chat, needle) {
  return needle === "" || foldSearch(chat.title).includes(needle);
}

/** A section's label as the button that folds it: a chevron that turns, the words, a count. */
function foldLabel(tag, folded, onToggle, ...words) {
  return el(
    tag,
    { class: "side-label" },
    el("button", { class: "side-fold", type: "button", "aria-expanded": folded ? "false" : "true", onclick: onToggle }, icon("chevron"), ...words),
  );
}

function mark(node, current) {
  if (current) {
    node.setAttribute("aria-current", "page");
  } else {
    node.removeAttribute("aria-current");
  }
}

/** What a row whose run works shows at its right, where its age sits at rest (item 122): a
 * dashed circle, "Working" and the time since the run was asked for, at `since` (seconds). */
function workingSlot(since) {
  return el(
    "span",
    { class: "side-working", title: `Working since ${dateTime(since)}` },
    icon("circleDashed"),
    el("span", null, "Working"),
    el("span", { class: "side-working-time", "data-since": String(since) }, workingFor(since)),
  );
}

/** Whose face a chat wears and how (item 277): the look of its agent or its campaign, or a look of its own from its id
 * for a chat with Dot; at work while its latest run works, waiting while that run waits for you, sad once it failed. */
function chatFaceOf(chat) {
  const actor = chat.actor_id ?? null;
  return {
    id: actor ?? chat.id,
    state: faceState(chat.latest_status),
    look: actor === null ? chatLook(chat.id) : lookOf(actor, null),
  };
}

/** A chat's name over its last line (item 277): its latest words, "You:" before the owner's own. */
function chatText(chat) {
  const words = chat.last_message ?? "";
  const last = words === "" || chat.last_role !== "user" ? words : `You: ${words}`;
  return el(
    "span",
    { class: "side-chat-text" },
    el("span", { class: "side-run-goal" }, clip(chat.title, 120)),
    last === "" ? null : el("span", { class: "side-chat-last" }, last),
  );
}

/** A chat as a link: it opens on the latest run a person started on it. Its creature (`face`, item 277) stands at its
 * left, its name over its last line; at its right, its last activity in the fewest characters (item 121), or, while its
 * latest run works, Working and for how long (item 122), over the accent dot of a chat that is unread (item 131). A
 * daemon from before item 131 sends no `unread`, and its chats are read. */
function chatLink(chat, onNavigate, face) {
  const word = [STATUS_WORD[chat.latest_status], chat.unread === true ? "unread" : undefined].filter((part) => part !== undefined).join(", ");
  const title = clip(chat.title, 120);
  return el(
    "a",
    {
      class: "side-run",
      href: `#/runs/${chat.latest_run_id}`,
      title: clip(chat.title, 240),
      "aria-label": word === "" ? null : `${title} (${word})`,
      onclick: onNavigate,
    },
    face,
    chatText(chat),
    el(
      "span",
      { class: "side-chat-meta" },
      WORKING.has(chat.latest_status)
        ? workingSlot(chat.latest_created_at)
        : el("span", { class: "side-age", "data-ts": String(chat.updated_at), title: dateTime(chat.updated_at) }, age(chat.updated_at)),
      chat.unread === true ? el("span", { class: "side-unread" }) : null,
    ),
  );
}

/** One row of the activity sections: the state dot, the title and what it needs or where it works. A delegated run
 * (`child`) sits under its parent with its steps, and one that ended says how. A run of the Working section says
 * Working and for how long at its right (item 122); a schedule due there does not work yet. */
function activityRow(row, onNavigate, child) {
  const ended = ENDED.has(row.status);
  const detail = child ? progressFacts(row) : [row.channel, row.detail].filter((part) => part !== "");
  const facts = ended ? [row.status, ...detail] : detail;
  const words = facts.join(", ");
  const state = row.section === "waiting" ? "waiting" : ended ? faceState(row.status) : "working";
  return el(
    "a",
    {
      class: child ? "side-run side-activity side-child" : "side-run side-activity",
      href: row.href,
      title: clip(row.title, 240),
      "aria-label": words === "" ? row.title : `${row.title} (${words})`,
      onclick: onNavigate,
    },
    row.actor_id === null
      ? el("span", { class: `status-dot s-${row.section === "waiting" ? "blocked" : ended ? row.status : "running"}` })
      : actorFace(row.actor_id, state, null),
    el(
      "span",
      { class: "side-run-goal" },
      el("span", { class: "side-activity-title" }, clipWords(row.title, ROW_WORDS, ROW_CHARS)),
      facts.length === 0 ? null : el("small", { class: "side-activity-detail" }, facts.join(" · ")),
    ),
    row.section === "working" && row.run_id !== null && WORKING.has(row.status) ? workingSlot(row.since) : null,
  );
}

/** `head`, the row of the run `runId`, with the runs it delegated under it, at work or ended (item 198), behind a
 * button that says how many and folds them; with none, `head` alone. `folded` holds the runs whose delegated runs are
 * folded; `onFold(runId, folded)` keeps a change, which the button shows at once. */
function withDelegated(head, runId, children, onNavigate, folded, onFold) {
  if (children.length === 0) {
    return head;
  }
  const hidden = folded.has(runId);
  const list = el("div", { class: "side-children", id: `side-children-${runId}`, hidden }, children.map((kid) => activityRow(kid, onNavigate, true)));
  const working = children.filter((kid) => WORKING.has(kid.status)).length;
  const count = children.length === 1 ? "1 delegated run" : `${children.length} delegated runs`;
  const toggle = el(
    "button",
    {
      class: "side-fold side-children-fold",
      type: "button",
      "aria-expanded": hidden ? "false" : "true",
      "aria-controls": list.id,
      onclick: () => {
        const fold = !list.hidden;
        list.hidden = fold;
        toggle.setAttribute("aria-expanded", fold ? "false" : "true");
        onFold(runId, fold);
      },
    },
    icon("chevron"),
    working > 0 && working < children.length ? `${count} · ${working} working` : count,
  );
  return el("div", { class: "side-family" }, head, toggle, list);
}

/** The rows of a section: each parent, then the runs it delegated under it (`withDelegated`). */
function activityRows(rows, onNavigate, folded, onFold) {
  return nestRows(rows).map(({ row, children }) => withDelegated(activityRow(row, onNavigate, false), row.run_id, children, onNavigate, folded, onFold));
}

/**
 * Where the runs at work stand (item 202). `rows` are the rows of Working with the delegated runs that ended under
 * them, `chats` the chats listed. A chat whose latest run is one of them stands once, as that chat, with the runs it
 * delegated: under Working (`working`, `{ chat, row, children }`), unless it is pinned, which keeps it under Pinned
 * (`pinned`, its family by chat id). A row that is no listed chat's (a schedule about to fire, a run whose chat is not
 * listed, a delegated run whose parent is not) stands under Working as it is, its `chat` null.
 */
function placeWork(rows, chats) {
  const byRun = new Map(chats.map((chat) => [chat.latest_run_id, chat]));
  const working = [];
  const pinned = new Map();
  for (const { row, children } of nestRows(rows)) {
    const chat = row.run_id === null ? undefined : byRun.get(row.run_id);
    if (chat === undefined) {
      working.push({ chat: null, row, children });
    } else if (chat.pinned_at === null) {
      working.push({ chat, row, children });
    } else {
      pinned.set(chat.id, { row, children });
    }
  }
  return { working, pinned };
}

/** A chat as a row to tick while chats are being chosen for deletion, with its creature (`face`), its name and its
 * last line: as tall as its row at rest, so the list keeps its place when the choosing ends. */
function chatPick(chat, checked, onToggle, face) {
  const box = el("input", {
    class: "side-check",
    type: "checkbox",
    checked,
    "aria-label": clip(chat.title, 120),
    onchange: () => onToggle(chat.id, box.checked),
  });
  return el(
    "label",
    { class: "side-run side-pick", title: clip(chat.title, 240) },
    box,
    face,
    chatText(chat),
  );
}

/**
 * Build the sidebar. `homeHref` is where "New chat" leads; `onCollapse()` runs when its hide button is pressed and
 * `onNavigate()` after any link in it was followed. Returns the element, `focusSearch()`, `focusClose()`,
 * `setRoute({ tab, runId })` to mark where the user is, and `dispose()`.
 */
export function buildSidebar({ homeHref, onCollapse, onNavigate }) {
  let chats = null;
  let listLimit = LIST_LIMIT;
  let failure = null;
  let needle = "";
  let routeRunId = null;
  // The chat that holds the run on the page; resolved from the list when the run is the latest one of a chat, otherwise
  // asked from the daemon.
  let routeThreadId = null;
  // The open chat last brought into the list's view (item 204): the list follows the page when it comes to another
  // chat, and otherwise stays where the owner scrolled it, through a delete, a pin, a rename, a reload of the activity
  // and a follow-up in the same chat, which moves the chat under Working.
  let revealedThreadId = null;
  let resolveTicket = 0;
  // The open chat the owner marked unread (item 131): it stays so until it is opened again or the page comes back into
  // view. Any other open chat is seen whenever the page is in view, so a run that ends there leaves no dot.
  let keptUnread = null;
  let selecting = false;
  // The chat whose name is being typed in its row (item 130): the list is painted again once the name is kept or left.
  let renaming = null;
  let disposed = false;
  const selected = new Set();
  const rowNodes = new Map();
  // The creature of each chat listed (item 277), kept from one painting of the list to the next while the chat wears the
  // same look in the same state: the list is painted again whenever a run starts or ends, and drawing two hundred
  // creatures anew took 180 ms with a phone's slower processor.
  const faces = new Map();
  // The chats' rows and the sections of their days, kept from one painting of the list to the next while what they show
  // is the same (item 328): the list read again as the page comes back leaves them standing, so their creatures go on
  // breathing where they were. A row whose name is typed in it is made anew after.
  const keptRows = new Map();
  const keptGroups = new Map();
  const list = el("div", { class: "side-runs" });
  const ageTicker = setInterval(() => {
    for (const node of list.querySelectorAll(".side-age")) {
      setText(node, age(Number(node.dataset.ts)));
    }
  }, AGE_REFRESH_MS);
  const search = el("input", {
    class: "side-search-input",
    type: "search",
    placeholder: "Search chats",
    "aria-label": "Search chats",
    autocomplete: "off",
    spellcheck: "false",
  });
  const close = el("button", { class: "icon-btn", type: "button", title: "Hide sidebar", "aria-label": "Hide sidebar", onclick: onCollapse });
  const selectButton = el("button", { class: "btn ghost sm", type: "button", hidden: true, onclick: () => enterSelectMode() }, "Select");
  let folds = readFolds(FOLDS_KEY);
  function toggleFold(key) {
    folds = { ...folds, [key]: folds[key] !== true };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    paintLists();
  }
  const chatsLabel = foldLabel("h2", folds.chats === true, () => toggleFold("chats"), "Chats");
  const head = el("div", { class: "side-list-head" }, chatsLabel, selectButton);
  const count = el("span", { class: "grow side-count" });
  const allButton = el("button", { class: "btn sm", type: "button", onclick: () => toggleAll() }, "Select all");
  const deleteButton = el(
    "button",
    { class: "btn danger solid sm", type: "button", onclick: () => attempt(deleteSelected) },
    icon("trash"),
    "Delete",
  );
  const cancelButton = el(
    "button",
    { class: "icon-btn", type: "button", title: "Cancel selection", "aria-label": "Cancel selection", onclick: () => leaveSelectMode() },
    icon("x"),
  );
  const bar = el(
    "div",
    { class: "side-select-bar", hidden: true },
    el("div", { class: "row" }, count, cancelButton),
    el("div", { class: "row" }, allButton, deleteButton),
  );
  let activity = null;
  // Where the runs at work stand (`placeWork`), worked out at each paint, and Working's entries as painted, each with
  // the id of its chat (null for a row that is no chat's).
  let work = { working: [], pinned: new Map() };
  let workingNodes = [];
  const activityNav = el("nav", { class: "side-activity-nav", "aria-label": "Activity", hidden: true });
  const agentsLink = el("a", { class: "side-link", href: "#/agents", onclick: onNavigate }, icon("user"), el("span", null, "Agents"));
  const campaignsLink = el("a", { class: "side-link", href: "#/campaigns", onclick: onNavigate }, icon("target"), el("span", null, "Campaigns"));
  const settingsLink = el("a", { class: "side-link", href: "#/settings", onclick: onNavigate }, icon("sliders"), el("span", null, "Settings"));
  // Customize (item 316): the services Dot connects to and the skills it reads, a page among the others under Campaigns.
  const schedulesLink = el("a", { class: "side-link", href: "#/schedules", onclick: onNavigate }, icon("calendar"), el("span", null, "Schedules"));
  const customizeLink = el("a", { class: "side-link", href: "#/customize/plugins", onclick: onNavigate }, icon("plug"), el("span", null, "Customize"));

  // The rows the arrow keys walk and the search's Enter opens, in order: Working's and the list's, the delegated runs
  // under a chat among them; while chats are being chosen, their boxes. A row folded away is left out.
  function rows() {
    const nodes = selecting
      ? list.querySelectorAll("input.side-check")
      : [...activityNav.querySelectorAll("[data-section=working] a.side-run"), ...list.querySelectorAll("a.side-run")];
    return [...nodes].filter((node) => node.getClientRects().length > 0);
  }

  function atWork(chat) {
    return work.working.some((entry) => entry.chat !== null && entry.chat.id === chat.id);
  }

  // The chats of the list: every chat but those that stand under Working (item 202).
  function shownChats() {
    return chats === null ? [] : chats.filter((chat) => !atWork(chat) && matchesQuery(chat, needle));
  }

  function markCurrent() {
    for (const [id, node] of rowNodes) {
      mark(node, id === routeThreadId);
    }
    // Folded, Working keeps the open chat in sight (item 202): a chat just sent on its way
    // from the composer stands there.
    for (const { chatId, node } of workingNodes) {
      node.hidden = folds.working === true && (chatId === null || chatId !== routeThreadId);
    }
    const active = rowNodes.get(routeThreadId);
    if (active !== undefined && routeThreadId !== revealedThreadId) {
      revealedThreadId = routeThreadId;
      active.scrollIntoView({ block: "nearest" });
    }
  }

  // The page is in view when it is shown and its window has the focus: a window behind others shows nothing new.
  function inView() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  /** Mark the open chat seen when the page is in view and the chat is unread: its row loses the dot at once. */
  function seeOpenChat() {
    if (routeThreadId === null || routeThreadId === keptUnread || chats === null || !inView()) {
      return;
    }
    const open = chats.find((chat) => chat.id === routeThreadId);
    if (open === undefined || open.unread !== true) {
      return;
    }
    chats = chats.map((chat) => (chat.id === open.id ? { ...chat, unread: false } : chat));
    paintLists();
    api.patch(`/threads/${open.id}`, { seen: true }).catch((error) => console.warn("the open chat could not be marked seen", error));
  }

  function onBackInView() {
    keptUnread = null;
    seeOpenChat();
  }

  function paintBar() {
    const shown = shownChats();
    const allChosen = shown.length > 0 && shown.every((chat) => selected.has(chat.id));
    setText(count, selected.size === 0 ? "Choose chats to delete" : `${plural(selected.size, "chat", "chats")} selected`);
    setText(allButton, allChosen ? "Clear" : "Select all");
    allButton.disabled = shown.length === 0;
    deleteButton.disabled = selected.size === 0;
  }

  // The list is cut when the daemon filled the whole page: older chats may be waiting, and the search only sees the ones
  // loaded.
  function olderNote() {
    if (chats === null || chats.length < listLimit) {
      return [];
    }
    if (listLimit >= LIST_CAP) {
      return [el("div", { class: "side-note" }, el("span", null, `Only the latest ${listLimit} chats are listed.`))];
    }
    return [
      el(
        "div",
        { class: "side-note" },
        el(
          "button",
          {
            class: "btn sm",
            type: "button",
            onclick: () => {
              listLimit = Math.min(listLimit + LIST_LIMIT, LIST_CAP);
              load();
            },
          },
          "Show older chats",
        ),
      ),
    ];
  }

  // What the list says when it shows no chat: that there is none yet, or none that matches the search; nothing while
  // every chat stands under Working, or while one there matches.
  function emptyNote() {
    if (needle !== "") {
      const found = work.working.some((entry) => entry.chat !== null && matchesQuery(entry.chat, needle));
      return found ? [] : [el("div", { class: "side-note" }, `No chat matches “${search.value.trim()}”.`)];
    }
    return chats.length === 0 ? [el("div", { class: "side-note" }, "No chats yet. Start one with New chat.")] : [];
  }

  // The "…" button beside the link `node` of a chat; null beside a box.
  function moreOf(node) {
    return node.closest(".side-chat")?.querySelector(".side-more") ?? null;
  }

  // The sections and the list, painted together: a chat stands in one place or the other (item 202). The paint takes
  // the rows away, so the focus goes back to the chat whose link, "…" button or box had it (a chat reached by a key).
  function paintLists() {
    if (renaming !== null) {
      return;
    }
    const active = document.activeElement;
    const focused = active === null ? undefined : [...rowNodes].find(([, node]) => node.contains(active) || moreOf(node) === active);
    const onMore = focused !== undefined && moreOf(focused[1]) === active;
    // A chat's menu acts on the rows it was opened from, which go now.
    closeMenu();
    rowNodes.clear();
    // The runs at work show those they delegated that have ended too (item 198). Until the chats come, what works waits
    // for them, so that a chat at work does not show first as its run's row; if they cannot come, the rows show.
    const loading = chats === null && failure === null;
    work = placeWork(activity === null || loading ? [] : [...activity.working, ...activity.ended_delegated], chats ?? []);
    // A chat chosen for deletion whose run starts leaves the list for Working, and the choice goes with it: a chat at
    // work cannot be deleted.
    for (const entry of work.working) {
      if (entry.chat !== null) {
        selected.delete(entry.chat.id);
      }
    }
    paintActivity();
    paintChats();
    paintBar();
    markCurrent();
    const listed = new Set((chats ?? []).map((chat) => chat.id));
    for (const id of faces.keys()) {
      if (!listed.has(id)) {
        faces.delete(id);
      }
    }
    if (focused !== undefined) {
      const node = rowNodes.get(focused[0]);
      const target = node === undefined ? null : onMore ? moreOf(node) : (node.querySelector("input.side-check") ?? node);
      target?.focus({ preventScroll: true });
    }
  }

  function paintChats() {
    selectButton.hidden = selecting || chats === null || chats.every((chat) => atWork(chat));
    head.hidden = selecting;
    bar.hidden = !selecting;
    // The chats fold under their label (item 95); choosing chats to delete needs them in sight.
    const foldedChats = folds.chats === true && !selecting;
    chatsLabel.firstChild.setAttribute("aria-expanded", foldedChats ? "false" : "true");
    list.hidden = foldedChats;
    if (failure !== null) {
      list.replaceChildren(
        el(
          "div",
          { class: "side-note" },
          el("span", null, "The chats could not be loaded."),
          el(
            "button",
            {
              class: "btn sm",
              type: "button",
              onclick: () => {
                failure = null;
                paintLists();
                load();
              },
            },
            icon("refresh"),
            "Try again",
          ),
        ),
      );
      return;
    }
    if (chats === null) {
      list.replaceChildren(el("div", { class: "side-note" }, el("div", { class: "spinner" }), el("span", null, "Loading chats…")));
      return;
    }
    const shown = shownChats();
    if (shown.length === 0) {
      list.replaceChildren(...emptyNote(), ...olderNote());
      return;
    }
    const groups = groupChats(shown, new Date());
    keepChildren(list, [
      ...groups.map((group) => {
        const section = keptGroups.get(group.label) ?? el("section", { class: "side-group" }, el("h3", { class: "side-label" }, group.label));
        keptGroups.set(group.label, section);
        keepChildren(section, [
          section.firstChild,
          ...group.chats.map((chat) => {
            if (selecting) {
              const pick = chatPick(chat, selected.has(chat.id), toggle, faceOf(chat));
              rowNodes.set(chat.id, pick);
              return pick;
            }
            return keptEntry(chat);
          }),
        ]);
        return section;
      }),
      ...olderNote(),
    ]);
    const labels = new Set(groups.map((group) => group.label));
    for (const label of keptGroups.keys()) {
      if (!labels.has(label)) {
        keptGroups.delete(label);
      }
    }
    const listed = new Set(shown.map((chat) => chat.id));
    for (const id of keptRows.keys()) {
      if (!listed.has(id)) {
        keptRows.delete(id);
      }
    }
  }

  // A chat's entry in the list as it was painted last, while what it shows is the same; else a new one (item 328).
  function keptEntry(chat) {
    const family = work.pinned.get(chat.id) ?? null;
    const key = JSON.stringify([chat, family]);
    const kept = keptRows.get(chat.id);
    if (kept !== undefined && kept.key === key) {
      rowNodes.set(chat.id, kept.link);
      return kept.node;
    }
    const node = chatEntry(chat, family);
    keptRows.set(chat.id, { key, node, link: rowNodes.get(chat.id) });
    return node;
  }

  // The sections above the chats. Working's entries are all painted, the open chat's among them, and `markCurrent`
  // hides them while Working is folded; the search leaves there only the chats it finds (item 202).
  function paintActivity() {
    workingNodes = [];
    const found = needle === "" ? work.working : work.working.filter((entry) => entry.chat !== null && matchesQuery(entry.chat, needle));
    const counts = { waiting: activity === null ? 0 : activity.waiting.length, working: found.length };
    const groups = activity === null ? [] : ACTIVITY_SECTIONS.filter(({ key }) => counts[key] > 0);
    activityNav.hidden = groups.length === 0;
    activityNav.replaceChildren(
      ...groups.map(({ key, label }) => {
        const folded = folds[key] === true;
        let items = null;
        if (key === "working") {
          items = found.map(workingEntry);
        } else if (!folded) {
          items = activityRows(activity[key], onNavigate, foldedRuns(), foldRun);
        }
        return el(
          "section",
          { class: "side-group", "data-section": key, "data-folded": folded ? "true" : null },
          foldLabel("h3", folded, () => toggleFold(key), `${label} `, el("span", { class: "side-label-count" }, String(counts[key]))),
          items,
        );
      }),
    );
  }

  // An entry of Working: a chat at work, or a row that is no chat's, each with the runs it delegated under it.
  function workingEntry(entry) {
    const node =
      entry.chat === null
        ? withDelegated(activityRow(entry.row, onNavigate, false), entry.row.run_id, entry.children, onNavigate, foldedRuns(), foldRun)
        : chatEntry(entry.chat, entry);
    workingNodes.push({ chatId: entry.chat === null ? null : entry.chat.id, node });
    return node;
  }

  // A chat's creature: the one drawn for it before, while it wears the same look in the same state.
  function faceOf(chat) {
    const { id, state, look } = chatFaceOf(chat);
    const key = `${state} ${look.shape} ${look.colour} ${look.expression}`;
    const kept = faces.get(chat.id);
    if (kept !== undefined && kept.key === key) {
      return kept.face;
    }
    const face = actorFace(id, state, look);
    faces.set(chat.id, { key, face });
    return face;
  }

  // A chat's entry: its row and, while its latest run works (`family`, that run's row and the runs it delegated; null
  // at rest), those runs under it (item 202).
  function chatEntry(chat, family) {
    const link = chatLink(chat, onNavigate, faceOf(chat));
    rowNodes.set(chat.id, link);
    const row = chatRow(chat, link);
    return family === null ? row : withDelegated(row, family.row.run_id, family.children, onNavigate, foldedRuns(), foldRun);
  }

  // A chat's row: its link and the button of its menu, which a right-click on the row and the context-menu key open
  // too (item 130).
  function chatRow(chat, link) {
    const more = el(
      "button",
      {
        class: "side-more",
        type: "button",
        title: "More",
        "aria-label": `More for ${clip(chat.title, 120)}`,
        "aria-haspopup": "menu",
        "aria-expanded": "false",
      },
      icon("ellipsis"),
    );
    more.addEventListener("click", () => {
      if (menuOpenedBy(more)) {
        closeMenu();
        return;
      }
      const box = more.getBoundingClientRect();
      openMenu({ label: chat.title, items: chatActions(chat), at: { x: box.left, y: box.bottom + 4 }, back: more, button: more });
    });
    link.addEventListener("keydown", (event) => {
      if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
        event.preventDefault();
        const box = link.getBoundingClientRect();
        openMenu({ label: chat.title, items: chatActions(chat), at: { x: box.left + 16, y: box.bottom }, back: link });
      }
    });
    const row = el("div", { class: "side-chat", "data-chat": chat.id }, link, more);
    // While the row holds the name's field, a right-click is the field's own.
    row.addEventListener("contextmenu", (event) => {
      if (renaming !== null) {
        return;
      }
      event.preventDefault();
      openMenu({ label: chat.title, items: chatActions(chat), at: { x: event.clientX, y: event.clientY }, back: link });
    });
    return row;
  }

  /** @returns {Array<import("./menu.js").MenuEntry>} */
  function chatActions(chat) {
    /** @type {Array<import("./menu.js").MenuEntry | null>} */
    const actions = [
      chat.pinned_at === null
        ? { label: "Pin", icon: "pin", run: () => attempt(() => pinChat(chat, true)) }
        : { label: "Unpin", icon: "pinOff", run: () => attempt(() => pinChat(chat, false)) },
      readAction(chat),
      { label: "Rename", icon: "pencil", run: () => renameChat(chat) },
      { label: "Copy link", icon: "link", run: () => attempt(() => copyLink(chat)) },
      "separator",
      // A chat under Working is not in the list, where the chats are chosen.
      atWork(chat) ? null : { label: "Select", icon: "squareCheck", run: () => enterSelectMode(chat.id) },
      { label: "Delete", icon: "trash", danger: true, run: () => attempt(() => deleteChats([chat.id])) },
    ];
    return actions.filter((item) => item !== null);
  }

  // Mark read on an unread chat, Mark unread on a read one whose latest run is over (item 131); a daemon from before
  // item 131 sends no `unread`, and neither is offered.
  function readAction(chat) {
    if (chat.unread === true) {
      return { label: "Mark read", icon: "eye", run: () => attempt(() => markChat(chat, true)) };
    }
    if (chat.unread === false && ENDED.has(chat.latest_status)) {
      return { label: "Mark unread", icon: "eyeOff", run: () => attempt(() => markChat(chat, false)) };
    }
    return null;
  }

  // The focus follows the chat to its new place, and the list stays where the owner scrolled it (item 204): a pinned
  // chat goes to the top of the list, and an unpinned one among its days.
  async function markChat(chat, seen) {
    if (chat.id === routeThreadId) {
      keptUnread = seen ? null : chat.id;
    }
    await api.patch(`/threads/${chat.id}`, { seen });
    await load();
    rowNodes.get(chat.id)?.focus({ preventScroll: true });
  }

  async function pinChat(chat, pinned) {
    await api.patch(`/threads/${chat.id}`, { pinned });
    await load();
    rowNodes.get(chat.id)?.focus({ preventScroll: true });
  }

  // The chat's name becomes a field in its row: Enter or leaving it keeps a name that changed, Escape leaves the old
  // one, and an empty name is not kept. A key gives the focus back to the row; leaving the field leaves it where it went.
  function renameChat(chat) {
    const link = rowNodes.get(chat.id);
    if (link === undefined) {
      return;
    }
    const field = el("input", {
      class: "input side-rename",
      type: "text",
      value: chat.title,
      maxlength: "120",
      spellcheck: "false",
      autocomplete: "off",
      "aria-label": "Chat name",
    });
    let ended = false;
    // The row comes back at once, under the new name while the daemon keeps it.
    async function end(keep) {
      if (ended) {
        return;
      }
      ended = true;
      renaming = null;
      const title = field.value.trim();
      if (!keep || title === "" || title === chat.title) {
        paintLists();
        return;
      }
      chats = chats.map((one) => (one.id === chat.id ? { ...one, title } : one));
      paintLists();
      await attempt(() => api.patch(`/threads/${chat.id}`, { title }));
      await load();
    }
    field.addEventListener("keydown", (event) => {
      // WebKit ends the composition before the key that commits it, so that key shows only as keyCode 229 (item 116).
      if (event.isComposing || event.keyCode === 229) {
        return;
      }
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault();
        end(event.key === "Enter").then(() => rowNodes.get(chat.id)?.focus());
      }
    });
    field.addEventListener("blur", () => end(true));
    renaming = chat.id;
    keptRows.delete(chat.id);
    link.parentElement.replaceChildren(field);
    field.focus();
    field.select();
  }

  // The chat's address, which opens it in this browser or another one signed in to this Dot.
  async function copyLink(chat) {
    await copyText(`${location.origin}${location.pathname}#/runs/${chat.latest_run_id}`);
    toast("ok", "Link copied.");
  }

  function toggle(id, checked) {
    if (checked) {
      selected.add(id);
    } else {
      selected.delete(id);
    }
    paintBar();
  }

  function toggleAll() {
    const shown = shownChats();
    if (shown.every((chat) => selected.has(chat.id))) {
      for (const chat of shown) {
        selected.delete(chat.id);
      }
    } else {
      for (const chat of shown) {
        selected.add(chat.id);
      }
    }
    paintLists();
  }

  // `chosen` is a chat ticked at the start, when its menu began the choosing; the focus goes to its box then.
  function enterSelectMode(chosen) {
    selecting = true;
    selected.clear();
    if (chosen !== undefined) {
      selected.add(chosen);
    }
    paintLists();
    const first = chosen === undefined ? rows()[0] : rowNodes.get(chosen)?.querySelector("input.side-check");
    (first === undefined ? cancelButton : first).focus();
  }

  // The list stays where it was scrolled to tick the chats (item 204): a box that had the focus hands it to its chat's
  // link, and otherwise it goes to Select, which takes the bar's place at the head of the list.
  function leaveSelectMode() {
    selecting = false;
    selected.clear();
    paintLists();
    if (!list.contains(document.activeElement) && !selectButton.hidden) {
      selectButton.focus({ preventScroll: true });
    }
  }

  async function deleteSelected() {
    await deleteChats([...selected]);
  }

  // Delete the chats `ids`, asked first: the ones chosen under Select, or the one of a chat's menu.
  async function deleteChats(ids) {
    if (ids.length === 0) {
      return;
    }
    const confirmed = await confirmDialog({
      title: ids.length === 1 && !selecting ? "Delete this chat?" : `Delete ${plural(ids.length, "chat", "chats")}?`,
      message:
        ids.length === 1
          ? "Its runs go with it, with every step, event and approval of those. This cannot be undone."
          : "Their runs go with them, with every step, event and approval of those. This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    let outcome;
    try {
      outcome = await request("DELETE", "/threads", { ids });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast("error", BUSY_MESSAGE);
        return;
      }
      throw error;
    }
    if (disposed) {
      return;
    }
    const gone = new Set(outcome.deleted);
    toast("ok", `Deleted ${plural(gone.size, "chat", "chats")}.`);
    chats = chats.filter((chat) => !gone.has(chat.id));
    if (selecting) {
      leaveSelectMode();
    } else {
      paintLists();
    }
    if (routeThreadId !== null && gone.has(routeThreadId)) {
      location.hash = homeHref;
    }
    await load();
  }

  async function resolveThread(runId) {
    resolveTicket += 1;
    const ticket = resolveTicket;
    let detail;
    try {
      detail = await api.get(`/runs/${runId}`);
    } catch (error) {
      console.warn("the chat of the open run could not be looked up", error);
      return;
    }
    if (disposed || ticket !== resolveTicket || routeRunId !== runId) {
      return;
    }
    routeThreadId = detail.run.thread_id;
    markCurrent();
    seeOpenChat();
  }

  /** Find the chat of the run on the page in the list, or ask the daemon which thread it is on. */
  function locateRoute() {
    if (routeRunId === null || routeThreadId !== null || chats === null) {
      return;
    }
    const owner = chats.find((chat) => chat.latest_run_id === routeRunId);
    if (owner !== undefined) {
      routeThreadId = owner.id;
      markCurrent();
      seeOpenChat();
      return;
    }
    resolveThread(routeRunId);
  }

  // The runs whose delegated runs the owner folded (item 198), kept with the folds of the sections; a run that is no
  // longer listed is forgotten at the next fold.
  function foldedRuns() {
    return new Set(Array.isArray(folds.runs) ? folds.runs.filter((id) => typeof id === "string") : []);
  }

  function foldRun(runId, fold) {
    const listed = new Set(activity === null ? [] : activity.working.map((row) => row.run_id));
    const runs = new Set([...foldedRuns()].filter((id) => listed.has(id)));
    if (fold) {
      runs.add(runId);
    } else {
      runs.delete(runId);
    }
    folds = { ...folds, runs: [...runs] };
    writePref(FOLDS_KEY, JSON.stringify(folds));
  }

  // The newest load answers for what is shown: an older answer that comes after it is dropped, not painted over it.
  let activityTicket = 0;
  let chatsTicket = 0;

  async function loadActivity() {
    const mine = ++activityTicket;
    try {
      const view = await api.get("/activity");
      if (disposed || mine !== activityTicket) {
        return;
      }
      activity = view;
    } catch (error) {
      if (activity === null) {
        console.warn("the activity of the sidebar could not be loaded", error);
      } else {
        readFailed("The activity of the sidebar", error);
      }
      return;
    }
    paintLists();
  }

  async function load() {
    loadActivity();
    const mine = ++chatsTicket;
    try {
      const all = await api.get(`/threads?limit=${listLimit}`);
      if (disposed || mine !== chatsTicket) {
        return;
      }
      chats = all;
      failure = null;
      const known = new Set(all.map((chat) => chat.id));
      for (const id of [...selected]) {
        if (!known.has(id)) {
          selected.delete(id);
        }
      }
    } catch (error) {
      if (disposed || mine !== chatsTicket) {
        return;
      }
      if (chats !== null) {
        readFailed("The chat list", error);
        return;
      }
      failure = error;
    }
    paintLists();
    locateRoute();
    seeOpenChat();
  }

  const reloadSoon = debounce(() => {
    load();
  }, 250);
  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      reloadSoon();
    }
  });
  const stopStream = onStreamBack(reloadSoon);
  document.addEventListener("visibilitychange", onBackInView);
  window.addEventListener("focus", onBackInView);

  // Escape drops the selection, unless the search field or an open dialog took the key first.
  function onEscape(event) {
    if (event.key !== "Escape" || event.defaultPrevented || !selecting) {
      return;
    }
    if (event.target instanceof Element && event.target.closest("dialog") !== null) {
      return;
    }
    event.preventDefault();
    leaveSelectMode();
  }
  window.addEventListener("keydown", onEscape);

  search.addEventListener("input", () => {
    needle = foldSearch(search.value);
    paintLists();
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (search.value !== "") {
        event.preventDefault();
        search.value = "";
        needle = "";
        paintLists();
      } else {
        search.blur();
      }
    } else if (event.key === "ArrowDown") {
      const first = rows()[0];
      if (first !== undefined) {
        event.preventDefault();
        first.focus();
      }
    } else if (event.key === "Enter") {
      const first = rows()[0];
      if (first !== undefined) {
        event.preventDefault();
        first.click();
      }
    }
  });
  // The arrows walk the rows of `rows()`, from Working into the list and back; up from the first, the search.
  function walkRows(event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
      return;
    }
    const nodes = rows();
    const index = nodes.indexOf(document.activeElement);
    if (index === -1) {
      return;
    }
    event.preventDefault();
    const next = nodes[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next !== undefined) {
      next.focus();
    } else if (event.key === "ArrowUp") {
      search.focus();
    }
  }
  activityNav.addEventListener("keydown", walkRows);
  list.addEventListener("keydown", walkRows);

  paintLists();
  load();

  // On a phone the sidebar is the list of chats over the whole screen (item 294): Dot's name
  // at its head beside the close, and at its foot, under the thumb, Settings, the search and a new chat. The search
  // moves between the head of the list and the foot as the width changes; there is one box.
  const searchBox = el("label", { class: "side-search" }, icon("search"), search, el("kbd", { "aria-hidden": "true" }, "⌘K"));
  const compose = el(
    "a",
    {
      class: "side-compose",
      href: homeHref,
      title: "New chat",
      "aria-label": "New chat",
      onclick: () => {
        requestCompose();
        onNavigate();
      },
    },
    icon("squarePen"),
  );
  const body = el(
    "div",
    { class: "side-body" },
    searchBox,
    el(
      "a",
      {
        class: "side-btn",
        href: homeHref,
        onclick: () => {
          requestCompose();
          onNavigate();
        },
      },
      icon("pencil"),
      el("span", { class: "grow" }, "New chat"),
      el("kbd", { "aria-hidden": "true" }, "⇧⌘O"),
    ),
    el("nav", { class: "side-links", "aria-label": "Pages" }, agentsLink, campaignsLink, schedulesLink, customizeLink),
    activityNav,
    el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list),
  );
  const foot = el("div", { class: "side-foot" }, settingsLink, compose);
  function placeForWidth() {
    close.replaceChildren(icon(PHONE.matches ? "x" : "panelLeft"));
    if (PHONE.matches) {
      settingsLink.after(searchBox);
    } else {
      body.prepend(searchBox);
    }
  }
  placeForWidth();
  PHONE.addEventListener("change", placeForWidth);
  const element = el(
    "aside",
    { class: "sidebar", id: "sidebar", "aria-label": "Sidebar" },
    el("div", { class: "side-head", "data-tauri-drag-region": "" }, close, el("span", { class: "side-title" }, dotName())),
    body,
    foot,
  );
  const workTicker = setInterval(() => {
    for (const node of element.querySelectorAll(".side-working-time")) {
      setText(node, workingFor(Number(node.dataset.since)));
    }
  }, WORKING_REFRESH_MS);

  return {
    element,
    focusSearch() {
      search.focus();
      search.select();
    },
    focusClose() {
      close.focus();
    },
    setRoute({ tab, runId }) {
      mark(agentsLink, tab === "agents");
      mark(campaignsLink, tab === "campaigns");
      mark(schedulesLink, tab === "schedules");
      mark(settingsLink, tab === "settings");
      mark(customizeLink, tab === "customize");
      routeRunId = runId;
      routeThreadId = null;
      keptUnread = null;
      markCurrent();
      locateRoute();
    },
    dispose() {
      disposed = true;
      closeMenu();
      clearInterval(ageTicker);
      clearInterval(workTicker);
      stopLifecycle();
      stopStream();
      document.removeEventListener("visibilitychange", onBackInView);
      window.removeEventListener("focus", onBackInView);
      window.removeEventListener("keydown", onEscape);
      PHONE.removeEventListener("change", placeForWidth);
    },
  };
}
