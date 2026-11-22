// The sidebar, one component on a Mac beside the page and on a phone as its drawer (items 1327 and 1328, after the reference app's):
// a head with the title; the search with three small buttons at its right (the agents' menu, a new agent, a message to
// Dot); the chats as rows of three lines, the pinned first and then the days; and, in a dock that keeps its place, Working
// (what waits for the owner and what works, with a count and a fold) and Settled (the chats resting past a week, folded);
// and, at the foot, four small buttons: the settings, Connect apps (the way to Customize, item 545), the Mac's screen live
// and the models. A run stops from its composer (item 289). A row whose run works says Working and for how long (item
// 122). A chat's menu (item 130) pins it, names it, copies its link, starts choosing chats with it and deletes it (several
// can be deleted at once); the pinned chats come first, under Pinned.
// A chat whose latest run ended after it was last seen is unread (item 131): an accent dot, until it is opened, or Mark
// read. Each chat stands once (item 202): a chat at work stands under Working, counting the runs it delegated (item 507),
// and goes back among the days once it rests; a pinned one stays under Pinned, with the same count.
// The chats a channel's messages made (item 619: the "iMessage" chat) leave the days for a section of their own at the foot
// of the list, with a count and no mark (item 828); it folds like the other groups do and keeps its fold, and a search
// finds its chats as it finds the others. Dot's own work does too (item 822): the chats of On its own and of the
// schedules stand folded at the foot, so that the days hold the owner's own chats.
// The agents are a menu (item 1328), where a column of faces and a phone's rows stood: each its badge, its name and how many
// chats it has, to its conversation (item 1233), else its page; under them All agents, a new agent and Campaigns.

import { AGENTS_CHANGED_EVENT, ApiError, api, request, SCHEDULES_CHANGED_EVENT, sharedGet, TEAMS_CHANGED_EVENT } from "./api.js";
import { age, clip, copyText, dateTime, debounce, el, foldSearch, keepChildren, setText, workingFor } from "./dom.js";
import { serviceFailure, serviceWords } from "./errors.js";
import { t, tCount } from "./i18n.js";
import { goTo } from "./hop.js";
import { icon } from "./icons.js";
import { DOT_ID } from "./actors.js";
import { onAssistant } from "./assistant.js";
import { badge, badgeCode } from "./badge.js";
import { shownOwner } from "./presence.js";
import { dotName } from "./face.js";
import { requestCompose } from "./shortcuts.js";
import { PHONE } from "./layout.js";
import { loopRhythm, nextFiring } from "./rhythm.js";
import { closeMenu, closeMenuFrom, menuOpenedBy, openMenu, openOnHold } from "./menu.js";
import { placeWork } from "./nesting.js";
import { readFolds, writePref } from "./prefs.js";
import { buildSettingsNav } from "./settingsnav.js";
import { onLifecycle, onStreamBack, readFailed } from "./state.js";
import { attempt, confirmDialog, toast } from "./ui.js";

// The chats listed at first, the step by which "Show older chats" lists more, and the most the API lists at once.
const LIST_LIMIT = 200;
const LIST_CAP = 500;
// The moments a run changes status; a run that waits for an approval is "blocked" until it is answered.
const RELOAD_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided", "takeover", "handback", "chat_rewound"]);
// The groups above the chats: what waits for you and what works now (`GET /activity`), each hidden while empty. They
// are rows as a messaging app's (item 836), with no heading: a row says that it waits or works itself, and its group's
// name is for a screen reader.
const ACTIVITY_SECTIONS = [
  { key: "waiting", label: t("side.waiting") },
  { key: "working", label: t("side.working") },
];
// What a chat's row says while something of it waits for the owner, ahead of what waits (item 836).
const WAITS_WORDS = t("side.waiting");
const DAY_LABELS = [t("side.today"), t("side.yesterday"), t("side.week"), t("side.older")];
// Where Connect apps leads (item 545): the services' tab of Customize.
const CONNECTORS_PATH = "#/customize/connectors";
// Which sections at the foot the owner folded, by their ids (`work`, `agents`, `reviews`, `imessage`), kept between visits; a fold of
// `chats`, `initiative` or `schedule` kept from before item 855, or of `waiting` or `working` from before item 836,
// whose groups no longer fold, is not read.
const FOLDS_KEY = "dot.sideFolds";
// The sections at the foot of the list (item 619), by the `source` of the threads each holds (a channel's id, as the
// router makes the thread): the section's name and whether it starts folded. Dot's own work, the chats of its own
// initiative and of its schedules, is one (item 855, after "On its own" and "Scheduled" of their own, items 822 and
// 827), folded until the owner opens it; the agents' chats, one an agent, the work Dot sends them, another (item 1082:
// the owner found the list full of them); the code reviews of the pre-push hook another; iMessage's another. A thread of any other source is a chat among the days.
// No label has a mark, so that it stands where the days' labels do (items 827, 828 and 855).
const CHANNEL_SECTIONS = {
  work: { label: t("side.dotsWork"), folded: true, sources: ["initiative", "schedule"] },
  agents: { label: t("side.agentsWork"), folded: true, sources: ["team"] },
  reviews: { label: t("side.reviews"), folded: true, sources: ["review"] },
  imessage: { label: "iMessage", folded: false, sources: ["imessage"] },
};
// The sections of the dock at the foot of the list (the rows of the reference app's sidebar): what works and waits, and the chats
// that rest past a week, which stand Settled, folded. They keep their folds under keys of their own, so that a fold of
// `working` or `waiting` kept from before item 836 is never read as theirs.
const DOCK_KEY = "dock";
const SETTLED = { label: t("side.settled"), folded: true };
const SECTION_OF_SOURCE = new Map(Object.entries(CHANNEL_SECTIONS).flatMap(([id, section]) => section.sources.map((source) => [source, id])));
// What a status dot means, for screen readers; a chat whose latest run is done or cancelled shows no dot and needs no word.
const STATUS_WORD = {
  queued: t("side.statusQueued"),
  running: t("side.statusRunning"),
  blocked: t("side.statusBlocked"),
  failed: t("side.statusFailed"),
  ending: t("side.statusEnding"),
};
// The states of a run that is over: only such a chat can be marked unread (item 131).
const ENDED = new Set(["done", "failed", "cancelled"]);
const BUSY_MESSAGE = t("side.busy");
// How often the ages at the right of the chats are read again, so a "5m" does not stay while the page rests.
const AGE_REFRESH_MS = 60_000;
// The states of a run that works; one that waits for you is "blocked" and keeps its dot and its age.
const WORKING = new Set(["queued", "running", "ending"]);
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

/** The pinned chats under "Pinned", the last pinned first, then the others by day (item 130); what is older than a
 * week is not a day's but Settled's, which the dock holds (`settled`). */
function groupChats(chats, now) {
  const pinned = chats.filter((chat) => chat.pinned_at !== null).sort((a, b) => b.pinned_at - a.pinned_at);
  const unpinned = chats.filter((chat) => chat.pinned_at === null);
  const all = groupByDay(unpinned, now);
  const days = all.filter((group) => group.label !== DAY_LABELS[3]);
  const settled = all.find((group) => group.label === DAY_LABELS[3])?.chats ?? [];
  return { groups: pinned.length === 0 ? days : [{ label: t("side.pinned"), chats: pinned }, ...days], settled };
}

