// The sidebar: a new chat, a search over the chats, the ways to the agents, the activity and the campaigns, what waits
// and what works (folded or not, as the owner left them), the chats themselves (several of them can be deleted at once)
// and, in the foot, the way to the settings and the emergency stop (item 120). A row whose run works says Working and
// for how long (item 122). A chat's menu (item 130), after T3 Code's thread menu, pins it, names it, copies its link,
// starts choosing chats with it and deletes it; the pinned chats come first, under Pinned. A chat whose latest run
// ended after it was last seen is unread (item 131): an accent dot, until it is opened, or Mark read.

import { ApiError, api, request } from "./api.js";
import { age, clip, clipWords, copyText, dateTime, debounce, el, foldSearch, plural, workingFor } from "./dom.js";
import { failsafeButton } from "./failsafe.js";
import { icon } from "./icons.js";
import { actorFace } from "./actors.js";
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
const RELOAD_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided", "takeover", "handback"]);
// The sections above the chats: what waits for you and what works now (`GET /activity`), each hidden while empty.
const ACTIVITY_SECTIONS = [
  { key: "waiting", label: "Waiting for you" },
  { key: "working", label: "Working" },
];
const DAY_LABELS = ["Today", "Yesterday", "Previous 7 days", "Older"];
// A row of the activity sections shows this many words of its title at most, on one line (item 95).
const ROW_WORDS = 7;
const ROW_CHARS = 56;
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

/** What a row whose run works shows at its right, where its age sits at rest, as T3 Code's sidebar does (item 122): a
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

/** A chat's dot: the state of its latest run, in the accent while the chat is unread (item 131); a failed run keeps its
 * red dot. A daemon from before item 131 sends no `unread`, and its chats are read. */
function chatDot(chat) {
  return el("span", { class: `status-dot s-${chat.latest_status}${chat.unread === true ? " unread" : ""}` });
}

/** A chat as a link: it opens on the latest run a person started on it. Its last activity is at the right, in the
 * fewest characters (item 121), or, while its latest run works, Working and for how long (item 122). */
function chatLink(chat, onNavigate) {
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
    chatDot(chat),
    el("span", { class: "side-run-goal" }, title),
    WORKING.has(chat.latest_status)
      ? workingSlot(chat.latest_created_at)
      : el("span", { class: "side-age", "data-ts": String(chat.updated_at), title: dateTime(chat.updated_at) }, age(chat.updated_at)),
  );
}

/** One row of the activity sections: the state dot, the title and what it needs or where it works. A run that
 * delegated runs says how many; a delegated run (`child`) sits under its parent with its steps. A run of the Working
 * section says Working and for how long at its right (item 122); a schedule due there does not work yet. */
function activityRow(row, onNavigate, children, child) {
  const detail = child ? progressFacts(row) : [row.channel, row.detail].filter((part) => part !== "");
  const delegated = children === 0 ? [] : [children === 1 ? "1 delegated run" : `${children} delegated runs`];
  const facts = [...detail, ...delegated];
  const words = facts.join(", ");
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
      ? el("span", { class: `status-dot s-${row.section === "waiting" ? "blocked" : "running"}` })
      : actorFace(row.actor_id, row.section === "waiting" ? "waiting" : "working", null),
    el(
      "span",
      { class: "side-run-goal" },
      el("span", { class: "side-activity-title" }, clipWords(row.title, ROW_WORDS, ROW_CHARS)),
      facts.length === 0 ? null : el("small", { class: "side-activity-detail" }, facts.join(" · ")),
    ),
    row.section === "working" && row.run_id !== null && WORKING.has(row.status) ? workingSlot(row.since) : null,
  );
}

/** The rows of a section: each parent, then its delegated runs under it. */
function activityRows(rows, onNavigate) {
  return nestRows(rows).map(({ row, children }) => [
    activityRow(row, onNavigate, children.length, false),
    children.length === 0 ? null : el("div", { class: "side-children" }, children.map((kid) => activityRow(kid, onNavigate, 0, true))),
  ]);
}

