// The frame around every page: the sidebar on the left (a drawer on narrow windows) and, above the page, what needs the
// owner: the approvals that wait, the connection while it is not live, the banner of an engaged emergency stop and the
// one of a page that Dot's update left out of date.

import { el, setText, showAfter } from "./dom.js";
import { releaseFailsafe } from "./failsafe.js";
import { icon } from "./icons.js";
import { followSeen, PHONE, setSidebarOpen, sidebarOpen, trackStagebar } from "./layout.js";
import { installShortcuts, requestCompose } from "./shortcuts.js";
import { buildSidebar } from "./sidebar.js";
import { stageTitleSlot, stageToolsSlot } from "./stage.js";
import { quietBreak } from "./state.js";
import { SCHEMES, chooseScheme, nextScheme } from "./theme.js";
import { attempt, toast } from "./ui.js";

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
  const dot = el("span", { class: "live-dot connecting" });
  const connectionLabel = el("span", { class: "label" }, STREAM_LABEL.connecting);
  const connection = el("span", { class: "pill", hidden: true }, dot, connectionLabel);
  const showConnection = showAfter(connection, quietBreak);
  const banner = el(
    "div",
    { class: "banner", hidden: true, role: "alert" },
    icon("alert"),
    el("span", { class: "grow" }, "Emergency stop is engaged. Desktop control is blocked until you release it."),
    el("button", { class: "btn sm", type: "button", onclick: () => attempt(releaseFailsafe) }, "Release"),
  );
  const updated = el(
    "div",
    { class: "banner update", hidden: true, role: "status" },
    icon("refresh"),
    el("span", { class: "grow" }, "Dot was updated since this page was loaded. Load it again to use the new version."),
    el("button", { class: "btn sm", type: "button", onclick: () => location.reload() }, "Load again"),
  );
  return {
    pills: [waiting, connection],
    banners: [banner, updated],
    paint(snapshot) {
      setText(waitingCount, String(snapshot.approvals.length));
      waiting.hidden = snapshot.approvals.length === 0;
      dot.className = `live-dot ${snapshot.stream}`;
      setText(connectionLabel, STREAM_LABEL[snapshot.stream]);
      // The connection shows while it is being made or lost; a live one says nothing (item 120), nor does a break that
      // mends at once (item 286). The runs that work are counted in the sidebar's Working section.
      showConnection(snapshot.stream !== "live");
      connection.title = `Connection to the daemon: ${STREAM_LABEL[snapshot.stream]}`;
      banner.hidden = snapshot.status === null || !snapshot.status.failsafe_engaged;
      updated.hidden = !snapshot.outdated;
    },
  };
}

/**
 * Build the frame. `homeRoute` is where "New chat" leads. Returns the element, the `main` element the pages draw into,
 * `setRoute({ tab, runId, layout, back })`, `paint(snapshot)` and `dispose()`.
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
  // Where the page's Back leads on a phone (item 338): `{ to: "history" }`, `{ to: "#/…" }`, or null on a page whose
  // way back is the list of chats.
  let back = null;
  const menu = el("button", {
    class: "icon-btn",
    type: "button",
    "aria-controls": "sidebar",
    "aria-expanded": "false",
    onclick: () => {
      if (backs()) {
        goBack();
        return;
      }
      setOpen(true);
      sidebar.focusClose();
    },
  });
  function backs() {
    return PHONE.matches && back !== null;
  }
  function goBack() {
    if (back.to === "history") {
      history.back();
    } else {
      location.hash = back.to;
    }
  }
  // On a phone the button at the top left is the one way back, a chevron as in T3 Code's phone app (item 295): to the
  // page before, or to the page's parent (item 338), and on a page opened from the sidebar to the list of chats. The
  // pages' own links back are for a wider window.
  function paintOpener() {
    menu.replaceChildren(icon(PHONE.matches ? "chevronLeft" : "panelLeft"));
    const label = backs() ? "Back" : "Show sidebar";
    menu.title = label;
    menu.setAttribute("aria-label", label);
  }
  paintOpener();
  // While the sidebar is closed the bar holds its opener alone (item 273).
  const opener = el("div", { class: "stagebar-left" }, menu);
  const main = el("main", { class: "page", tabindex: "-1" });
  const stagebar = el(
    "header",
    { class: "stagebar" },
    // The bar drags the desktop shell's window (item 88); a browser ignores the attribute.
    el("div", { class: "stagebar-row", "data-tauri-drag-region": "" }, opener, stageTitleSlot(), stageToolsSlot(), el("div", { class: "stagebar-right" }, indicators.pills)),
    ...indicators.banners,
  );
  const stage = el("div", { class: "stage" }, stagebar, main);
  trackStagebar(stagebar);
  const stopFollowing = followSeen();
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

  function openAppearance() {
    location.hash = "#/settings/appearance";
    closeAfterNavigation();
  }

  function cycleScheme() {
    const id = nextScheme();
    chooseScheme(id);
    toast("ok", `Colour scheme: ${SCHEMES.find((scheme) => scheme.id === id).label}`);
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
  PHONE.addEventListener("change", paintOpener);
  window.addEventListener("keydown", onEscape);
  const removeShortcuts = installShortcuts({ search: focusSearch, newRun, appearance: openAppearance, nextScheme: cycleScheme });
  apply();

  return {
    element: frame,
    main,
    setRoute(route) {
      sidebar.setRoute(route);
      main.dataset.layout = route.layout;
      back = route.back;
      paintOpener();
      if (narrow && drawer) {
        setOpen(false);
      }
    },
    paint: indicators.paint,
    dispose() {
      narrowQuery.removeEventListener("change", onViewportChange);
      PHONE.removeEventListener("change", paintOpener);
      window.removeEventListener("keydown", onEscape);
      removeShortcuts();
      stopFollowing();
      sidebar.dispose();
    },
  };
}