/** The channel whose messages made `chat` (item 619), or null: a thread whose source is a channel's id. A chat the
 * owner pinned stays under Pinned, where they put it. */
function channelOf(chat) {
  return chat.pinned_at === null && typeof chat.source === "string" ? (SECTION_OF_SOURCE.get(chat.source) ?? null) : null;
}

function matchesQuery(chat, needle) {
  return needle === "" || foldSearch(chat.title).includes(needle);
}

/** A section's label as the button that folds it: the words, a count and a chevron that turns, at its right. */
function foldLabel(tag, folded, onToggle, ...words) {
  return el(
    tag,
    { class: "side-label" },
    el("button", { class: "side-fold", type: "button", "aria-expanded": folded ? "false" : "true", onclick: onToggle }, ...words, icon("chevron")),
  );
}

function mark(node, current) {
  if (current) {
    node.setAttribute("aria-current", "page");
  } else {
    node.removeAttribute("aria-current");
  }
}

/** Whose chat a row is, for the row's first line: the agent's name and its two-letter badge. */
function agentLabel(name) {
  return { name, code: badgeCode(name) };
}

/** What a row whose run works shows at its right, where its age sits at rest (item 122): a
 * dashed circle, "Working" and the time since the run was asked for, at `since` (seconds), in a quiet pill. */
function workingSlot(since) {
  return el(
    "span",
    { class: "side-working", title: t("side.workingSince", { when: dateTime(since) }) },
    icon("circleDashed"),
    el("span", null, t("side.working")),
    el("span", { class: "side-working-time", "data-since": String(since) }, workingFor(since)),
  );
}

/** What a row that waits for the owner shows at its right (item 836, the pill the reference app's rows have): the warning's dot,
 * "Waiting" and how long it has waited, since `since` (seconds). */
function waitingSlot(since) {
  return el(
    "span",
    { class: "side-waiting", title: t("side.waitingSince", { when: dateTime(since) }) },
    el("span", { class: "status-dot s-blocked side-waits-dot" }),
    el("span", null, t("side.waitingShort")),
    el("span", { class: "side-working-time", "data-since": String(since) }, workingFor(since)),
  );
}

/** What a row delegated, in words: "1 helper", "4 helpers", nothing for a row that delegated none (item 507). */
function helperWords(children) {
  return children.length === 1 ? t("side.helper") : children.length > 1 ? t("side.helpers", { count: children.length }) : "";
}

/** The runs a row delegated, as one quiet count in the row's last line (item 507): the users mark and how many, the row that wears it opens the chat they work in. */
function helperChip(children) {
  if (children.length === 0) {
    return null;
  }
  const working = children.filter((kid) => WORKING.has(kid.status)).length;
  return el(
    "span",
    { class: "side-helpers", "aria-hidden": "true", title: helperWords(children), dataset: { working: String(working) } },
    icon("users"),
    el("span", null, String(children.length)),
  );
}

/** A row's first line: the badge and name of its agent, and at the right what it says of its run (`right`). */
function rowTop(agent, right) {
  return el(
    "span",
    { class: "side-chat-top" },
    el("span", { class: "side-badge", "aria-hidden": "true", dataset: { code: agent.code } }),
    el("span", { class: "side-agent" }, agent.name),
    right === null ? null : el("span", { class: "side-chat-meta" }, right),
  );
}

/** A row's text, three quiet lines (the rows of the reference app's sidebar): the agent's badge and name with the run's age or state
 * at the right; the chat's title, in the heavier weight, one line; and its last line, which holds the marks of its loop
 * and its campaign, the team it is with, what it delegated (item 507) or its last words — "You:" before the owner's own
 * (item 277) — and the dot of an unread chat. While something of it waits for the owner (`waits`, its rows of `GET
 * /activity`, oldest first) the last line says "Waiting for you:", what has waited longest and how many more wait, in the
 * warning's ink (item 836). */
function chatText(chat, agent, children, waits = null, right = null, unread = false) {
  const title = el("span", { class: "side-run-goal" }, clip(chat.title, 120));
  const top = rowTop(agent, right);
  const loop = loopWords(chat);
  const campaign = campaignWords(chat);
  const marks = [
    campaign === null ? null : el("span", { class: "side-campaign", title: campaign, "aria-hidden": "true" }, icon("target")),
    loop === null ? null : el("span", { class: "side-loop", title: loop, "aria-hidden": "true" }, icon("refresh")),
  ];
  const dot = unread ? el("span", { class: "side-unread" }) : null;
  if (waits !== null) {
    const more = waits.length > 1 ? ` (+${waits.length - 1})` : "";
    return el("span", { class: "side-chat-text" }, top, title, waitsLine(`${WAITS_WORDS}: ${waits[0].title}${more}`, ...marks));
  }
  // A chat its model service stopped says so in a few words of what stopped it (the service's sentence, a billing link
  // and all, stood here after item 1136); its own line stands in the chat. Any other chat ends on its last words.
  const stopped = chat.latest_status === "failed" ? serviceFailure(chat.last_message ?? "") : null;
  const words = stopped === null ? serviceWords(chat.last_message ?? "").text : stopped.short;
  const last = words === "" || chat.last_role !== "user" || stopped !== null ? words : t("side.you", { words });
  const helpers = helperChip(children);
  // A chat with a team (item 610) says which, ahead of its last words.
  const team = chat.team === null || chat.team === undefined ? null : el("span", { class: "side-chat-team", title: chat.team.name }, chat.team.name);
  const empty = last === "" && team === null && helpers === null && marks.every((mark) => mark === null) && dot === null;
  const under = empty
    ? null
    : el(
        "span",
        { class: helpers === null ? "side-chat-last" : "side-chat-last side-chat-helpers" },
        ...marks,
        team,
        helpers,
        last === "" || helpers !== null ? null : el("span", { class: "side-chat-words", dataset: stopped === null ? {} : { stopped: "" } }, last),
        dot,
      );
  return el("span", { class: "side-chat-text" }, top, title, under);
}

/** The last line of a row that waits for the owner (item 836), in the warning's ink, after the marks `marks` it wears. */
function waitsLine(words, ...marks) {
  return el("span", { class: "side-chat-last side-chat-waits" }, ...marks, el("span", { class: "side-chat-words" }, words));
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
  const rhythm = t("side.loops", { rhythm: loopRhythm(cron) ?? t("side.onSchedule") });
  return next === null ? rhythm : t("side.loopsNext", { rhythm, when: nextFiring(next, Date.now() / 1000) });
}

/** What a chat or a row at work says of its campaign (item 705), or null: the conversation of a campaign, or one of its
 * sessions, which the Campaigns page lists. */
const CAMPAIGN_CHAT = t("side.campaignChat");
const CAMPAIGN_SESSION = t("side.campaignSession");

/** What a chat says of its campaign: the conversation of one (item 705), or null. A daemon from before sends none. */
function campaignWords(chat) {
  return chat.campaign_id === null || chat.campaign_id === undefined ? null : CAMPAIGN_CHAT;
}

