// The sidebar: a new chat, a search over the chats, the chats themselves (several of them can be deleted at once) and the
// way to the settings.

import { ApiError, api, request } from "./api.js";
import { clip, debounce, el, foldSearch, plural } from "./dom.js";
import { brandMark, icon } from "./icons.js";
import { nestRows, progressWords } from "./nesting.js";
import { requestCompose } from "./shortcuts.js";
import { onLifecycle } from "./state.js";
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
// What a status dot means, for screen readers; a chat whose latest run is done or cancelled shows no dot and needs no word.
const STATUS_WORD = { queued: "queued", running: "running", blocked: "waiting for you", failed: "failed" };
const BUSY_MESSAGE = "A chat with a run under way cannot be deleted. Wait for the run to end, or cancel it, and try again.";

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

function matchesQuery(chat, needle) {
  return needle === "" || foldSearch(chat.title).includes(needle);
}

function mark(node, current) {
  if (current) {
    node.setAttribute("aria-current", "page");
  } else {
    node.removeAttribute("aria-current");
  }
}

/** A chat as a link: it opens on the latest run a person started on it. */
function chatLink(chat, onNavigate) {
  const word = STATUS_WORD[chat.latest_status];
  const title = clip(chat.title, 120);
  return el(
    "a",
    {
      class: "side-run",
      href: `#/runs/${chat.latest_run_id}`,
      title: clip(chat.title, 240),
      "aria-label": word === undefined ? null : `${title} (${word})`,
      onclick: onNavigate,
    },
    el("span", { class: `status-dot s-${chat.latest_status}` }),
    el("span", { class: "side-run-goal" }, title),
  );
}

/** One row of the activity sections: the state dot, the title and what it needs or where it works. A run that
 * delegated runs says how many; a delegated run (`child`) sits under its parent with its steps. */
function activityRow(row, onNavigate, children, child) {
  const detail = child ? progressWords(row) : [row.channel, row.detail].filter((part) => part !== "").join(" · ");
  const delegated = children === 0 ? "" : children === 1 ? "1 delegated run" : `${children} delegated runs`;
  const words = [detail, delegated].filter((part) => part !== "").join(" · ");
  return el(
    "a",
    {
      class: child ? "side-run side-activity side-child" : "side-run side-activity",
      href: row.href,
      title: clip(row.title, 240),
      "aria-label": words === "" ? row.title : `${row.title} (${words})`,
      onclick: onNavigate,
    },
    el("span", { class: `status-dot s-${row.section === "waiting" ? "blocked" : "running"}` }),
    el("span", { class: "side-run-goal" }, clip(row.title, 120), words === "" ? null : el("small", { class: "side-activity-detail" }, words)),
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
    el("span", { class: `status-dot s-${chat.latest_status}` }),
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
  let selecting = false;
  let disposed = false;
  const selected = new Set();
  const rowNodes = new Map();
  const list = el("div", { class: "side-runs" });
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
  const head = el("div", { class: "side-list-head" }, el("h2", { class: "side-label" }, "Chats"), selectButton);
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
    rowNodes.clear();
    selectButton.hidden = selecting || chats === null || chats.length === 0;
    head.hidden = selecting;
    bar.hidden = !selecting;
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
      ...groupByDay(shown, new Date()).map((group) =>
        el(
          "section",
          { class: "side-group" },
          el("h3", { class: "side-label" }, group.label),
          group.chats.map((chat) => {
            const node = selecting ? chatPick(chat, selected.has(chat.id), toggle) : chatLink(chat, onNavigate);
            rowNodes.set(chat.id, node);
            return node;
          }),
        ),
      ),
      ...olderNote(),
    );
    markCurrent();
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

  function enterSelectMode() {
    selecting = true;
    selected.clear();
    paintChats();
    paintBar();
    const first = rows()[0];
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
    const ids = [...selected];
    if (ids.length === 0) {
      return;
    }
    const confirmed = await confirmDialog({
      title: `Delete ${plural(ids.length, "chat", "chats")}?`,
      message: "Their runs go with them, with every step, event and approval of those. This cannot be undone.",
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
    leaveSelectMode();
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
      return;
    }
    resolveThread(routeRunId);
  }

  function paintActivity() {
    const groups = activity === null ? [] : ACTIVITY_SECTIONS.filter(({ key }) => activity[key].length > 0);
    activityNav.hidden = groups.length === 0;
    activityNav.replaceChildren(
      ...groups.map(({ key, label }) =>
        el(
          "section",
          { class: "side-group", "data-section": key },
          el("h3", { class: "side-label" }, `${label} `, el("span", { class: "side-label-count" }, String(activity[key].length))),
          activityRows(activity[key], onNavigate),
        ),
      ),
    );
  }

  async function loadActivity() {
    try {
      const view = await api.get("/activity");
      if (disposed) {
        return;
      }
      activity = view;
    } catch (error) {
      console.warn("the activity of the sidebar could not be refreshed", error);
      return;
    }
    paintActivity();
  }

  async function load() {
    loadActivity();
    try {
      const all = await api.get(`/threads?limit=${listLimit}`);
      if (disposed) {
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
      if (disposed) {
        return;
      }
      if (chats !== null) {
        console.warn("the chat list of the sidebar could not be refreshed", error);
        return;
      }
      failure = error;
    }
    paintChats();
    paintBar();
    locateRoute();
  }

  const reloadSoon = debounce(() => {
    load();
  }, 250);
  const stopLifecycle = onLifecycle((event) => {
    if (RELOAD_KINDS.has(event.kind)) {
      reloadSoon();
    }
  });

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
      { class: "side-head" },
      el("a", { class: "brand", href: homeHref, "aria-label": "Dot", onclick: onNavigate }, brandMark(), el("span", { class: "brand-name" }, "Dot")),
      close,
    ),
    el(
      "div",
      { class: "side-body" },
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
      el("label", { class: "side-search" }, icon("search"), search, el("kbd", { "aria-hidden": "true" }, "⌘K")),
      activityNav,
      el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list),
    ),
    el("div", { class: "side-foot" }, activityLink, campaignsLink, settingsLink),
  );

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
      mark(activityLink, tab === "activity");
      mark(campaignsLink, tab === "campaigns");
      mark(settingsLink, tab === "settings");
      routeRunId = runId;
      routeThreadId = null;
      markCurrent();
      locateRoute();
    },
    dispose() {
      disposed = true;
      stopLifecycle();
      window.removeEventListener("keydown", onEscape);
    },
  };
}
