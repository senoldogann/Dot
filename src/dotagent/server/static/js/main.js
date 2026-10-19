// The entry point: sign-in, the frame with its sidebar and live indicators, and routing between the pages.

import { loadLooks } from "./actors.js";
import { ApiError, clearToken, forgetImages, hasToken, loadToken, onAuthLost, pairWithCode, setToken } from "./api.js";
import { loadOwnThemes } from "./appearance/own.js";
import { loadAssistant } from "./assistant.js";
import { applyStoredSettings } from "./appearance/settings.js";
import { setPageTitle, watchAttention } from "./attention.js";
import { wireConversationCopy } from "./clipboard.js";
import { el, OPEN_FILE_EVENT } from "./dom.js";
import { fileDialog } from "./filepanel.js";
import { icon, wordmark } from "./icons.js";
import { buildShell } from "./shell.js";
import { requestCompose } from "./shortcuts.js";
import { clearStage } from "./stage.js";
import { getSnapshot, onChange, refresh, runLifecycle } from "./state.js";
import { applyStoredDensity, PHONE } from "./layout.js";
import { readPref, writePref } from "./prefs.js";
import { applyStoredScheme, applyStoredTheme, followBarColour } from "./theme.js";
import { attempt, toast } from "./ui.js";
import { openingCurtain, PAGE_READY_EVENT } from "./waiting.js";
import * as run from "./views/run.js";
import * as runs from "./views/runs.js";

const DEFAULT_ROUTE = "/runs";
// The chat screen the owner was on last, a run's page or New chat: Dot opens there (item 286).
const LAST_CHAT_KEY = "dot.lastChat";
// Where a home-screen app added before item 286 starts (its manifest's start_url then): it opens the last chat instead.
const OLD_START = "#/activity";
// The pages of the earlier sidebar live on as tabs of the settings or of Customize; their old addresses lead there.
const MOVED = new Map([
  ["/memory", "/settings/memory"],
  ["/settings/schedules", "/schedules"],
  ["/rules", "/settings/rules"],
  ["/system", "/settings/system"],
  // Connect apps and the settings' Skills became Customize's two tabs (items 316 and 317).
  ["/connect", "/customize/plugins"],
  // Customize opens on Plugins, under Plugins' own address, so that the open tab is one page with one address.
  ["/customize", "/customize/plugins"],
  ["/settings/skills", "/customize/skills"],
  // The Activity page is gone (item 364): what it listed stands in the sidebar and on the home's Today strip.
  ["/activity", "/runs"],
]);
// The chat and the home are what every opening needs and come with main.js; every other page is loaded when it is
// first asked for (`load`), and fetched once the browser is idle after the first page, so that it opens at once later
// and the service worker keeps a copy of it.
// `layout` is put on the page element as `data-layout`, so the style sheets can give each kind of page its own width.
// `parent(params)` is the page a phone's Back leads to when nothing was opened before this page (item 338), or null
// for a page whose way back is the list of chats.
const ROUTES = [
  { tab: "runs", layout: "home", title: "New chat", pattern: /^\/runs$/, view: runs, parent: () => null },
  { tab: "runs", layout: "run", title: "Run", pattern: /^\/runs\/([A-Za-z0-9_-]+)$/, view: run, parent: () => null },
  {
    tab: "campaigns",
    layout: "page",
    title: "Campaigns",
    pattern: /^\/campaigns(?:\/([A-Za-z0-9_-]+))?$/,
    load: () => import("./views/campaigns.js"),
    parent: ([id]) => (id === undefined ? null : "/campaigns"),
  },
  {
    tab: "agents",
    layout: "page",
    title: "Agents",
    pattern: /^\/agents(?:\/([A-Za-z0-9_-]+))?$/,
    load: () => import("./views/agents.js"),
    parent: ([id]) => (id === undefined ? null : "/agents"),
  },
  { tab: "agents", layout: "page", title: "Team", pattern: /^\/team$/, load: () => import("./views/team.js"), parent: () => "/agents" },
  {
    tab: "schedules",
    layout: "page",
    title: "Schedules",
    pattern: /^\/schedules$/,
    load: () => import("./views/schedules.js"),
    parent: () => null,
  },
  {
    tab: "customize",
    layout: "page",
    title: "Customize",
    pattern: /^\/customize(?:\/(plugins|skills))?(?:\/([a-z0-9-]+))?$/,
    load: () => import("./views/customize.js"),
    parent: ([, id]) => (id === undefined ? null : "/customize/plugins"),
  },
  {
    tab: "settings",
    layout: "page",
    title: "Settings",
    pattern: /^\/settings(?:\/([a-z]+))?$/,
    load: () => import("./views/settings.js"),
    // On a phone a tab is a page of the list of the settings (item 330); in a wider window the tabs are the page.
    parent: ([tab]) => (tab === undefined || !PHONE.matches ? null : "/settings"),
  },
  // Settings › System's Diagnostics (item 370) and Open source licences (item 371), pages of their own under it.
  { tab: "settings", layout: "page", title: "Diagnostics", pattern: /^\/diagnostics$/, load: () => import("./views/diagnostics.js"), parent: () => "/settings/system" },
  { tab: "settings", layout: "page", title: "Open source licences", pattern: /^\/licences$/, load: () => import("./views/licences.js"), parent: () => "/settings/system" },
  // Every control of the interface on one page, for checking a look; not linked from the sidebar.
  { tab: "settings", layout: "page", title: "Controls", pattern: /^\/controls$/, load: () => import("./views/controls.js"), parent: () => null },
];