/** A chat as a link: it opens on the latest run a person started on it. Three lines (`chatText`): its agent's badge and
 * name with, at the right, its last activity in the fewest characters (item 121), or, while its latest run works,
 * Working and for how long (item 122), or, while it waits, Waiting and for how long (item 836); its title; and its last
 * line, with the dot of a chat that is unread (item 131). A daemon from before item 131 sends no `unread`, and its chats
 * are read. */
function chatLink(chat, onNavigate, agent, children, waits = null) {
  const loop = loopWords(chat);
  const word = [
    chat.team === null || chat.team === undefined ? undefined : t("side.withTeam", { name: chat.team.name }),
    waits === null ? STATUS_WORD[chat.latest_status] : `${WAITS_WORDS.toLowerCase()}: ${waits.map((row) => `${row.title}, ${row.detail}`).join("; ")}`,
    helperWords(children),
    // While it waits its row shows no dot of an unread chat, so its name says none either (the pre-push review).
    chat.unread === true && waits === null ? t("side.unread") : undefined,
    loop === null ? undefined : loop.toLowerCase(),
    campaignWords(chat)?.toLowerCase(),
  ]
    .filter((part) => part !== undefined && part !== "")
    .join(", ");
  const title = clip(chat.title, 120);
  const right =
    waits !== null
      ? waitingSlot(waits[0].since)
      : WORKING.has(chat.latest_status)
        ? workingSlot(chat.latest_created_at)
        : el("span", { class: "side-age", "data-ts": String(chat.updated_at), title: dateTime(chat.updated_at) }, age(chat.updated_at));
  return el(
    "a",
    {
      class: "side-run",
      href: `#/runs/${chat.latest_run_id}`,
      title: clip(chat.title, 240),
      "aria-label": word === "" ? null : `${title} (${word})`,
      onclick: onNavigate,
    },
    chatText(chat, agent, children, waits, right, chat.unread === true && waits === null),
  );
}

/** A run at work that is no listed chat's (a session of a campaign after its first, a run whose chat is not listed),
 * drawn as a chat's row is (item 705): its agent's badge and name, what it does, with the mark of its campaign when it
 * is a session of one, and at its right Working and for how long. */
function workRow(row, onNavigate, children, agent) {
  const detail = [row.channel, row.detail].filter((part) => part !== "");
  const campaign = row.kind === "campaign" ? CAMPAIGN_SESSION : null;
  const words = [...detail, helperWords(children)].filter((part) => part !== "").join(", ");
  const helpers = helperChip(children);
  return el(
    "div",
    { class: "side-chat", dataset: { actor: row.actor_id ?? "" } },
    el(
      "a",
      {
        class: "side-run",
        href: row.href,
        title: clip(row.title, 240),
        "aria-label": `${clip(row.title, 120)} (${["working", words].filter(Boolean).join(", ")})`,
        onclick: onNavigate,
      },
      el(
        "span",
        { class: "side-chat-text" },
        rowTop(agent, WORKING.has(row.status) ? workingSlot(row.since) : null),
        el("span", { class: "side-run-goal" }, clip(row.title, 120)),
        helpers === null && detail.length === 0 && campaign === null
          ? null
          : el(
              "span",
              { class: helpers === null ? "side-chat-last" : "side-chat-last side-chat-helpers" },
              campaign === null ? null : el("span", { class: "side-campaign", title: campaign, "aria-hidden": "true" }, icon("target")),
              helpers,
              helpers !== null || detail.length === 0 ? null : el("span", { class: "side-chat-words" }, detail.join(" · ")),
            ),
      ),
    ),
  );
}

/** What waits for the owner that is no listed chat's (a run whose chat is not listed, a delegated run, a session of a
 * campaign), drawn as a chat's row is (item 836): its agent's badge and name with Waiting and how long at the right,
 * what waits over where it stands, in the warning's ink. */
function waitingRow(row, onNavigate, agent) {
  const ended = ENDED.has(row.status);
  const facts = [ended ? row.status : "", row.detail].filter((part) => part !== "");
  return el(
    "div",
    { class: "side-chat", dataset: { actor: row.actor_id ?? "" } },
    el(
      "a",
      {
        class: "side-run",
        href: row.href,
        title: clip(row.title, 240),
        "aria-label": `${clip(row.title, 120)} (${[WAITS_WORDS.toLowerCase(), ...facts].join(", ")})`,
        onclick: onNavigate,
      },
      el(
        "span",
        { class: "side-chat-text" },
        rowTop(agent, waitingSlot(row.since)),
        el("span", { class: "side-run-goal" }, clip(row.title, 120)),
        waitsLine([WAITS_WORDS, ...facts].join(" · ")),
      ),
    ),
  );
}

/**
 * Where what waits for the owner stands (item 836), as `placeWork` places what works. `rows` are Waiting's, read
 * oldest first. A row whose run is a listed chat's latest stands as that chat, once however many of its rows wait, its last
 * line saying what waits (`waiting`, `{ chat, row }`), unless the chat is pinned, which keeps it under Pinned; either
 * way its row says so (`byChat`, the rows of each chat that waits, oldest first). A row that is no listed chat's stands
 * as it is, its `chat` null.
 */
function placeWaiting(rows, chats) {
  const byRun = new Map(chats.map((chat) => [chat.latest_run_id, chat]));
  const waiting = [];
  const byChat = new Map();
  // Oldest first whatever order they came in, so that a chat's line names what has waited longest.
  for (const row of [...rows].sort((one, other) => one.since - other.since)) {
    const chat = row.run_id === null ? undefined : byRun.get(row.run_id);
    if (chat === undefined) {
      waiting.push({ chat: null, row });
      continue;
    }
    const kept = byChat.get(chat.id);
    if (kept !== undefined) {
      kept.push(row);
      continue;
    }
    byChat.set(chat.id, [row]);
    if (chat.pinned_at === null) {
      waiting.push({ chat, row });
    }
  }
  return { waiting, byChat };
}


/** A chat as a row to tick while chats are being chosen for deletion, with its agent's badge and name, its title and its
 * last line: as tall as its row at rest, so the list keeps its place when the choosing ends. */
function chatPick(chat, checked, onToggle, agent) {
  const box = el("input", {
    class: "side-check",
    type: "checkbox",
    checked,
    "aria-label": clip(chat.title, 120),
    onchange: () => onToggle(chat.id, box.checked),
  });
  return el("label", { class: "side-run side-pick", title: clip(chat.title, 240) }, box, chatText(chat, agent, []));
}

/**
 * Build the sidebar. `homeHref` is where "New chat" leads; `onCollapse()` runs when its hide button is pressed and
 * `onNavigate()` after any link in it was followed. Returns the element, `focusSearch()`, `focusClose()`,
 * `setRoute({ tab, runId })` to mark where the user is, and `dispose()`.
 */
// A swipe that closes the drawer on a touch screen (item 888): mostly sideways, to the left, past SWIPE_PX.
const SWIPE_PX = 56;

