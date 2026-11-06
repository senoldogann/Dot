// The sidebar while the settings are open (item 562): Back to chats and the settings' groups of rows (item 999, as the
// phone's sheet, after seven sections of tabs, item 845), a row one page, with what some hold (how many notes Dot keeps,
// how many entries the Vault holds, whether a phone reaches Dot). sidebar.js
// shows it in the place of the chats and its pages (a Mac's window; a phone has the list of tabs for a page of its own,
// views/settings.js). The daemon's state is the page's (shell.js): the connection, and the emergency stop's banner.

import { api, query } from "./api.js";
import { el, setText } from "./dom.js";
import { icon } from "./icons.js";
import { LAST_CHAT_KEY, readPref } from "./prefs.js";
import { SETTINGS_GROUPS, TABS, tabAt } from "./settingstabs.js";

// The chat the owner was on last, which main.js keeps; Back to chats leads there.
// What the phone needs asked of Tailscale is read again no sooner than this: a tab's visit should not ask it each time.
const PHONE_FRESH_MS = 60_000;
// The rows whose count or state the nav shows at their right (readValues); the main model's name, which ran long beside
// Models, it shows no more (item 852), and the theme is the phone's sheet's alone.
const NAV_VALUES = new Set(["memory", "vault", "phone"]);

/**
 * Build the nav. `homeHref` is where Back to chats leads when no chat was open before. `onNavigate` runs after a link is
 * followed (the drawer closes), `onCollapse` hides the sidebar. Returns the element, `setRoute()` (the address is read
 * again), `focusClose()` and `dispose()`.
 */
export function buildSettingsNav({ homeHref, onNavigate, onCollapse }) {
  const hide = el("button", { class: "icon-btn", type: "button", title: "Hide sidebar", "aria-label": "Hide sidebar", onclick: onCollapse }, icon("panelLeft"));
  const back = el("a", { class: "settings-back", href: homeHref, onclick: onNavigate }, icon("arrowLeft"), el("span", null, "Back to chats"));
  const values = new Map([...NAV_VALUES].map((id) => [id, el("span", { class: "settings-nav-value" })]));
  // A row of the tab it names, by its id (the open one is marked), or of the page it names (Connect apps): its mark
  // and its name, without the phone's line under it.
  const links = new Map();
  const groups = SETTINGS_GROUPS.map((group) =>
    el(
      "section",
      { class: "settings-nav-group", "aria-label": group.title ?? null },
      group.title === null ? null : el("h3", { class: "settings-nav-group-title" }, group.title),
      group.rows.map((row) => {
        const tab = row.tab === undefined ? null : TABS.find((candidate) => candidate.id === row.tab);
        const link = el(
          "a",
          { class: "settings-nav-link", href: tab === null ? row.href : `#/settings/${tab.id}`, onclick: onNavigate },
          icon(tab === null ? row.icon : tab.icon),
          el("span", { class: "settings-nav-label" }, tab === null ? row.label : tab.label),
          row.value === undefined ? null : (values.get(row.value) ?? null),
        );
        if (tab !== null) {
          links.set(tab.id, link);
        }
        return link;
      }),
    ),
  );
  // The first row holds the button that hides the sidebar where the chats' head holds it (items 113 and 134), beside
  // the traffic lights of the desktop shell (item 88); Back to chats stands at the foot of the nav, under the groups
  // (item 852, the owner's ask: it stood at the top, item 577).
  const element = el(
    "nav",
    { class: "settings-nav", "aria-label": "Settings" },
    el("div", { class: "settings-nav-top", "data-tauri-drag-region": "" }, hide),
    el("h2", { class: "settings-nav-title" }, "Settings"),
    el("div", { class: "settings-nav-groups" }, groups),
    el("div", { class: "settings-nav-foot" }, back),
  );

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
    const [notes, vault, phone] = await Promise.allSettled([
      api.get(`/memories${query({ limit: 1, offset: 0 })}`),
      api.get("/vault/counts"),
      Date.now() - phoneReadAt < PHONE_FRESH_MS ? Promise.resolve(null) : api.get("/phone"),
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
    if (phone.status === "fulfilled" && phone.value !== null) {
      phoneReadAt = Date.now();
      const reached = phone.value.tailnet.reached;
      values.get("phone").replaceChildren(...(reached ? [el("span", { class: "settings-nav-dot", title: "A phone reaches Dot" })] : []));
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
    },
  };
}