// The build a tab loaded again for as it opened, its pages being of another build than the daemon's (the service worker
// answered from its copy while the network woke): once per build, so that a copy answered again does not load the page
// again and again; the page then says that Dot was updated (item 328).
const RELOADED_KEY = "dot.reloadedFor";
// The longest the curtain stays over a chat's page that has not said it shows its chat (item 328).
const CURTAIN_MAX_MS = 3000;

const root = document.getElementById("app");
let session = null;
// The curtain over the page while Dot opens (waiting.js), lifted once the page shows what it was opened for.
let curtain = null;

function findRoute(path) {
  for (const route of ROUTES) {
    const match = route.pattern.exec(path);
    if (match !== null) {
      return { route, params: match.slice(1) };
    }
  }
  return null;
}

/** Where Dot opens: the chat screen the owner was on last (`LAST_CHAT_KEY`), else New chat. */
function startPath() {
  const last = readPref(LAST_CHAT_KEY);
  const found = last === null ? null : findRoute(last);
  return found !== null && found.route.tab === "runs" ? last : DEFAULT_ROUTE;
}

function currentPath() {
  const fragment = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  return fragment === "" ? startPath() : fragment.split("?")[0];
}

/** Whether the page runs as an app of the home screen (a phone's) rather than in a browser's tab. */
function standalone() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

/** The token from `#token=...`, which `dotx open` and `dotx url` put in the address; it is moved out of the address bar. */
function takeFragmentToken() {
  const fragment = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const value = new URLSearchParams(fragment).get("token");
  if (value === null || value === "") {
    return false;
  }
  setToken(value);
  history.replaceState(null, "", `${location.pathname}${location.search}#${startPath()}`);
  return true;
}

// ------------------------------------------------------------------------------------------------ screens

function screen(...children) {
  document.title = "Dot";
  liftCurtain();
  root.replaceChildren(
    el(
      "div",
      { class: "signin" },
      el("div", { class: "card" }, el("div", { class: "stack" }, el("h1", { class: "signin-mark" }, wordmark()), ...children)),
    ),
  );
}

/** Run `action` when Enter is pressed in `input`. */
function onEnter(input, action) {
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      action();
    }
  });
}