/** Close the drawer `element` with `onCollapse` when a finger swipes it to the left, on a phone's width. */
function closesOnSwipe(element, onCollapse) {
  let start = null;
  element.addEventListener(
    "touchstart",
    (event) => {
      const touch = event.touches.length === 1 ? event.touches[0] : null;
      start = touch === null || !PHONE.matches ? null : { x: touch.clientX, y: touch.clientY };
    },
    { passive: true },
  );
  element.addEventListener(
    "touchend",
    (event) => {
      const touch = event.changedTouches[0];
      if (start !== null && touch !== undefined) {
        const dx = touch.clientX - start.x;
        const dy = touch.clientY - start.y;
        if (dx < -SWIPE_PX && Math.abs(dy) < Math.abs(dx) / 2) {
          onCollapse();
        }
      }
      start = null;
    },
    { passive: true },
  );
}

export function buildSidebar({ homeHref, onCollapse, onNavigate }) {
  let chats = null;
  let listLimit = LIST_LIMIT;
  let failure = null;
  let needle = "";
  let routeRunId = null;
  // The tab of the page that is open (`setRoute`), which marks the pages' links and the faces of the agents.
  let routeTab = null;
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
  // The chats' rows and the sections of their days, kept from one painting of the list to the next while what they show
  // is the same (item 328): the list read again as the page comes back leaves them standing, so their creatures go on
  // breathing where they were. A row whose name is typed in it is made anew after.
  const keptRows = new Map();
  const keptGroups = new Map();
  const list = el("div", { class: "side-runs" });
  // The sections of the channels' chats (item 619), at the foot of the list, outside it so that folding Chats does not
  // fold them; each section and its label are kept from one painting to the next, so that the focus stays on a label
  // that was pressed.
  const channelsNav = el("div", { class: "side-channels" });
  const keptSections = new Map();
  const keptLabels = new Map();
  const ageTicker = setInterval(() => {
    for (const node of element.querySelectorAll(".side-age")) {
      setText(node, age(Number(node.dataset.ts)));
    }
  }, AGE_REFRESH_MS);
  const search = el("input", {
    class: "side-search-input",
    type: "search",
    placeholder: t("side.search"),
    "aria-label": t("side.search"),
    autocomplete: "off",
    spellcheck: "false",
  });
  // On a touch screen the drawer has no X (item 888, the owner's ask): a chat chosen closes it, and so does a swipe to
  // the left; a narrow window of the Mac, with no touch, keeps the button.
  const close = el("button", { class: "icon-btn side-close", type: "button", title: t("sidebar.hide"), "aria-label": t("sidebar.hide"), onclick: onCollapse });
  const selectButton = el("button", { class: "btn ghost sm", type: "button", hidden: true, onclick: () => enterSelectMode() }, t("side.select"));
  let folds = readFolds(FOLDS_KEY);
  // Whether a section is folded: as the owner left it, else as it starts (item 822: Dot's own work starts folded).
  function isFolded(key) {
    return folds[key] ?? (key === "settled" ? SETTLED.folded : CHANNEL_SECTIONS[key]?.folded) ?? false;
  }
  function toggleFold(key) {
    folds = { ...folds, [key]: !isFolded(key) };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    paintLists();
  }
  // The chats have no heading of their own (item 855): Select stands at the right of their first group's label, and
  // the days no longer fold away under a "Chats" that only named the list.
  const head = el("div", { class: "side-list-head" }, selectButton);
  const count = el("span", { class: "grow side-count" });
  const allButton = el("button", { class: "btn sm", type: "button", onclick: () => toggleAll() }, t("sidebar.selectAll"));
  const deleteButton = el(
    "button",
    { class: "btn danger solid sm", type: "button", onclick: () => attempt(deleteSelected) },
    icon("trash"),
    t("sidebar.delete"),
  );
  const cancelButton = el(
    "button",
    { class: "icon-btn", type: "button", title: t("sidebar.cancelSelection"), "aria-label": t("sidebar.cancelSelection"), onclick: () => leaveSelectMode() },
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
  // Where what waits for the owner stands (`placeWaiting`), worked out at each paint (item 836).
  let waits = { waiting: [], byChat: new Map() };
  // The chats that stand above the list, with what waits (item 836) and what works (item 202), worked out at each
  // paint. They are not in the list, where the chats are chosen, and every question of whether a chat is in it asks
  // this one set (item 836's pre-push review found the question asked apart in four places, each round one of them
  // missing a group).
  let above = new Set();
  const activityNav = el("nav", { class: "side-activity-nav", "aria-label": "Activity", hidden: true });
  const settledNav = el("nav", { class: "side-settled-nav", "aria-label": SETTLED.label, hidden: true });
  // Working's label folds what works; what waits for the owner is never folded out of sight.
  const workingCount = el("span", { class: "side-label-count" });
  const workingLabel = foldLabel("h3", false, () => toggleFold(DOCK_KEY), `${t("side.working")} `, workingCount);
  // The search, with the three small buttons at its right (the row at the top of the reference app's sidebar): the agents, a new
  // agent and a new chat. One box on a Mac and on a phone: the drawer is this sidebar.
  const searchBox = el("label", { class: "side-search" }, icon("search"), search, el("kbd", { "aria-hidden": "true" }, "⌘K"));
  // A tool of the rows at the head and the foot: an icon button that leads to a page, marked while that page is open.
  const pageTool = (href, label, name, extra = "") =>
    el("a", { class: `icon-btn side-tool ${extra}`.trim(), href, title: label, "aria-label": label, onclick: onNavigate }, icon(name));
  // The agents: a menu of the owner's agents, each with its chats' count, to its conversation (item 1233), where a message
  // to it goes on, else to its page; All agents, a new agent and Campaigns under them. It stands where a column of the
  // agents' faces stood (item 835): faces made a toy of the list, and the agents are few.
  const agentsButton = el(
    "button",
    {
      class: "icon-btn side-tool side-agents-button",
      type: "button",
      title: t("sidebar.yourAgents"),
      "aria-label": t("sidebar.yourAgents"),
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      onclick: () => void openAgentsMenu(),
    },
    icon("users"),
  );
  // A new agent: its name and its look, then its conversation. Dot itself stays one conversation. Its dialog is loaded
  // when asked for, as the page's first paint needs none of it.
  const newBot = el(
    "button",
    { class: "icon-btn side-tool side-new-agent", type: "button", title: t("sidebar.newBot"), "aria-label": t("sidebar.newBot"), onclick: () => void attempt(openNewBotDialog) },
    icon("plus"),
  );
  async function openNewBotDialog() {
    const { openNewBot } = await import("./newbot.js");
    await openNewBot();
  }
  // A message to Dot: the home, with the cursor in its box; it goes on in Dot's one conversation (item 1279).
  const composeButton = el(
    "a",
    {
      class: "icon-btn side-tool side-compose",
      href: homeHref,
      title: t("sidebar.writeTo", { name: dotName() }),
      "aria-label": t("sidebar.writeTo", { name: dotName() }),
      onclick: () => {
        onNavigate();
        requestCompose();
      },
    },
    icon("squarePen"),
  );
  const top = el("div", { class: "side-top" }, searchBox, el("div", { class: "side-tools" }, agentsButton, newBot, composeButton));
  // The foot: four small icon buttons (the reference app's), the settings with the owner's name for a screen reader, Connect apps
  // (items 492 and 545, the way to Customize › Connectors), the Mac's screen live (item 1264) and the models.
  const settingsLink = pageTool("#/settings", t("sidebar.settings"), "settings", "side-settings");
  const watchLink = pageTool("#/watch", t("sidebar.watch"), "monitor");
  const modelsLink = pageTool("#/settings/models", t("sidebar.models"), "layers");
  const connectButton = el(
    "button",
    {
      class: "icon-btn side-tool side-connect",
      type: "button",
      title: t("sidebar.connectApps"),
      "aria-label": t("sidebar.connectApps"),
      onclick: () => {
        location.hash = CONNECTORS_PATH;
      },
    },
    icon("plug"),
  );
  const foot = el("div", { class: "side-foot" }, settingsLink, connectButton, watchLink, modelsLink);
  const homes = new Map();
  // The owner's agents as `GET /agents` last gave them, Dot first and the others the conversation that moved last first.
  let agentList = [];
  let facesTicket = 0;
  let agentsNamed = null;
  function paintFaces(found) {
    // Each row names its agent: a name that came or changed is said in the rows already drawn.
    const named = JSON.stringify([dotName(), ...found.map(({ record }) => [record.id, record.name])]);
    if (named !== agentsNamed) {
      agentsNamed = named;
      agentNames.clear();
      for (const { record } of found) {
        agentNames.set(record.id, record.name);
      }
      relabel();
    }
    const lastAt = (summary) => summary.home?.updated_at ?? summary.latest?.created_at ?? summary.record.created_at;
    const dot = found.find(({ record }) => record.id === DOT_ID);
    const others = found.filter(({ record }) => record.id !== DOT_ID).sort((one, other) => lastAt(other) - lastAt(one));
    agentList = dot === undefined ? others : [dot, ...others];
    homes.clear();
    for (const { record, home } of agentList) {
      homes.set(record.id, home ?? null);
    }
    markAgents();
  }
  /** The chats an agent's runs answered last, as the chats listed hold them: Dot's are those of no agent. */
  function chatsOf(id) {
    return (chats ?? []).filter((chat) => (id === DOT_ID ? (chat.actor_id ?? DOT_ID) === DOT_ID : chat.actor_id === id)).length;
  }
  /** Where an agent's entry leads: its conversation, else its page, whose box starts the first chat with it (Dot's is the home). */
  function goToAgent(record, home) {
    if (home !== null && home !== undefined) {
      location.hash = `#/runs/${home.latest_run_id}`;
    } else {
      location.hash = record.id === DOT_ID ? homeHref : `#/agents/${record.id}`;
    }
    onNavigate();
    // Dot's own entry starts its box (item 1265), as its row did.
    if (record.id === DOT_ID && (home === null || home === undefined)) {
      requestCompose();
    }
  }
  function openPage(hash) {
    location.hash = hash;
    onNavigate();
  }
  async function openAgentsMenu() {
    if (menuOpenedBy(agentsButton)) {
      closeMenu();
      return;
    }
    // The agents are read again as the menu opens, so that an entry leads to the conversation as it is now: a chat that
    // began, or went, since the page last read them is not the one it names; a press before the first reading waits
    // for it rather than showing none.
    await agentsRead;
    await loadFaces();
    if (menuOpenedBy(agentsButton)) {
      return;
    }
    const open = /^#\/agents\/([A-Za-z0-9_-]+)$/.exec(location.hash)?.[1] ?? null;
    /** @type {Array<import("./menu.js").MenuEntry>} */
    const items = [
      ...agentList.map(({ record, home }) => {
        const name = record.id === DOT_ID ? dotName() : record.name;
        const reading = routeTab === "runs" && home !== null && home !== undefined && home.id === routeThreadId;
        return {
          label: name,
          mark: badge(name),
          detail: String(chatsOf(record.id)),
          current: reading || (routeTab === "agents" && record.id === open),
          title: record.role === "" || record.id === DOT_ID ? undefined : record.role,
          run: () => goToAgent(record, home),
        };
      }),
      "separator",
      { label: t("sidebar.allAgents"), icon: "users", run: () => openPage("#/agents") },
      { label: t("sidebar.newBot"), icon: "plus", run: () => void attempt(openNewBotDialog) },
      "separator",
      { label: t("sidebar.campaigns"), icon: "target", run: () => openPage("#/campaigns") },
    ];
    const box = agentsButton.getBoundingClientRect();
    openMenu({ label: t("sidebar.yourAgents"), items, at: { x: box.left, y: box.bottom + 4 }, back: agentsButton, button: agentsButton });
  }

  async function loadFaces() {
    const mine = ++facesTicket;
    let found;
    try {
      found = await sharedGet("/agents");
    } catch (error) {
      console.warn("the agents could not be read for the sidebar", error);
      return;
    }
    if (!disposed && mine === facesTicket) {
      paintFaces(found);
    }
  }
  // The agents' button is marked on the pages of the agents, and the foot's tools on theirs.
  function markAgents() {
    agentsButton.toggleAttribute("data-current", routeTab === "agents");
  }

  // The rows the arrow keys walk and the search's Enter opens, in order: what waits (during a search, the chats among it
  // that the search finds, item 836), what works, the list's and the channels'; while chats are being chosen, their
  // boxes. A row folded away is left out.
  function rows() {
    const nodes = selecting
      ? [...list.querySelectorAll("input.side-check"), ...channelsNav.querySelectorAll("input.side-check"), ...settledNav.querySelectorAll("input.side-check")]
      : [
          ...activityNav.querySelectorAll(needle === "" ? "[data-section=waiting] a.side-run" : "[data-section=waiting] [data-found] a.side-run"),
          ...activityNav.querySelectorAll("[data-section=working] a.side-run"),
          ...list.querySelectorAll("a.side-run"),
          ...channelsNav.querySelectorAll("a.side-run"),
          ...settledNav.querySelectorAll("a.side-run"),
        ];
    return [...nodes].filter((node) => node.getClientRects().length > 0);
  }

  // Whether a chat stands above the list, with what waits or what works, rather than in it.
  function aboveTheList(chat) {
    return above.has(chat.id);
  }

  // The chats of the list: every chat but those that stand above it.
  function shownChats() {
    return chats === null ? [] : chats.filter((chat) => !aboveTheList(chat) && matchesQuery(chat, needle));
  }

  function markCurrent() {
    for (const [id, node] of rowNodes) {
      mark(node, id === routeThreadId);
    }
    markAgents();
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
    // The agent whose conversation it is reads it as read too (item 1235).
    for (const [id, home] of homes) {
      if (home?.id === open.id) {
        homes.set(id, { ...home, unread: false });
      }
    }
    api.patch(`/threads/${open.id}`, { seen: true }).catch((error) => console.warn("the open chat could not be marked seen", error));
  }

  function onBackInView() {
    keptUnread = null;
    seeOpenChat();
    if (document.visibilityState === "visible") {
      reloadFacesSoon();
    }
  }

  function paintBar() {
    const shown = shownChats();
    const allChosen = shown.length > 0 && shown.every((chat) => selected.has(chat.id));
    setText(count, selected.size === 0 ? t("sidebar.chooseToDelete") : tCount(selected.size, "sidebar.selectedOne", "sidebar.selectedMany"));
    setText(allButton, allChosen ? t("sidebar.clear") : t("sidebar.selectAll"));
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
      return [el("div", { class: "side-note" }, el("span", null, t("sidebar.onlyLatest", { count: listLimit })))];
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
          t("side.showOlder"),
        ),
      ),
    ];
  }

  // What the list says when it shows no chat: that there is none yet, or none that matches the search; nothing while
  // every chat stands under Working or in a channel's section, or while one there matches.
  function emptyNote(inChannels) {
    if (needle !== "") {
      const found =
        inChannels ||
        [...work.working, ...waits.waiting].some((entry) => entry.chat !== null && matchesQuery(entry.chat, needle));
      return found ? [] : [el("div", { class: "side-note" }, t("sidebar.noMatch", { query: search.value.trim() }))];
    }
    return chats.length === 0 ? [el("div", { class: "side-note" }, t("sidebar.noChats"))] : [];
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
    // A chat's menu acts on the rows it was opened from, which go now (those of the list, of the channels' sections and
    // of Working and Waiting, painted below); a menu of another part of the page stays.
    closeMenuFrom(list, channelsNav, settledNav, activityNav);
    rowNodes.clear();
    // The runs at work show those they delegated that have ended too (item 198). Until the chats come, what works waits
    // for them, so that a chat at work does not show first as its run's row; if they cannot come, the rows show.
    const loading = chats === null && failure === null;
    work = placeWork(activity === null || loading ? [] : [...activity.working, ...activity.ended_delegated], chats ?? []);
    waits = placeWaiting(activity === null || loading ? [] : activity.waiting, chats ?? []);
    // What waits for the owner while its run goes on (a step kept for them, item 482) stands once, with what waits,
    // wearing what it delegated (the pre-push review: it stood with what works too, twice).
    const families = new Map(work.working.filter((entry) => entry.chat !== null).map((entry) => [entry.chat.id, entry]));
    const waitingRuns = new Set(waits.waiting.filter((entry) => entry.chat === null).map((entry) => entry.row.run_id));
    work = {
      ...work,
      working: work.working.filter((entry) => (entry.chat === null ? !waitingRuns.has(entry.row.run_id) : !waits.byChat.has(entry.chat.id))),
    };
    waits = {
      ...waits,
      waiting: waits.waiting.map((entry) => (entry.chat === null ? entry : { ...entry, family: families.get(entry.chat.id) ?? null })),
    };
    above = new Set([...work.working, ...waits.waiting].flatMap((entry) => (entry.chat === null ? [] : [entry.chat.id])));
    // A chat chosen for deletion that comes to stand above the list leaves the choice: one at work cannot be deleted,
    // and one that waits for the owner (item 836) is not in the list, where the chats are chosen.
    for (const id of above) {
      selected.delete(id);
    }
    paintActivity();
    paintChats();
    paintBar();
    markCurrent();
    if (focused !== undefined) {
      const node = rowNodes.get(focused[0]);
      const target = node === undefined ? null : onMore ? moreOf(node) : (node.querySelector("input.side-check") ?? node);
      target?.focus({ preventScroll: true });
    }
  }

  function paintChats() {
    // Select stands on the line of the days' first label (item 855): with no chat among the days shown there is none
    // (a search that finds none shows its note there instead), and a chat of a section at the foot is chosen from its
    // row's menu.
    selectButton.hidden = selecting || shownChats().every((chat) => channelOf(chat) !== null);
    head.hidden = selecting;
    bar.hidden = !selecting;
    list.hidden = false;
    if (failure !== null) {
      channelsNav.replaceChildren();
      settledNav.replaceChildren();
      list.replaceChildren(
        el(
          "div",
          { class: "side-note" },
          el("span", null, t("sidebar.loadFailed")),
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
            t("sidebar.tryAgain"),
          ),
        ),
      );
      return;
    }
    if (chats === null) {
      channelsNav.replaceChildren();
      settledNav.replaceChildren();
      list.replaceChildren(el("div", { class: "side-note" }, el("div", { class: "spinner" }), el("span", null, t("sidebar.loading"))));
      return;
    }
    const shown = shownChats();
    // The chats of the channels stand at the foot, in a section of their own each (item 619); the others among the days.
    const channelChats = shown.filter((chat) => channelOf(chat) !== null);
    const own = shown.filter((chat) => channelOf(chat) === null);
    const { groups, settled } = groupChats(own, new Date());
    const ids = paintChannels(channelChats);
    ids.push(...paintSettled(settled));
    pruneSections(ids);
    if (groups.length === 0) {
      list.replaceChildren(...emptyNote(channelChats.length > 0 || settled.length > 0), ...olderNote());
      pruneKept(shown);
      return;
    }
    keepChildren(list, [
      ...groups.map((group) => {
        const section = keptGroups.get(group.label) ?? el("section", { class: "side-group" }, el("h3", { class: "side-label" }, group.label));
        keptGroups.set(group.label, section);
        keepChildren(section, [
          section.firstChild,
          ...group.chats.map((chat) => {
            if (selecting) {
              const pick = chatPick(chat, selected.has(chat.id), toggle, agentOf(chat));
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
    pruneKept(shown);
  }

  // What is kept of the rows of chats that are no longer listed goes (item 328).
  function pruneKept(shown) {
    const listed = new Set(shown.map((chat) => chat.id));
    for (const id of keptRows.keys()) {
      if (!listed.has(id)) {
        keptRows.delete(id);
      }
    }
  }

  // A section's label (item 619): the button that folds it, its name, how many chats it holds and, folded over a chat
  // nobody has read, a dot. Made once, so that it keeps the focus.
  function sectionLabel(id) {
    let kept = keptLabels.get(id);
    if (kept === undefined) {
      const words = id === "settled" ? SETTLED.label : CHANNEL_SECTIONS[id].label;
      const count = el("span", { class: "side-label-count" });
      const unread = el("span", { class: "side-label-dot", role: "img", "aria-label": "unread", hidden: true });
      kept = { count, unread, label: foldLabel("h3", false, () => toggleFold(id), `${words} `, unread, count) };
      keptLabels.set(id, kept);
    }
    return kept;
  }

  // One section of chats under a label that folds (item 619), as any chat's row is drawn (a chat at work stands under
  // Working, as every chat does): its section, or null while it holds none. A search shows what it finds in a section
  // folded, as Working does; choosing chats to delete needs them in sight.
  function paintSection(id, members, folds) {
    if (members.length === 0) {
      return null;
    }
    const folded = folds && !selecting && needle === "";
    const { count, unread, label } = sectionLabel(id);
    label.firstChild.setAttribute("aria-expanded", folded ? "false" : "true");
    setText(count, String(members.length));
    unread.hidden = !(folded && members.some((chat) => chat.unread === true));
    const section = keptSections.get(id) ?? el("section", { class: "side-group side-channel", "data-channel": id });
    keptSections.set(id, section);
    section.toggleAttribute("data-folded", folded);
    keepChildren(section, [
      label,
      ...(folded
        ? []
        : members.map((chat) => {
            if (selecting) {
              const pick = chatPick(chat, selected.has(chat.id), toggle, agentOf(chat));
              rowNodes.set(chat.id, pick);
              return pick;
            }
            return keptEntry(chat);
          })),
    ]);
    return section;
  }

  // The sections of the channels' chats, at the foot of the list (item 619): each has its label and its chats' rows. The
  // ids of those painted are returned.
  function paintChannels(channelChats) {
    const sections = [];
    for (const id of Object.keys(CHANNEL_SECTIONS)) {
      const section = paintSection(
        id,
        channelChats.filter((chat) => channelOf(chat) === id),
        isFolded(id),
      );
      if (section !== null) {
        sections.push(section);
      }
    }
    keepChildren(channelsNav, sections);
    return sections.map((section) => section.dataset.channel);
  }

  // The chats that rest past a week stand Settled in the dock under what works, folded as the owner left it.
  function paintSettled(settled) {
    const section = paintSection("settled", settled, isFolded("settled"));
    keepChildren(settledNav, section === null ? [] : [section]);
    settledNav.hidden = section === null;
    return section === null ? [] : ["settled"];
  }

  function pruneSections(ids) {
    for (const id of keptSections.keys()) {
      if (!ids.includes(id)) {
        keptSections.delete(id);
        keptLabels.delete(id);
      }
    }
  }

  // A chat's entry in the list as it was painted last, while what it shows is the same; else a new one (item 328).
  function keptEntry(chat) {
    const family = work.pinned.get(chat.id) ?? null;
    // What waits of it is in the key: a pinned chat whose step was answered, or that gained one, is drawn anew (the
    // pre-push review of item 836).
    const key = JSON.stringify([chat, family, waits.byChat.get(chat.id) ?? null, agentOf(chat).name]);
    const kept = keptRows.get(chat.id);
    if (kept !== undefined && kept.key === key) {
      rowNodes.set(chat.id, kept.link);
      return kept.node;
    }
    const node = chatEntry(chat, family);
    keptRows.set(chat.id, { key, node, link: rowNodes.get(chat.id) });
    return node;
  }

  // What waits and what works, in the dock at the foot of the list as rows (item 836): what waits first, in every search, so
  // that what the owner must answer is never out of sight; what works as a search finds it. Under Working's label, with
  // the count of both, which folds what works and nothing that waits.
  function paintActivity() {
    const found = needle === "" ? work.working : work.working.filter((entry) => entry.chat !== null && matchesQuery(entry.chat, needle));
    const entries = { waiting: waits.waiting.map(waitingEntry), working: found.map(workingEntry) };
    const groups = activity === null ? [] : ACTIVITY_SECTIONS.filter(({ key }) => entries[key].length > 0);
    activityNav.hidden = groups.length === 0;
    const folded = isFolded(DOCK_KEY) && needle === "";
    workingLabel.firstChild.setAttribute("aria-expanded", folded ? "false" : "true");
    setText(workingCount, String(entries.waiting.length + entries.working.length));
    activityNav.replaceChildren(
      workingLabel,
      ...groups
        .filter(({ key }) => !(folded && key === "working"))
        .map(({ key, label }) => el("section", { class: "side-group", "data-section": key, "aria-label": label }, entries[key])),
    );
  }

  // An entry of what waits: the chat it is of, or a row that is no listed chat's (item 836). A chat a search finds is
  // marked, so that the search's Enter and the arrows reach it while what waits stands in every search.
  function waitingEntry(entry) {
    if (entry.chat === null) {
      return waitingRow(entry.row, onNavigate, agentOfRow(entry.row));
    }
    const node = chatEntry(entry.chat, entry.family ?? null);
    node.toggleAttribute("data-found", needle !== "" && matchesQuery(entry.chat, needle));
    return node;
  }

  // An entry of Working: a chat at work, or a row that is no chat's, each wearing the faces of the runs it delegated.
  function workingEntry(entry) {
    return entry.chat === null ? workRow(entry.row, onNavigate, entry.children, agentOfRow(entry.row)) : chatEntry(entry.chat, entry);
  }

  // The agents' names by id, as `GET /agents` last gave them (`paintFaces`): each row's first line names the agent whose
  // chat it is, the badge its two letters; Dot's own name is the owner's for it (item 420).
  const agentNames = new Map();
  /** Whose a chat is, for its row's first line: the team it leads, the agent of its latest run, else Dot. */
  function agentOf(chat) {
    if (chat.team !== null && chat.team !== undefined && chat.actor_id === chat.team.id) {
      return agentLabel(chat.team.name);
    }
    return agentOfId(chat.actor_id ?? null);
  }
  // The rows already drawn take a name that came or changed in place: painting the lists again closes a menu a row opened.
  function relabel() {
    const say = (link, agent) => {
      link.querySelector(".side-badge")?.setAttribute("data-code", agent.code);
      setText(link.querySelector(".side-agent"), agent.name);
    };
    for (const chat of chats ?? []) {
      const link = rowNodes.get(chat.id);
      if (link !== undefined) {
        say(link, agentOf(chat));
      }
    }
    for (const node of activityNav.querySelectorAll("[data-actor]")) {
      say(node, agentOfId(node.dataset.actor === "" ? null : node.dataset.actor));
    }
  }
  function agentOfRow(row) {
    return agentOfId(row.actor_id ?? null);
  }
  function agentOfId(id) {
    return agentLabel(id === null || id === DOT_ID ? dotName() : (agentNames.get(id) ?? t("side.agent")));
  }

  // A chat's entry: its row, wearing while its latest run works (`family`, that run's row and the runs it delegated;
  // null at rest) the faces of those runs (items 202 and 507).
  function chatEntry(chat, family) {
    const link = chatLink(chat, onNavigate, agentOf(chat), family === null ? [] : family.children, waits.byChat.get(chat.id) ?? null);
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
        title: t("sidebar.more"),
        "aria-label": t("sidebar.moreFor", { title: clip(chat.title, 120) }),
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
    // On a phone a finger's hold opens the row's menu (item 1079), as a right-click does with a mouse.
    openOnHold(row, () => ({ label: chat.title, items: renaming === null ? chatActions(chat) : [], back: link }));
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
        ? { label: t("sidebar.pin"), icon: "pin", run: () => attempt(() => pinChat(chat, true)) }
        : { label: t("sidebar.unpin"), icon: "pinOff", run: () => attempt(() => pinChat(chat, false)) },
      readAction(chat),
      { label: t("sidebar.rename"), icon: "pencil", run: () => renameChat(chat) },
      { label: t("sidebar.copyLink"), icon: "link", run: () => attempt(() => copyLink(chat)) },
      "separator",
      // A chat above the list, with what waits or what works, is not in it, where the chats are chosen.
      aboveTheList(chat) ? null : { label: t("sidebar.select"), icon: "squareCheck", run: () => enterSelectMode(chat.id) },
      { label: t("sidebar.delete"), icon: "trash", danger: true, run: () => attempt(() => deleteChats([chat.id])) },
    ];
    return actions.filter((item) => item !== null);
  }

  // Mark read on an unread chat, Mark unread on a read one whose latest run is over (item 131); a daemon from before
  // item 131 sends no `unread`, and neither is offered.
  function readAction(chat) {
    if (chat.unread === true) {
      return { label: t("sidebar.markRead"), icon: "eye", run: () => attempt(() => markChat(chat, true)) };
    }
    if (chat.unread === false && ENDED.has(chat.latest_status)) {
      return { label: t("sidebar.markUnread"), icon: "eyeOff", run: () => attempt(() => markChat(chat, false)) };
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
      "aria-label": t("sidebar.chatName"),
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
    toast("ok", t("sidebar.linkCopied"));
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
      title: ids.length === 1 && !selecting ? t("sidebar.deleteThis") : tCount(ids.length, "sidebar.deleteOne", "sidebar.deleteMany"),
      message:
        ids.length === 1
          ? t("sidebar.itsTasksGo")
          : t("sidebar.theirTasksGo"),
      confirmLabel: t("sidebar.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    // The chats leave the list at the yes (the owner's ask of 2026-11-19: many chats took seconds to delete and the
    // page showed nothing meanwhile); a delete that fails brings them back with why.
    const leaving = new Set(ids);
    for (const id of ids) {
      deleting.add(id);
    }
    chats = chats.filter((chat) => !leaving.has(chat.id));
    if (selecting) {
      leaveSelectMode();
    } else {
      paintLists();
    }
    if (routeThreadId !== null && leaving.has(routeThreadId)) {
      goTo(homeHref);
    }
    let outcome;
    try {
      outcome = await request("DELETE", "/threads", { ids });
    } catch (error) {
      for (const id of ids) {
        deleting.delete(id);
      }
      if (!disposed) {
        await load();
      }
      if (error instanceof ApiError && error.status === 409) {
        toast("error", BUSY_MESSAGE);
        return;
      }
      throw error;
    }
    if (disposed) {
      return;
    }
    for (const id of ids) {
      deleting.delete(id);
    }
    toast("ok", tCount(outcome.deleted.length, "sidebar.deletedOne", "sidebar.deletedMany"));
    await load();
  }

  async function resolveThread(runId) {
    resolveTicket += 1;
    const ticket = resolveTicket;
    let detail;
    try {
      // The run's page reads it at the same moment: one request for both (item 1169).
      detail = await sharedGet(`/runs/${runId}`);
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

  // The chats whose delete was asked and has not answered yet (deleteChats).
  const deleting = new Set();
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
        readFailed("readFailed.sidebar", error);
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
      // A chat being deleted stays out of the list while its delete is under way, whatever a reload in between reads.
      chats = all.filter((chat) => !deleting.has(chat.id));
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
        readFailed("readFailed.chatList", error);
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
  // The agents are read when the page may not know them as they are: as it opens, once a run ended (a run can make
  // one), when a page of it changed one, when the stream comes back and when the page comes back into view.
  const reloadFacesSoon = debounce(() => {
    loadFaces();
  }, 250);
  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      reloadSoon();
    }
    // A run that starts may begin an agent's conversation (item 1233), and one that ends moves its face.
    if (event.kind === "run_started" || event.kind === "run_finished") {
      reloadFacesSoon();
    }
  });
  const stopStream = onStreamBack(() => {
    reloadSoon();
    reloadFacesSoon();
  });
  // The daemon tells of no change of a schedule: a page that made one says so, and the marks of the loops follow.
  window.addEventListener(SCHEDULES_CHANGED_EVENT, reloadSoon);
  // Nor of a team's (item 610): the chats with it are named and drawn as the team is now.
  window.addEventListener(TEAMS_CHANGED_EVENT, reloadSoon);
  // Nor of an agent's (item 835): the faces at the top are the agents as they are now.
  window.addEventListener(AGENTS_CHANGED_EVENT, reloadFacesSoon);
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
  channelsNav.addEventListener("keydown", walkRows);
  settledNav.addEventListener("keydown", walkRows);

  paintLists();
  load();
  const agentsRead = loadFaces();

  // The sidebar is one component, in the window beside the page and as the drawer over it on a phone (item 294): a head
  // with the title, the search row, the list and its dock, and the foot's four tools, the same on both.
  const body = el("div", { class: "side-body" }, el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list, channelsNav));
  // The dock under the list (the sidebar of the reference app): what works and waits under Working, then what rests past a week,
  // Settled. It keeps its place while the list scrolls.
  const dock = el("div", { class: "side-dock" }, activityNav, settledNav);
  // Settings has the owner's name for a screen reader, which it does not show (WCAG 2.5.3: the name holds what is shown).
  function paintOwner() {
    const owner = shownOwner().trim();
    settingsLink.setAttribute("aria-label", owner === "" ? t("sidebar.settings") : t("sidebar.settingsOf", { owner }));
  }
  paintOwner();
  const sideHead = el(
    "div",
    { class: "side-head", "data-tauri-drag-region": "" },
    close,
    el("span", { class: "side-title" }, dotName()),
  );
  function placeForWidth() {
    close.replaceChildren(icon(PHONE.matches ? "x" : "panelLeft"));
  }
  placeForWidth();
  PHONE.addEventListener("change", placeForWidth);
  // While the settings are open the sidebar is their nav in place of the chats and the pages (item 562); a phone keeps the
  // sidebar as it is, its settings being a list of tabs on a page of their own.
  const settingsNav = buildSettingsNav({ homeHref, onCollapse, onNavigate });
  const element = el(
    "aside",
    { class: "sidebar", id: "sidebar", "aria-label": "Sidebar" },
    sideHead,
    top,
    body,
    dock,
    foot,
    settingsNav.element,
  );
  closesOnSwipe(element, onCollapse);
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
  onAssistant((names) => {
    setText(element.querySelector(".side-title"), names.name);
    composeButton.title = t("sidebar.writeTo", { name: dotName() });
    composeButton.setAttribute("aria-label", t("sidebar.writeTo", { name: dotName() }));
    paintOwner();
    // Dot's own rows and its entry read the name again (item 1265): a rename shows at once.
    void loadFaces();
  });
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
      markAgents();
      mark(watchLink, tab === "watch");
      mark(settingsLink, tab === "settings");
      // Customize is reached from Connect apps (item 545), which stands for it while it is open.
      connectButton.toggleAttribute("data-current", tab === "customize");
      // The styles read data-current; a screen reader reads aria-current, as the links of the pages give it (item 559).
      mark(connectButton, tab === "customize");
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
      stopLifecycle();
      stopStream();
      window.removeEventListener(SCHEDULES_CHANGED_EVENT, reloadSoon);
      window.removeEventListener(TEAMS_CHANGED_EVENT, reloadSoon);
      window.removeEventListener(AGENTS_CHANGED_EVENT, reloadFacesSoon);
      document.removeEventListener("visibilitychange", onBackInView);
      window.removeEventListener("focus", onBackInView);
      window.removeEventListener("keydown", onEscape);
      PHONE.removeEventListener("change", placeForWidth);
      PHONE.removeEventListener("change", paintMode);
    },
  };
}
