// The sidebar while the settings are open (item 562): Back to chats, the tabs in their groups with what each holds (the
// main model, how many providers are signed in, how many notes Dot keeps, whether a phone reaches Dot), and at the foot
// whether the daemon is running. sidebar.js shows it in the place of the chats and its pages (a Mac's window; a phone has
// the list of tabs for a page of its own, views/settings.js).

import { api, query } from "./api.js";
import { duration, el, modelName, setText } from "./dom.js";
import { icon } from "./icons.js";
import { readPref } from "./prefs.js";
import { GROUPS, TABS, tabAt } from "./settingstabs.js";
import { getSnapshot, onChange } from "./state.js";

// The chat the owner was on last, which main.js keeps; Back to chats leads there.
const LAST_CHAT_KEY = "dot.lastChat";
const UPTIME_REFRESH_MS = 30_000;
// What the phone needs asked of Tailscale is read again no sooner than this: a tab's visit should not ask it each time.
const PHONE_FRESH_MS = 60_000;

/**
 * Build the nav. `homeHref` is where Back to chats leads when no chat was open before. `onNavigate` runs after a link is
 * followed (the drawer closes), `onCollapse` hides the sidebar. Returns the element, `setRoute()` (the address is read
 * again), `focusClose()` and `dispose()`.
 */
export function buildSettingsNav({ homeHref, onNavigate, onCollapse }) {
  const hide = el("button", { class: "icon-btn", type: "button", title: "Hide sidebar", "aria-label": "Hide sidebar", onclick: onCollapse }, icon("panelLeft"));
  const back = el("a", { class: "settings-back", href: homeHref, onclick: onNavigate }, icon("arrowLeft"), el("span", null, "Back to chats"));
  const values = new Map(TABS.map((tab) => [tab.id, el("span", { class: "settings-nav-value" })]));
  const links = new Map(
    TABS.map((tab) => [
      tab.id,
      el("a", { class: "settings-nav-link", href: `#/settings/${tab.id}`, onclick: onNavigate }, icon(tab.icon), el("span", { class: "settings-nav-label" }, tab.label), values.get(tab.id)),
    ]),
  );
  const groups = GROUPS.map((group) =>
    el(
      "section",
      { class: "settings-nav-group" },
      el("h3", { class: "settings-nav-group-title" }, group.title),
      group.tabs.map((id) => links.get(id)),
    ),
  );
  const statusDot = el("span", { class: "settings-status-dot", "aria-hidden": "true" });
  const statusTitle = el("span", { class: "settings-status-title" });
  const statusLine = el("span", { class: "settings-status-line" });
  const status = el("div", { class: "settings-status", role: "status" }, statusDot, el("div", { class: "settings-status-text" }, statusTitle, statusLine));
  const element = el(
    "nav",
    { class: "settings-nav", "aria-label": "Settings" },
    el("div", { class: "settings-nav-top" }, back, hide),
    el("h2", { class: "settings-nav-title" }, "Settings"),
    el("div", { class: "settings-nav-groups" }, groups),
    status,
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

  function paintStatus(snapshot) {
    const daemon = snapshot.status;
    if (daemon === null || snapshot.stream !== "live") {
      statusDot.dataset.state = "wait";
      setText(statusTitle, snapshot.stream === "live" ? "Reading Dot" : "Dot is not answering");
      setText(statusLine, snapshot.stream === "live" ? "" : "Reconnecting to the daemon");
      return;
    }
    if (daemon.failsafe_engaged) {
      statusDot.dataset.state = "bad";
      setText(statusTitle, "Emergency stop is on");
      setText(statusLine, "Desktop control is blocked");
      return;
    }
    statusDot.dataset.state = "ok";
    setText(statusTitle, "Dot is running");
    setText(statusLine, `Daemon up ${duration(Math.max(0, Date.now() / 1000 - daemon.started_at))}`);
  }

  function paintModel(snapshot) {
    setText(values.get("models"), snapshot.status === null ? "" : modelName(snapshot.status.models.main));
    paintStatus(snapshot);
  }

  /** What the tabs hold, read from the daemon each time the nav is shown or the tab changes; one that cannot be read leaves its place empty. */
  async function readValues() {
    const [keys, notes, phone] = await Promise.allSettled([
      api.get("/keys"),
      api.get(`/memories${query({ limit: 1, offset: 0 })}`),
      Date.now() - phoneReadAt < PHONE_FRESH_MS ? Promise.resolve(null) : api.get("/phone"),
    ]);
    if (disposed) {
      return;
    }
    if (keys.status === "fulfilled") {
      const signedIn = keys.value.keys.filter((key) => key.source !== null).length;
      setText(values.get("keys"), signedIn === 0 ? "" : String(signedIn));
    }
    if (notes.status === "fulfilled") {
      setText(values.get("memory"), notes.value.total === 0 ? "" : String(notes.value.total));
    }
    if (phone.status === "fulfilled" && phone.value !== null) {
      phoneReadAt = Date.now();
      const reached = phone.value.tailnet.reached;
      values.get("phone").replaceChildren(...(reached ? [el("span", { class: "settings-nav-dot", title: "A phone reaches Dot" })] : []));
    }
  }

  paintCurrent();
  paintModel(getSnapshot());
  const stopChange = onChange(paintModel);
  const ticker = setInterval(() => paintStatus(getSnapshot()), UPTIME_REFRESH_MS);

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
      stopChange();
      clearInterval(ticker);
    },
  };
}