function renderSignIn(message) {
  // A pairing code from Settings › Phone (item 267): the way in for an app on a phone's home screen, whose storage is
  // its own, so that it has none of the token Safari was given.
  const code = el("input", {
    class: "input mono",
    type: "text",
    autocomplete: "one-time-code",
    autocapitalize: "characters",
    spellcheck: "false",
    placeholder: "Pairing code",
    "aria-label": "Pairing code",
  });
  const input = el("input", {
    class: "input mono",
    type: "password",
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
    placeholder: "Access token",
    "aria-label": "Access token",
  });
  const pairButton = el("button", { class: "btn primary", type: "button" }, "Pair");
  const pair = async () => {
    const value = code.value.trim();
    if (value === "") {
      code.focus();
      return;
    }
    pairButton.disabled = true;
    let token;
    try {
      token = await pairWithCode(value);
    } catch (error) {
      renderSignIn(error.message);
      return;
    }
    setToken(token);
    attempt(connect);
  };
  const submit = () => {
    const value = input.value.trim();
    if (value === "") {
      input.focus();
      return;
    }
    setToken(value);
    attempt(connect);
  };
  pairButton.addEventListener("click", () => attempt(pair));
  onEnter(code, () => attempt(pair));
  onEnter(input, submit);
  screen(
    el("p", { class: "muted" }, "Type a pairing code: in Dot on your Mac, or in Safari where Dot is open, go to Settings › Phone and press Pair a phone."),
    message === "" ? null : el("div", { class: "note bad" }, icon("alert"), el("span", null, message)),
    code,
    pairButton,
    el("div", { class: "hint" }, "Or paste the access token of your Dot daemon:"),
    input,
    el("button", { class: "btn", type: "button", onclick: submit }, "Connect"),
    el(
      "div",
      { class: "stack tight" },
      el("div", { class: "hint" }, "The easy way in: this command opens the page already signed in."),
      el("code", { class: "code-line" }, "dotx open"),
      el("div", { class: "hint" }, "Or print the address, token included:"),
      el("code", { class: "code-line" }, "dotx url"),
      el("div", { class: "hint" }, "If your shell does not find dotx, run it from the project folder:"),
      el("code", { class: "code-line" }, ".venv/bin/dotx open"),
    ),
  );
  code.focus();
}

// A daemon out of reach is asked again by itself (item 200): soon at first, since it may be starting or the Mac waking,
// then less often; at once when the browser is back on the network or the page back in view.
const RETRY_FIRST_MS = 2000;
const RETRY_MAX_MS = 30000;
let retryPause = RETRY_FIRST_MS;
let stopRetry = () => {};

function retryNow() {
  stopRetry();
  attempt(connect);
}

function renderUnreachable(message) {
  const when = el("span", null, "");
  screen(
    el("div", { class: "note bad" }, icon("alert"), el("span", null, message)),
    el("p", { class: "muted" }, "Make sure the daemon is running (", el("code", { class: "mono" }, "dotx daemon"), "); this page tries again by itself."),
    el("p", { class: "muted" }, when),
    el("div", { class: "cluster" }, el("button", { class: "btn primary", type: "button", onclick: retryNow }, icon("refresh"), "Try again")),
  );
  const due = Date.now() + retryPause;
  retryPause = Math.min(retryPause * 2, RETRY_MAX_MS);
  const tell = () => {
    when.textContent = `Trying again in ${Math.max(1, Math.ceil((due - Date.now()) / 1000))} s.`;
  };
  const inView = () => {
    if (document.visibilityState === "visible") {
      retryNow();
    }
  };
  tell();
  const ticker = setInterval(tell, 1000);
  const timer = setTimeout(retryNow, due - Date.now());
  window.addEventListener("online", retryNow);
  document.addEventListener("visibilitychange", inView);
  stopRetry = () => {
    clearInterval(ticker);
    clearTimeout(timer);
    window.removeEventListener("online", retryNow);
    document.removeEventListener("visibilitychange", inView);
    stopRetry = () => {};
  };
}