/** A chat as a row to tick while chats are being chosen for deletion. */
function chatPick(chat, checked, onToggle) {
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
    chatDot(chat),
    el("span", { class: "side-run-goal" }, clip(chat.title, 120)),
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
  const list = el("div", { class: "side-runs" });
  const ageTicker = setInterval(() => {
    for (const node of list.querySelectorAll(".side-age")) {
      node.textContent = age(Number(node.dataset.ts));
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
  const close = el(
    "button",
    { class: "icon-btn", type: "button", title: "Hide sidebar", "aria-label": "Hide sidebar", onclick: onCollapse },
    icon("panelLeft"),
  );
  const selectButton = el("button", { class: "btn ghost sm", type: "button", hidden: true, onclick: () => enterSelectMode() }, "Select");
  let folds = readFolds(FOLDS_KEY);
  function toggleFold(key) {
    folds = { ...folds, [key]: folds[key] !== true };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    paintActivity();
    paintChats();
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
  const activityNav = el("nav", { class: "side-activity-nav", "aria-label": "Activity", hidden: true });
  const agentsLink = el("a", { class: "side-link", href: "#/agents", onclick: onNavigate }, icon("user"), el("span", null, "Agents"));
  const activityLink = el("a", { class: "side-link", href: "#/activity", onclick: onNavigate }, icon("bolt"), el("span", null, "Activity"));
  const campaignsLink = el("a", { class: "side-link", href: "#/campaigns", onclick: onNavigate }, icon("target"), el("span", null, "Campaigns"));
  const settingsLink = el("a", { class: "side-link", href: "#/settings", onclick: onNavigate }, icon("sliders"), el("span", null, "Settings"));

  function rows() {
    return [...list.querySelectorAll("a.side-run, input.side-check")];
  }

  function shownChats() {
    return chats === null ? [] : chats.filter((chat) => matchesQuery(chat, needle));
  }

  function markCurrent() {
    for (const [id, node] of rowNodes) {
      mark(node, id === routeThreadId);
    }
    const active = rowNodes.get(routeThreadId);
    if (active !== undefined) {
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
    // The repaint takes the rows away, so the focus goes back to the row that had it (a chat opened by a key).
    const focused = [...rowNodes].find(([, node]) => node === document.activeElement);
    paintChats();
    if (focused !== undefined) {
      rowNodes.get(focused[0])?.focus();
    }
    api.patch(`/threads/${open.id}`, { seen: true }).catch((error) => console.warn("the open chat could not be marked seen", error));
  }

  function onBackInView() {
    keptUnread = null;
    seeOpenChat();
  }

  function paintBar() {
    const shown = shownChats();
    const allChosen = shown.length > 0 && shown.every((chat) => selected.has(chat.id));
    count.textContent = selected.size === 0 ? "Choose chats to delete" : `${plural(selected.size, "chat", "chats")} selected`;
    allButton.textContent = allChosen ? "Clear" : "Select all";
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

  function paintChats() {
    if (renaming !== null) {
      return;
    }
    // A chat's menu acts on the rows it was opened from, which go now.
    closeMenu();
    rowNodes.clear();
    selectButton.hidden = selecting || chats === null || chats.length === 0;
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
                paintChats();
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
      const text = needle === "" ? "No chats yet. Start one with New chat." : `No chat matches “${search.value.trim()}”.`;
      list.replaceChildren(el("div", { class: "side-note" }, text), ...olderNote());
      return;
    }
    list.replaceChildren(
      ...groupChats(shown, new Date()).map((group) =>
        el(
          "section",
          { class: "side-group" },
          el("h3", { class: "side-label" }, group.label),
          group.chats.map((chat) => {
            if (selecting) {
              const pick = chatPick(chat, selected.has(chat.id), toggle);
              rowNodes.set(chat.id, pick);
              return pick;
            }
            const link = chatLink(chat, onNavigate);
            rowNodes.set(chat.id, link);
            return chatRow(chat, link);
          }),
        ),
      ),
      ...olderNote(),
    );
    markCurrent();
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

  function chatActions(chat) {
    return [
      chat.pinned_at === null
        ? { label: "Pin", icon: "pin", run: () => attempt(() => pinChat(chat, true)) }
        : { label: "Unpin", icon: "pinOff", run: () => attempt(() => pinChat(chat, false)) },
      readAction(chat),
      { label: "Rename", icon: "pencil", run: () => renameChat(chat) },
      { label: "Copy link", icon: "link", run: () => attempt(() => copyLink(chat)) },
      "separator",
      { label: "Select", icon: "squareCheck", run: () => enterSelectMode(chat.id) },
      { label: "Delete", icon: "trash", danger: true, run: () => attempt(() => deleteChats([chat.id])) },
    ].filter((item) => item !== null);
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

  async function markChat(chat, seen) {
    if (chat.id === routeThreadId) {
      keptUnread = seen ? null : chat.id;
    }
    await api.patch(`/threads/${chat.id}`, { seen });
    await load();
    rowNodes.get(chat.id)?.focus();
  }

  async function pinChat(chat, pinned) {
    await api.patch(`/threads/${chat.id}`, { pinned });
    await load();
    rowNodes.get(chat.id)?.focus();
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
        paintChats();
        return;
      }
      chats = chats.map((one) => (one.id === chat.id ? { ...one, title } : one));
      paintChats();
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
    paintChats();
    paintBar();
  }

  // `chosen` is a chat ticked at the start, when its menu began the choosing; the focus goes to its box then.
  function enterSelectMode(chosen) {
    selecting = true;
    selected.clear();
    if (chosen !== undefined) {
      selected.add(chosen);
    }
    paintChats();
    paintBar();
    const first = chosen === undefined ? rows()[0] : rowNodes.get(chosen)?.querySelector("input.side-check");
    (first === undefined ? cancelButton : first).focus();
  }

  function leaveSelectMode() {
    selecting = false;
    selected.clear();
    paintChats();
    paintBar();
    if (!selectButton.hidden) {
      selectButton.focus();
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
      paintChats();
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

  function paintActivity() {
    const groups = activity === null ? [] : ACTIVITY_SECTIONS.filter(({ key }) => activity[key].length > 0);
    activityNav.hidden = groups.length === 0;
    activityNav.replaceChildren(
      ...groups.map(({ key, label }) => {
        const folded = folds[key] === true;
        return el(
          "section",
          { class: "side-group", "data-section": key, "data-folded": folded ? "true" : null },
          foldLabel("h3", folded, () => toggleFold(key), `${label} `, el("span", { class: "side-label-count" }, String(activity[key].length))),
          folded ? null : activityRows(activity[key], onNavigate),
        );
      }),
    );
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
    paintActivity();
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
    paintChats();
    paintBar();
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
    paintChats();
    paintBar();
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (search.value !== "") {
        event.preventDefault();
        search.value = "";
        needle = "";
        paintChats();
        paintBar();
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
  list.addEventListener("keydown", (event) => {
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
  });

  paintChats();
  paintBar();
  load();

  const element = el(
    "aside",
    { class: "sidebar", id: "sidebar", "aria-label": "Sidebar" },
    el(
      "div",
      { class: "side-head", "data-tauri-drag-region": "" },
      close,
    ),
    el(
      "div",
      { class: "side-body" },
      el("label", { class: "side-search" }, icon("search"), search, el("kbd", { "aria-hidden": "true" }, "⌘K")),
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
      el("nav", { class: "side-links", "aria-label": "Pages" }, agentsLink, activityLink, campaignsLink),
      activityNav,
      el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list),
    ),
    el("div", { class: "side-foot" }, settingsLink, failsafeButton()),
  );
  const workTicker = setInterval(() => {
    for (const node of element.querySelectorAll(".side-working-time")) {
      node.textContent = workingFor(Number(node.dataset.since));
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
      mark(activityLink, tab === "activity");
      mark(campaignsLink, tab === "campaigns");
      mark(settingsLink, tab === "settings");
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
    },
  };
}
