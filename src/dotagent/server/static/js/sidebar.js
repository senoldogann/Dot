// The sidebar: a new chat, a search over the chats, the ways to the agents, the activity, the campaigns and the
// schedules, what waits and what works (folded or not, as the owner left them), the chats themselves (several of them
// can be deleted at once) and, in the foot, Connect apps (the way to Customize, item 545) and the way to the settings;
// a run stops from its composer (item 289). A row whose run works says Working and for how long (item 122). A chat's
// menu (item 130) pins it, names it, copies its link, starts choosing chats with it and deletes it; the pinned chats
// come first, under Pinned.
// A chat whose latest run ended after it was last seen is unread (item 131): an accent dot, until it is opened, or Mark
// read. Each chat stands once (item 202): a chat at work stands under Working, wearing the faces of the runs it delegated
// (item 507), and goes back among the days once it rests; a pinned one stays under Pinned, with the same faces.

import { ApiError, api, request, SCHEDULES_CHANGED_EVENT, TEAMS_CHANGED_EVENT } from "./api.js";
import { age, clip, clipWords, copyText, dateTime, debounce, el, foldSearch, keepChildren, plural, setText, workingFor } from "./dom.js";
import { icon } from "./icons.js";
import { actorFace, lookOf, teamDuo } from "./actors.js";
import { onAssistant } from "./assistant.js";
import { dotName, faceState } from "./face.js";
import { chatLook } from "./hues.js";
import { PHONE } from "./layout.js";
import { loopRhythm, nextFiring } from "./rhythm.js";
import { markContent, markInk } from "./marks.js";
import { closeMenu, menuOpenedBy, openMenu } from "./menu.js";
import { nestRows } from "./nesting.js";
import { readFolds, writePref } from "./prefs.js";
import { buildSettingsNav } from "./settingsnav.js";
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
// Connect apps at the foot shows the marks of this many servers it is connected to (item 492), another few of them
// every so often when it is connected to more (item 509).
const CONNECTED_MARKS = 3;
// Where Connect apps leads (item 545): the services' tab of Customize.
const CONNECTORS_PATH = "#/customize/connectors";
const MARKS_TURN_MS = 6000;
// Which sections the owner folded (`waiting`, `working`, `chats`), kept between visits.
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

/** Whether a chat's row wears its team (item 610): a chat with a team while its latest run is Dot's, which leads the
 * team; one whose latest run is an agent's the owner put in it wears that agent's face, as the chat's own page does. */
function wearsTeam(chat) {
  return chat.team !== null && chat.team !== undefined && chat.actor_id === chat.team.id;
}

/** The face a chat wears in its row (item 277): the faces of two of its team's agents, one over the other (`wearsTeam`),
 * or its creature (`chatFaceOf`). */
function chatFace(chat, state) {
  const { id, look } = chatFaceOf(chat);
  return wearsTeam(chat) ? teamDuo(chat.team, state) : actorFace(id, state, look);
}

/** A copy of `items` in a random order (Fisher–Yates). */
function shuffled(items) {
  const copy = [...items];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const other = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[other]] = [copy[other], copy[index]];
  }
  return copy;
}

// The faces a row shows of the runs it delegated, the rest counted (item 507), as the marks of the connected apps stand
// over one another in the foot.
const SIDE_HELPERS = 3;

/** What a row delegated, in words: "1 helper", "4 helpers", nothing for a row that delegated none (item 507). */
function helperWords(children) {
  return children.length === 1 ? "1 helper" : children.length > 1 ? `${children.length} helpers` : "";
}

/** The runs a row delegated, as small faces laid over one another (item 507): three at most, the rest counted, a run
 * without an actor a dot of its state. The row that wears them opens the chat they work in. */
function helperFaces(children) {
  if (children.length === 0) {
    return null;
  }
  const shown = children.slice(0, SIDE_HELPERS);
  const more = children.length - shown.length;
  return el(
    "span",
    { class: "side-helpers", "aria-hidden": "true" },
    ...shown.map((kid) =>
      kid.actor_id === null
        ? el(
            "span",
            { class: "side-helper" },
            el("span", { class: `status-dot s-${WORKING.has(kid.status) ? "running" : kid.status}` }),
          )
        : el(
            "span",
            { class: "side-helper" },
            actorFace(kid.actor_id, WORKING.has(kid.status) ? "working" : faceState(kid.status), null),
          ),
    ),
    more > 0 ? el("span", { class: "side-helper-more" }, `+${more}`) : null,
  );
}

/** A chat's name over its last line (item 277): its latest words, "You:" before the owner's own — or, while runs it
 * delegated stand with it, their faces with how many they are (item 507), the line its last words would take. */