/** The daemon answered, with an error: it runs, so the screen says what failed rather than to start it. */
function renderFailed(error) {
  const cause = error.context && typeof error.context.error === "string" ? error.context.error : null;
  screen(
    el("div", { class: "note bad" }, icon("alert"), el("span", null, `The daemon is running but could not answer: ${error.message}`)),
    cause === null ? null : el("p", { class: "muted mono" }, cause),
    // The log's folder is where the daemon was told to keep it (DOT_LOG_DIR), which this page cannot know.
    el("p", { class: "muted" }, "Its log has the details: ", el("code", { class: "mono" }, "dotx service status"), " names its folder."),
    el("div", { class: "cluster" }, el("button", { class: "btn primary", type: "button", onclick: () => attempt(connect) }, icon("refresh"), "Try again")),
  );
}

/** While Dot waits for the daemon as it opens: the curtain, with the chat's bar and composer when it opens on a chat
 * this browser showed before. */
function renderWaiting() {
  liftCurtain();
  const found = findRoute(currentPath());
  curtain = openingCurtain(found !== null && found.route.layout === "run" ? found.params[0] : null);
  root.replaceChildren(curtain.element);
}

function liftCurtain() {
  if (curtain !== null) {
    curtain.lift();
    curtain = null;
  }
}

/** Lift the curtain once the page of `layout` shows what it was opened for: a chat's page says so (PAGE_READY_EVENT),
 * within CURTAIN_MAX_MS; every other page at once. */
function liftWhenShown(layout) {
  if (curtain === null) {
    return;
  }
  if (layout !== "run") {
    liftCurtain();
    return;
  }
  const timer = setTimeout(liftCurtain, CURTAIN_MAX_MS);
  document.addEventListener(
    PAGE_READY_EVENT,
    () => {
      clearTimeout(timer);
      liftCurtain();
    },
    { once: true },
  );
}

/** Load the page again, once per build of the daemon's pages (`RELOADED_KEY`): whether it was loaded again. A tab whose
 * storage is blocked cannot count, and is not. */
function reloadForBuild(pages) {
  try {
    if (sessionStorage.getItem(RELOADED_KEY) === pages) {
      return false;
    }
    sessionStorage.setItem(RELOADED_KEY, pages);
  } catch {
    return false;
  }
  location.reload();
  return true;
}

// ------------------------------------------------------------------------------------------------ session

function endSession() {
  if (session !== null) {
    session.stop();
    session = null;
  }
}

