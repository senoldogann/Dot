// The sidebar while the settings are open (item 562): a search of the settings (`/` takes the focus, item 1336), the
// settings' groups of rows (item 999, as the phone's sheet, after seven sections of tabs, item 845), a row one page with a
// small mark, with what some hold (how many notes Dot keeps, how many entries the Vault holds, how many schedules are on,
// whether a phone reaches Dot), and Back to chats at the foot. sidebar.js
// shows it in the place of the chats and its pages (a Mac's window; a phone has the list of tabs for a page of its own,
// views/settings.js). The daemon's state is the page's (shell.js): the connection, and the emergency stop's banner.

import { api, query } from "./api.js";
import { el, setText } from "./dom.js";
import { icon } from "./icons.js";
import { LAST_CHAT_KEY, readPref } from "./prefs.js";
import { matchesSearch, SETTINGS_GROUPS, settingsHref, tabAt, tabOf } from "./settingstabs.js";
import { t } from "./i18n.js";

// The chat the owner was on last, which main.js keeps; Back to chats leads there.
// What the phone needs asked of Tailscale is read again no sooner than this: a tab's visit should not ask it each time.
const PHONE_FRESH_MS = 60_000;
// The rows whose count or state the nav shows at their right (readValues); the main model's name, which ran long beside
// Models, it shows no more (item 852), and the theme is the phone's sheet's alone.
const NAV_VALUES = new Set(["memory", "vault", "phone", "schedules"]);

/**
 * Build the nav. `homeHref` is where Back to chats leads when no chat was open before. `onNavigate` runs after a link is
 * followed (the drawer closes), `onCollapse` hides the sidebar. Returns the element, `setRoute()` (the address is read
 * again), `focusClose()` and `dispose()`.
 */
export function buildSettingsNav({ homeHref, onNavigate, onCollapse }) {
  const hide = el("button", { class: "icon-btn", type: "button", title: t("settingsnav.hideSidebar"), "aria-label": t("settingsnav.hideSidebar"), onclick: onCollapse }, icon("panelLeft"));
  const back = el("a", { class: "settings-back", href: homeHref, onclick: onNavigate }, icon("arrowLeft"), el("span", null, t("settingsnav.backToChats")));
  const values = new Map([...NAV_VALUES].map((id) => [id, el("span", { class: "snav-value" })]));
  // A row of the page it names, by its id (the open one is marked): its small mark and its name.
  const links = new Map();
  const entries = [];
  const groups = SETTINGS_GROUPS.map((group) => {
    const section = el(
      "section",
      { class: "snav-group", "aria-label": group.title ?? null },
      group.title === null ? null : el("h3", { class: "snav-group-title" }, group.title),
      group.rows.map((row) => {
        const tab = tabOf(row.tab);
        const link = el(
          "a",
          { class: "snav-link", href: settingsHref(tab.id), onclick: onNavigate },
          icon(tab.icon),
          el("span", { class: "snav-label" }, tab.label),
          row.value === undefined ? null : (values.get(row.value) ?? null),
        );
        links.set(tab.id, link);
        entries.push({ tab, line: row.line, link });
        return link;
      }),
    );
    return { section, rows: group.rows.map((row) => links.get(row.tab)) };
  });
  const search = el("input", {
    class: "snav-input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: t("settingsnav.search"),
    "aria-label": t("settingsnav.searchSettings"),
  });
  const empty = el("p", { class: "snav-empty", hidden: true }, t("settingsnav.noSettingMatches"));
  const element = el(
    "nav",
    { class: "settings-nav", "aria-label": t("settingsnav.settings") },
    el("div", { class: "settings-nav-top", "data-tauri-drag-region": "" }, hide),
    el("label", { class: "snav-search" }, icon("search"), search, el("kbd", { class: "snav-key", "aria-hidden": "true" }, "/")),
    el("div", { class: "snav-groups" }, groups.map((group) => group.section), empty),
    el("div", { class: "settings-nav-foot" }, back),
  );

  /** Show the rows that match `search` (all of them when it is empty) and the groups that still hold one. */
  function filter() {
    const typed = search.value.trim();
    let shown = 0;
    for (const entry of entries) {
      const match = typed === "" || matchesSearch(entry.tab, entry.line, typed);
      entry.link.hidden = !match;
      shown += match ? 1 : 0;
    }
    for (const group of groups) {
      group.section.hidden = group.rows.every((link) => link.hidden);
    }
    empty.hidden = shown !== 0;
  }
  search.addEventListener("input", filter);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && search.value !== "") {
      event.preventDefault();
      search.value = "";
      filter();
    } else if (event.key === "Enter") {
      const first = entries.find((entry) => !entry.link.hidden);
      if (first !== undefined) {
        event.preventDefault();
        location.hash = first.link.getAttribute("href");
        search.value = "";
        filter();
        search.blur();
        onNavigate?.();
      }
    }
  });
  // `/` takes the focus while the settings are open and no box has it.
  function onSlash(event) {
    const typing = event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]") !== null;
    if (event.key !== "/" || typing || event.metaKey || event.ctrlKey || event.altKey || element.closest("#sidebar")?.dataset.mode !== "settings") {
      return;
    }
    event.preventDefault();
    search.focus();
    search.select();
  }
  document.addEventListener("keydown", onSlash);

  let disposed = false;
  let phoneReadAt = 0;

  function paintCurrent() {
    const current = tabAt(location.hash);
    for (const [id, link] of links) {
      if (id === current) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    }
  }

  /** What the tabs hold, read from the daemon each time the nav is shown or the tab changes; one that cannot be read leaves its place empty. */
  async function readValues() {
    const [notes, vault, phone, schedules] = await Promise.allSettled([
      api.get(`/memories${query({ limit: 1, offset: 0 })}`),
      api.get("/vault/counts"),
      Date.now() - phoneReadAt < PHONE_FRESH_MS ? Promise.resolve(null) : api.get("/phone"),
      api.get("/schedules"),
    ]);
    if (disposed) {
      return;
    }
    if (notes.status === "fulfilled") {
      setText(values.get("memory"), notes.value.total === 0 ? "" : String(notes.value.total));
    }
    if (vault.status === "fulfilled") {
      const held = vault.value.login + vault.value.card + vault.value.contact;
      setText(values.get("vault"), held === 0 ? "" : String(held));
    }
    if (schedules.status === "fulfilled") {
      const on = schedules.value.filter((view) => view.record.enabled).length;
      setText(values.get("schedules"), on === 0 ? "" : String(on));
    }
    if (phone.status === "fulfilled" && phone.value !== null) {
      phoneReadAt = Date.now();
      const reached = phone.value.tailnet.reached;
      values.get("phone").replaceChildren(...(reached ? [el("span", { class: "snav-dot", title: t("settingsnav.aPhoneReachesDot") })] : []));
    }
  }

  paintCurrent();

  return {
    element,
    /** The address changed (or the settings were opened): mark the open tab, point Back at the chat last open and read the tabs' values. */
    setRoute() {
      paintCurrent();
      const last = readPref(LAST_CHAT_KEY);
      back.href = last?.startsWith("/runs") ? `#${last}` : homeHref;
      readValues();
    },
    focusClose() {
      hide.focus();
    },
    dispose() {
      disposed = true;
      document.removeEventListener("keydown", onSlash);
    },
  };
}