function chatText(chat, children) {
  const words = chat.last_message ?? "";
  const last = words === "" || chat.last_role !== "user" ? words : `You: ${words}`;
  const faces = helperFaces(children);
  // A chat with a team (item 610) says which, ahead of its last words.
  const team = chat.team === null || chat.team === undefined ? null : el("span", { class: "side-chat-team", title: chat.team.name }, chat.team.name);
  const under =
    faces === null
      ? last === "" && team === null
        ? null
        : el("span", { class: "side-chat-last" }, team, last === "" ? null : el("span", { class: "side-chat-words" }, last))
      : el("span", { class: "side-chat-last side-chat-helpers" }, team, faces);
  return el("span", { class: "side-chat-text" }, el("span", { class: "side-run-goal" }, clip(chat.title, 120)), under);
}

/** What a chat's loop says of itself (item 580): how often it asks again and when next ("Loops every 30 minutes, next
 * 15:30", "Loops every day at 10:00, next Sat 10:00"; item 590), or null for a chat with no loop. A daemon from before
 * item 580 sends none. */
function loopWords(chat) {
  const cron = chat.loop_cron ?? null;
  if (cron === null) {
    return null;
  }
  const next = chat.loop_next_at ?? null;
  const rhythm = `Loops ${loopRhythm(cron) ?? "on its schedule"}`;
  return next === null ? rhythm : `${rhythm}, next ${nextFiring(next, Date.now() / 1000)}`;
}

/** A chat's creature, with the mark of its loop at its foot (item 580): arrows that turn slowly in a glow, so that a
 * chat that asks again by itself stands out in the list. The row's name says the loop to a screen reader. */
function loopFace(face, words) {
  if (words === null) {
    return face;
  }
  return el("span", { class: "side-face" }, face, el("span", { class: "side-loop", title: words, "aria-hidden": "true" }, icon("refresh")));
}

/** A chat as a link: it opens on the latest run a person started on it. Its creature (`face`, item 277) stands at its
 * left, with the mark of its loop when it has one (item 580), its name over its last line, where the faces of the runs
 * it delegated stand instead of its last words while they work (item 507); at its right its last activity in the
 * fewest characters (item 121), or, while its latest run works, Working and for how long (item 122), over the accent
 * dot of a chat that is unread (item 131). A daemon from before item 131 sends no `unread`, and its chats are read. */