function beginSession() {
  const controller = new AbortController();
  const shell = buildShell({ homeRoute: DEFAULT_ROUTE });
  let disposeView = () => undefined;
  // The curtain stays over the page until the page shows what it was opened for.
  if (curtain === null) {
    root.replaceChildren(shell.element);
  } else {
    root.prepend(shell.element);
  }

  // An entry of the tab's history whose page was opened by a link of the page before it says so
  // (`history.state.dotLinked`, item 338), so the browser's own Back and Forward and a reload keep a phone's Back as
  // it was: such a page goes back to the page before it; another goes to the page above it, or opens the chats. The
  // link is told by the click that leads to it, wherever on the page it is (a dialog, a helper's row that stops its
  // clicks), but in the sidebar, whose pages start anew, and but a click that opens another tab.
  let linkedTo = null;
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target instanceof Element ? event.target.closest("a[href^='#/']") : null;
      const plain = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
      if (link instanceof HTMLAnchorElement && link.target === "" && plain && link.closest("#sidebar") === null) {
        const path = link.getAttribute("href").slice(1).split("?")[0];
        // A link to an old address counts for the page it leads to.
        linkedTo = MOVED.get(path) ?? path;
      }
    },
    true,
  );
  // The page on screen and whether it was opened by a link: Back is reckoned again when a phone turns (a settings tab
  // has a page above it on a phone alone).
  let current = null;

  /** Whether the page on screen at `path` was opened by a link of the page before it, kept in its history entry. Only
   * such an entry is written: writing the history as any page opens would cut short a move to another address begun
   * meanwhile (Chromium drops it), as one typed or asked for while Dot starts. */
  function linkedHere(path) {
    const state = history.state;
    const kept = state !== null && typeof state === "object" && state.dotPath === path && state.dotLinked === true;
    const linked = kept || linkedTo === path;
    linkedTo = null;
    if (linked && !kept) {
      history.replaceState({ ...(state !== null && typeof state === "object" ? state : {}), dotPath: path, dotLinked: true }, "");
    }
    return linked;
  }

  /** Where a phone's Back leads from the page `found`: the page before it, the page above it, or nowhere (null). */
  function backFrom(found, linked) {
    if (linked) {
      return { to: "history" };
    }
    const parent = found.route.parent(found.params);
    return parent === null ? null : { to: `#${parent}` };
  }

  PHONE.addEventListener("change", () => {
    if (current !== null) {
      shell.setBack(backFrom(current.found, current.linked));
    }
  });

  // Which page was asked for last: a page whose script came after another was asked for is not opened.
  let asked = 0;

  async function show() {
    disposeView();
    disposeView = () => undefined;
    shell.main.replaceChildren();
    clearStage();
    forgetImages();
    const path = currentPath();
    const moved = MOVED.get(path);
    if (moved !== undefined) {
      location.replace(`${location.pathname}${location.search}#${moved}`);
      return;
    }
    const found = findRoute(path);
    if (found === null) {
      location.replace(`${location.pathname}${location.search}#${DEFAULT_ROUTE}`);
      return;
    }
    if (found.route.tab === "runs") {
      writePref(LAST_CHAT_KEY, path);
    }
    const linked = linkedHere(path);
    current = { found, linked };
    shell.setRoute({
      tab: found.route.tab,
      runId: found.route.layout === "run" ? found.params[0] : null,
      layout: found.route.layout,
      back: backFrom(found, linked),
    });
    setPageTitle(`${found.route.title} · Dot`);
    window.scrollTo(0, 0);
    const ctx = {
      params: found.params,
      navigate: (target) => {
        location.hash = `#${target}`;
      },
      // The chat goes on at the address of another of its runs (a follow-up, the queue's next message, Continue, an
      // answer, a rewind; item 345): it is the same page, so it takes this page's place in the history and keeps how
      // the page was reached. A phone's Back still leads where it led (to Activity, for a run opened there), and the
      // browser's Back skips the run the chat went on from.
      goOnAt: (target) => {
        const linked = current?.linked;
        history.replaceState(linked ? { dotPath: target, dotLinked: true } : null, "", `${location.pathname}${location.search}#${target}`);
        // Once the caller's own work is done, as the event of a new address would come.
        setTimeout(show, 0);
      },
      setTitle: (text) => {
        setPageTitle(`${text} · Dot`);
      },
    };
    const ticket = ++asked;
    try {
      const view = found.route.view ?? (await found.route.load());
      if (ticket !== asked) {
        return;
      }
      disposeView = view.mount(shell.main, ctx);
    } catch (error) {
      console.error("page failed to open", error);
      // The error of a page asked for earlier never covers the page asked for since, which holds the ticket now.
      if (ticket !== asked) {
        return;
      }
      shell.main.replaceChildren(el("div", { class: "card empty" }, icon("alert"), el("div", null, `This page failed to open: ${error.message}`)));
      liftCurtain();
    }
    liftWhenShown(found.route.layout);
  }

  // The scripts of the pages that load on demand are fetched once the browser is idle, so that the first one asked for
  // opens at once; a fetch that fails is left to the page that asks for it, which then says so.
  function warmUpPages() {
    const warm = () => {
      for (const route of ROUTES) {
        if (route.load !== undefined) {
          route.load().catch(() => undefined);
        }
      }
    };
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(warm, { timeout: 4000 });
    } else {
      setTimeout(warm, 1500);
    }
  }

  // A page that Dot's update left out of date loads again once it is out of sight, so that it is the new version when
  // the owner comes back to it (item 328).
  function reloadOutOfSight() {
    if (document.visibilityState === "hidden" && getSnapshot().outdated) {
      location.reload();
    }
  }

  const stopPainting = onChange((snapshot) => shell.paint(snapshot));
  shell.paint(getSnapshot());
  const stopAttention = watchAttention();
  window.addEventListener("hashchange", show);
  document.addEventListener("visibilitychange", reloadOutOfSight);
  // Opened at no page, or as a home-screen app added before item 286: the last chat screen, in the address too.
  if (location.hash === "" || location.hash === "#" || (standalone() && location.hash === OLD_START)) {
    history.replaceState(null, "", `${location.pathname}${location.search}#${startPath()}`);
  }
  show();
  warmUpPages();

  runLifecycle(controller.signal, () => handleAuthLost()).catch((error) => {
    console.error("the event stream ended", error);
    toast("error", `The live connection ended: ${error.message}`);
  });

  session = {
    stop() {
      controller.abort();
      stopPainting();
      stopAttention();
      window.removeEventListener("hashchange", show);
      document.removeEventListener("visibilitychange", reloadOutOfSight);
      liftCurtain();
      disposeView();
      disposeView = () => undefined;
      shell.dispose();
    },
  };
}

