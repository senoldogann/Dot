// The frame around every page: the sidebar on the left (a drawer on narrow windows) and, above the page, the live
// indicators of the daemon and the emergency stop.

import { el } from "./dom.js";
import { engageFailsafe, releaseFailsafe } from "./failsafe.js";
import { brandMark, icon } from "./icons.js";
import { setSidebarOpen, sidebarOpen } from "./layout.js";
import { installShortcuts, requestCompose } from "./shortcuts.js";
import { buildSidebar } from "./sidebar.js";
import { attempt } from "./ui.js";

const NARROW = "(max-width: 859.98px)";
const STREAM_LABEL = { connecting: "Connecting", live: "Live", reconnecting: "Reconnecting" };

function buildIndicators() {
  const waitingCount = el("span");
  const waiting = el(
    "a",
    { class: "pill warn", href: "#/runs", hidden: true, title: "Approvals that wait for you" },
    icon("bell"),
    waitingCount,
    el("span", { class: "label" }, "waiting"),
  );
  const runningCount = el("span");
  const running = el(
    "span",
    { class: "pill run", hidden: true, title: "Runs in progress" },
    el("span", { class: "live-dot live" }),
    runningCount,
    el("span", { class: "label" }, "running"),
  );
  const dot = el("span", { class: "live-dot connecting" });
  const connectionLabel = el("span", { class: "label" }, STREAM_LABEL.connecting);
  const connection = el("span", { class: "pill" }, dot, connectionLabel);
  // The emergency stop is a red square at the top right of every page; its words are in its tooltip.
  const stop = el(
    "button",
    {
      class: "btn danger sm failsafe",
      type: "button",
      title: "Emergency stop: cancel every run and block desktop control",
      "aria-label": "Emergency stop",
      onclick: () => attempt(engageFailsafe),
    },
    icon("stop"),
    el("span", { class: "label" }, "Emergency stop"),
  );
  const banner = el(
    "div",
    { class: "banner", hidden: true, role: "alert" },
    icon("alert"),
    el("span", { class: "grow" }, "Emergency stop is engaged. Desktop control is blocked until you release it."),
    el("button", { class: "btn sm", type: "button", onclick: () => attempt(releaseFailsafe) }, "Release"),
  );
  return {
    pills: [waiting, running, connection, stop],
    banner,
    paint(snapshot) {
      const active = snapshot.status === null ? 0 : snapshot.status.active_runs.length;
      runningCount.textContent = String(active);
      running.hidden = active === 0;
      waitingCount.textContent = String(snapshot.approvals.length);
      waiting.hidden = snapshot.approvals.length === 0;
      dot.className = `live-dot ${snapshot.stream}`;
      connectionLabel.textContent = STREAM_LABEL[snapshot.stream];
      // A live connection is a green dot; the word only shows while the connection is being made or lost.
      connection.classList.toggle("quiet", snapshot.stream === "live");
      connection.title = `Connection to the daemon: ${STREAM_LABEL[snapshot.stream]}`;
      banner.hidden = snapshot.status === null || !snapshot.status.failsafe_engaged;
    },
  };
}

/**
 * Build the frame. `homeRoute` is where "New chat" leads. Returns the element, the `main` element the pages draw into,
 * `setRoute({ tab, runId, layout })`, `paint(snapshot)` and `dispose()`.
 */
export function buildShell({ homeRoute }) {
  const homeHref = `#${homeRoute}`;
  const narrowQuery = window.matchMedia(NARROW);
  let narrow = narrowQuery.matches;
  let docked = sidebarOpen();
  let drawer = false;

  const sidebar = buildSidebar({
    homeHref,
    onCollapse: closeToOpener,
    onNavigate: closeAfterNavigation,
  });
  const indicators = buildIndicators();
  const menu = el(
    "button",
    {
      class: "icon-btn",
      type: "button",
      title: "Show sidebar",
      "aria-label": "Show sidebar",
      "aria-controls": "sidebar",
      "aria-expanded": "false",
      onclick: () => {
        setOpen(true);
        sidebar.focusClose();
      },
    },
    icon("panelLeft"),
  );
  const opener = el(
    "div",
    { class: "stagebar-left" },
    menu,
    el("a", { class: "brand", href: homeHref, "aria-label": "Dot" }, brandMark(), el("span", { class: "brand-name" }, "Dot")),
  );
  const main = el("main", { class: "page", tabindex: "-1" });
  const stage = el(
    "div",
    { class: "stage" },
    el("header", { class: "stagebar" }, el("div", { class: "stagebar-row" }, opener, el("div", { class: "stagebar-right" }, indicators.pills)), indicators.banner),
    main,
  );
  const scrim = el("div", { class: "scrim", onclick: closeToOpener });
  const frame = el("div", { class: "shell" }, sidebar.element, scrim, stage);

  function isOpen() {
    return narrow ? drawer : docked;
  }

  function apply() {
    const open = isOpen();
    frame.dataset.nav = narrow ? "drawer" : "docked";
    frame.dataset.sidebar = open ? "open" : "closed";
    sidebar.element.inert = !open;
    stage.inert = narrow && open;
    opener.hidden = open;
    menu.setAttribute("aria-expanded", String(open));
  }

  function setOpen(next) {
    if (narrow) {
      drawer = next;
    } else {
      docked = next;
      setSidebarOpen(next);
    }
    apply();
  }

  function closeToOpener() {
    setOpen(false);
    menu.focus();
  }

  function closeAfterNavigation() {
    if (narrow && drawer) {
      setOpen(false);
      main.focus({ preventScroll: true });
    }
  }

  function focusSearch() {
    if (!isOpen()) {
      setOpen(true);
    }
    sidebar.focusSearch();
  }

  function newRun() {
    location.hash = homeHref;
    closeAfterNavigation();
    requestCompose();
  }

  function onViewportChange(event) {
    narrow = event.matches;
    drawer = false;
    apply();
  }

  // The sidebar answers Escape first when it has a selection to drop; the drawer closes on the next press.
  function onEscape(event) {
    if (event.key === "Escape" && !event.defaultPrevented && narrow && drawer) {
      event.preventDefault();
      closeToOpener();
    }
  }

  narrowQuery.addEventListener("change", onViewportChange);
  window.addEventListener("keydown", onEscape);
  const removeShortcuts = installShortcuts({ search: focusSearch, newRun });
  apply();

  return {
    element: frame,
    main,
    setRoute(route) {
      sidebar.setRoute(route);
      main.dataset.layout = route.layout;
      if (narrow && drawer) {
        setOpen(false);
      }
    },
    paint: indicators.paint,
    dispose() {
      narrowQuery.removeEventListener("change", onViewportChange);
      window.removeEventListener("keydown", onEscape);
      removeShortcuts();
      sidebar.dispose();
    },
  };
}
