// The sidebar: a new chat, a search over the chats, the chats themselves (several of them can be deleted at once) and the
// way to the settings.

import { ApiError, api, request } from "./api.js";
import { clip, debounce, el, plural } from "./dom.js";
import { brandMark, icon } from "./icons.js";
import { requestCompose } from "./shortcuts.js";
import { onLifecycle } from "./state.js";
import { attempt, confirmDialog, toast } from "./ui.js";

const LIST_LIMIT = 200;
// The moments a run changes status; a run that waits for an approval is "blocked" until it is answered.
const RELOAD_KINDS = new Set(["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided"]);
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
  return needle === "" || chat.title.toLowerCase().includes(needle);
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
      list.replaceChildren(el("div", { class: "side-note" }, text));
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

  async function load() {
    try {
      const all = await api.get(`/threads?limit=${LIST_LIMIT}`);
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
    needle = search.value.trim().toLowerCase();
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
      el("nav", { class: "side-recent", "aria-label": "Chats" }, head, bar, list),
    ),
    el("div", { class: "side-foot" }, settingsLink),
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