function handleAuthLost() {
  if (session === null) {
    return;
  }
  endSession();
  clearToken();
  renderSignIn("The daemon did not accept the saved access token. Pair again with a new code, paste the current token, or open this page with dotx open.");
}

async function connect() {
  renderWaiting();
  try {
    await Promise.all([refresh(), loadLooks(), loadOwnThemes(), loadAssistant()]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearToken();
      renderSignIn("The daemon did not accept that access token.");
      return;
    }
    // A status is the daemon's answer; without one (0, or no ApiError) nothing that is the daemon answered.
    if (error instanceof ApiError && error.status !== 0) {
      renderFailed(error);
      return;
    }
    renderUnreachable(error.message);
    return;
  }
  retryPause = RETRY_FIRST_MS;
  // Pages of another build than the daemon's (a copy the service worker kept, answered while the network woke) load
  // again before they show anything, so that they never meet an API they do not know (item 309).
  const snapshot = getSnapshot();
  if (snapshot.outdated && reloadForBuild(snapshot.status.pages)) {
    return;
  }
  beginSession();
}

async function boot() {
  // The desktop shell's New chat (menu bar item, ⇧⌘Space) calls this after it moves to the home.
  window.focusComposer = requestCompose;
  if (!takeFragmentToken()) {
    loadToken();
  }
  if (!hasToken()) {
    renderSignIn("");
    return;
  }
  await connect();
}

// The look goes on first, before any screen is drawn, so the page never shows in the wrong skin.
applyStoredTheme();
applyStoredScheme();
applyStoredDensity();
applyStoredSettings();
followBarColour();
// The service worker keeps the shell files for when the Mac is out of reach and shows the pushes of the daemon; a
// browser that has none (or an insecure address) simply has neither.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch((error) => console.warn("the service worker could not be registered", error));
}
onAuthLost(handleAuthLost);
// A copy out of the conversation goes on the clipboard as the markdown it was written in (item 206).
wireConversationCopy(document);
// A file asked for on a page without the panel beside the conversation shows in a dialog (item 269); a conversation's
// page shows it in its panel and keeps the request to itself.
document.addEventListener(OPEN_FILE_EVENT, (event) => {
  if (event instanceof CustomEvent) {
    fileDialog(event.detail, event.target);
  }
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  if (reason instanceof ApiError && reason.status === 401) {
    return;
  }
  console.error("unhandled rejection", reason);
  toast("error", reason instanceof Error ? reason.message : String(reason));
});

attempt(boot);