function chatLink(chat, onNavigate, face, children) {
  const loop = loopWords(chat);
  const word = [
    chat.team === null || chat.team === undefined ? undefined : `with ${chat.team.name}`,
    STATUS_WORD[chat.latest_status],
    helperWords(children),
    chat.unread === true ? "unread" : undefined,
    loop === null ? undefined : loop.toLowerCase(),
  ]
    .filter((part) => part !== undefined && part !== "")
    .join(", ");
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
    loopFace(face, loop),
    chatText(chat, children),
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

/** One row of the activity sections: the state dot, the title and what it needs or where it works, and the faces of the
 * runs it delegated (item 507). A run of the Working section says Working and for how long at its right (item 122). */
function activityRow(row, onNavigate, children) {
  const ended = ENDED.has(row.status);
  const detail = [row.channel, row.detail].filter((part) => part !== "");
  const facts = ended ? [row.status, ...detail] : detail;
  const words = [...facts, helperWords(children)].filter((part) => part !== "").join(", ");
  const state = row.section === "waiting" ? "waiting" : ended ? faceState(row.status) : "working";
  return el(
    "a",
    {
      class: "side-run side-activity",
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
    helperFaces(children),
    row.section === "working" && row.run_id !== null && WORKING.has(row.status) ? workingSlot(row.since) : null,
  );
}

/** The rows of a section: each run, wearing the faces of the runs it delegated (item 507). */
function activityRows(rows, onNavigate) {
  return nestRows(rows).map(({ row, children }) => activityRow(row, onNavigate, children));
}

/**
 * Where the runs at work stand (item 202). `rows` are the rows of Working with the delegated runs that ended under
 * them, `chats` the chats listed. A chat whose latest run is one of them stands once, as that chat, with the runs it
 * delegated: under Working (`working`, `{ chat, row, children }`), unless it is pinned, which keeps it under Pinned
 * (`pinned`, its family by chat id). A row that is no listed chat's (a run whose chat is not listed, a delegated run
 * whose parent is not) stands under Working as it is, its `chat` null.
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
    chatText(chat, []),
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
  // Where the runs at work stand (`placeWork`), worked out at each paint, and Working's entries as painted.
  let work = { working: [], pinned: new Map() };
  let workingNodes = [];
  const activityNav = el("nav", { class: "side-activity-nav", "aria-label": "Activity", hidden: true });
  const agentsLink = el("a", { class: "side-link", href: "#/agents", onclick: onNavigate }, icon("user"), el("span", null, "Agents"));
  const campaignsLink = el("a", { class: "side-link", href: "#/campaigns", onclick: onNavigate }, icon("target"), el("span", null, "Campaigns"));
  // Settings is its cog at the foot, beside Connect apps (item 492); its name stays for a reader of the screen.
  const settingsLink = el(
    "a",
    { class: "side-link side-settings", href: "#/settings", title: "Settings", "aria-label": "Settings", onclick: onNavigate },
    icon("settings"),
    el("span", null, "Settings"),
  );
  const schedulesLink = el("a", { class: "side-link", href: "#/schedules", onclick: onNavigate }, icon("calendar"), el("span", null, "Schedules"));

  // The rows the arrow keys walk and the search's Enter opens, in order: Working's and the list's; while chats are being
  // chosen, their boxes. A row folded away is left out.
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
    // Folded, Working hides every row, the open chat's too (item 513: it kept that one in sight, item 202, so a fold with
    // only the open chat at work did nothing); its count stays, and a search shows the chats at work it finds.
    for (const { node } of workingNodes) {
      node.hidden = folds.working === true && needle === "";
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
              const pick = chatPick(chat, selected.has(chat.id), toggle, pickFaceOf(chat));
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

  // The sections above the chats. Working's entries are all painted, and `markCurrent` hides them while Working is
  // folded; the search leaves there only the chats it finds (item 202), and shows them folded or not.
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
          items = activityRows(activity[key], onNavigate);
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

  // An entry of Working: a chat at work, or a row that is no chat's, each wearing the faces of the runs it delegated.
  function workingEntry(entry) {
    const node =
      entry.chat === null ? activityRow(entry.row, onNavigate, entry.children) : chatEntry(entry.chat, entry);
    workingNodes.push({ node });
    return node;
  }

  // A chat's creature: the one drawn for it before, while the same one wears the same look in the same state.
  function faceOf(chat) {
    const { id, state, look } = chatFaceOf(chat);
    // A chat that wears its team wears the faces of its agents (item 610): kept while they and the state are the same.
    // Whose face it is goes in the key: a team's chat whose latest run became an agent's at rest, the page seeing
    // only the end of it, wears the agent's face, and an agent's new look is drawn.
    const key = wearsTeam(chat)
      ? `team ${state} ${chat.team.agents.slice(0, 2).map((agent) => `${agent} ${JSON.stringify(lookOf(agent, null))}`).join(" ")}`
      : `${state} ${id} ${look.shape} ${look.colour} ${look.expression}`;
    const kept = faces.get(chat.id);
    if (kept !== undefined && kept.key === key) {
      return kept.face;
    }
    const face = chatFace(chat, state);
    faces.set(chat.id, { key, face });
    return face;
  }

  // The creature of a chat's row while chats are chosen: one of its own, as the kept one stays in the chat's kept link,
  // which comes back when the choosing ends.
  function pickFaceOf(chat) {
    return chatFace(chat, chatFaceOf(chat).state);
  }

  // A chat's entry: its row, wearing while its latest run works (`family`, that run's row and the runs it delegated;
  // null at rest) the faces of those runs (items 202 and 507).
  function chatEntry(chat, family) {
    const link = chatLink(chat, onNavigate, faceOf(chat), family === null ? [] : family.children);
    rowNodes.set(chat.id, link);
    return chatRow(chat, link);
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
  // The daemon tells of no change of a schedule: a page that made one says so, and the marks of the loops follow.
  window.addEventListener(SCHEDULES_CHANGED_EVENT, reloadSoon);
  // Nor of a team's (item 610): the chats with it are named and drawn as the team is now.
  window.addEventListener(TEAMS_CHANGED_EVENT, reloadSoon);
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
    el("nav", { class: "side-links", "aria-label": "Pages" }, agentsLink, campaignsLink, schedulesLink),
    activityNav,
    el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list),
  );
  // Connect apps (items 492 and 545): at the foot, right of the cog of Settings, the way to Customize › Connectors, the
  // page of the services Dot works with, with the marks of the MCP servers it is connected to; the list of pages no
  // longer names Customize. On a phone, a round button of its own.
  const connectMarks = el("span", { class: "side-connect-marks", "aria-hidden": "true" });
  const connectButton = el(
    "button",
    {
      class: "side-connect",
      type: "button",
      title: "Connect apps",
      "aria-label": "Connect apps",
      onclick: () => {
        location.hash = CONNECTORS_PATH;
      },
    },
    icon("plug"),
    el("span", { class: "side-connect-label" }, "Connect apps"),
    connectMarks,
  );
  // The marks of the servers that are ready, each as its service's tile wears it (items 408 and 477).
  let readyServers = [];
  async function paintConnected() {
    let found;
    try {
      found = await api.get("/mcp/servers");
    } catch (error) {
      console.warn("the servers Dot is connected to could not be read for Connect apps", error);
      return;
    }
    if (disposed) {
      return;
    }
    readyServers = found.servers.filter((server) => server.state === "ready" && server.mark !== null);
    showMarks();
  }
  /** Show a few of the ready servers' marks, chosen afresh each time. */
  function showMarks() {
    const shown = shuffled(readyServers).slice(0, CONNECTED_MARKS);
    connectMarks.replaceChildren(
      ...shown.map((server) => {
        const mark = el("span", { class: "side-connect-mark" }, markContent(server.mark));
        const ink = markInk(server.mark.colour);
        if (ink !== null) {
          mark.style.setProperty("--plugin-ink", ink);
        }
        return mark;
      }),
    );
  }
  paintConnected();
  const marksTicker = setInterval(() => {
    if (readyServers.length > CONNECTED_MARKS) {
      showMarks();
    }
  }, MARKS_TURN_MS);
  const foot = el("div", { class: "side-foot" }, settingsLink, connectButton, compose);
  function placeForWidth() {
    close.replaceChildren(icon(PHONE.matches ? "x" : "panelLeft"));
    if (PHONE.matches) {
      connectButton.after(searchBox);
    } else {
      body.prepend(searchBox);
    }
  }
  placeForWidth();
  PHONE.addEventListener("change", placeForWidth);
  // While the settings are open the sidebar is their nav in place of the chats and the pages (item 562); a phone keeps the
  // sidebar as it is, its settings being a list of tabs on a page of their own.
  const settingsNav = buildSettingsNav({ homeHref, onCollapse, onNavigate });
  const element = el(
    "aside",
    { class: "sidebar", id: "sidebar", "aria-label": "Sidebar" },
    el("div", { class: "side-head", "data-tauri-drag-region": "" }, close, el("span", { class: "side-title" }, dotName())),
    body,
    foot,
    settingsNav.element,
  );
  let routeTab = null;
  function paintMode() {
    if (routeTab === "settings" && !PHONE.matches) {
      element.dataset.mode = "settings";
      settingsNav.setRoute();
    } else {
      delete element.dataset.mode;
    }
  }
  PHONE.addEventListener("change", paintMode);
  // The name the owner calls Dot by, as it changes (item 420).
  onAssistant((names) => setText(element.querySelector(".side-title"), names.name));
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
      if (element.dataset.mode === "settings") {
        settingsNav.focusClose();
      } else {
        close.focus();
      }
    },
    setRoute({ tab, runId }) {
      routeTab = tab;
      paintMode();
      mark(agentsLink, tab === "agents");
      mark(campaignsLink, tab === "campaigns");
      mark(schedulesLink, tab === "schedules");
      mark(settingsLink, tab === "settings");
      // Customize is reached from Connect apps (item 545), which stands for it while it is open; on the way back from
      // it, where a server may have been connected or removed, the marks of the servers that are ready are read again.
      const leavingCustomize = connectButton.hasAttribute("data-current") && tab !== "customize";
      connectButton.toggleAttribute("data-current", tab === "customize");
      // The styles read data-current; a screen reader reads aria-current, as the links of the pages give it (item 559).
      mark(connectButton, tab === "customize");
      if (leavingCustomize) {
        paintConnected();
      }
      routeRunId = runId;
      routeThreadId = null;
      keptUnread = null;
      markCurrent();
      locateRoute();
    },
    dispose() {
      disposed = true;
      settingsNav.dispose();
      closeMenu();
      clearInterval(ageTicker);
      clearInterval(workTicker);
      clearInterval(marksTicker);
      stopLifecycle();
      stopStream();
      window.removeEventListener(SCHEDULES_CHANGED_EVENT, reloadSoon);
      window.removeEventListener(TEAMS_CHANGED_EVENT, reloadSoon);
      document.removeEventListener("visibilitychange", onBackInView);
      window.removeEventListener("focus", onBackInView);
      window.removeEventListener("keydown", onEscape);
      PHONE.removeEventListener("change", placeForWidth);
      PHONE.removeEventListener("change", paintMode);
    },
  };
}
