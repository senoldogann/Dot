// The entry point: sign-in, the frame with its sidebar and live indicators, and routing between the pages.

import { loadLooks } from "./actors.js";
import { ApiError, clearToken, forgetImages, hasToken, loadToken, onAuthLost, pairWithCode, setToken } from "./api.js";
import { loadOwnThemes } from "./appearance/own.js";
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
import { applyStoredDensity } from "./layout.js";
import { readPref, writePref } from "./prefs.js";
import { applyStoredScheme, applyStoredTheme } from "./theme.js";
import { attempt, toast } from "./ui.js";
import * as activity from "./views/activity.js";
import * as agents from "./views/agents.js";
import * as team from "./views/team.js";
import * as campaigns from "./views/campaigns.js";
import * as connectApps from "./views/connect.js";
import * as controls from "./views/controls.js";
import * as run from "./views/run.js";
import * as runs from "./views/runs.js";
import * as settings from "./views/settings.js";

const DEFAULT_ROUTE = "/runs";
// The chat screen the owner was on last, a run's page or New chat: Dot opens there (item 286).
const LAST_CHAT_KEY = "dot.lastChat";
// Where a home-screen app added before item 286 starts (its manifest's start_url then): it opens the last chat instead.
const OLD_START = "#/activity";
// The pages of the earlier sidebar live on as tabs of the settings; their old addresses lead there.
const MOVED = new Map([
  ["/memory", "/settings/memory"],
  ["/schedules", "/settings/schedules"],
  ["/rules", "/settings/rules"],
  ["/system", "/settings/system"],
]);
// `layout` is put on the page element as `data-layout`, so the style sheets can give each kind of page its own width.
const ROUTES = [
  { tab: "runs", layout: "home", title: "New chat", pattern: /^\/runs$/, view: runs },
  { tab: "runs", layout: "run", title: "Run", pattern: /^\/runs\/([A-Za-z0-9_-]+)$/, view: run },
  { tab: "activity", layout: "page", title: "Activity", pattern: /^\/activity$/, view: activity },
  { tab: "campaigns", layout: "page", title: "Campaigns", pattern: /^\/campaigns(?:\/([A-Za-z0-9_-]+))?$/, view: campaigns },
  { tab: "agents", layout: "page", title: "Agents", pattern: /^\/agents(?:\/([A-Za-z0-9_-]+))?$/, view: agents },
  { tab: "agents", layout: "page", title: "Team", pattern: /^\/team$/, view: team },
  { tab: "connect", layout: "page", title: "Connect apps", pattern: /^\/connect$/, view: connectApps },
  { tab: "settings", layout: "page", title: "Settings", pattern: /^\/settings(?:\/([a-z]+))?$/, view: settings },
  // Every control of the interface on one page, for checking a look; not linked from the sidebar.
  { tab: "settings", layout: "page", title: "Controls", pattern: /^\/controls$/, view: controls },
];

const root = document.getElementById("app");
let session = null;

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

function renderConnecting() {
  screen(el("div", { class: "row muted" }, el("div", { class: "spinner" }), el("span", null, "Connecting to the daemon…")));
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
  root.replaceChildren(shell.element);

  function show() {
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
    shell.setRoute({
      tab: found.route.tab,
      runId: found.route.layout === "run" ? found.params[0] : null,
      layout: found.route.layout,
    });
    setPageTitle(`${found.route.title} · Dot`);
    window.scrollTo(0, 0);
    const ctx = {
      params: found.params,
      navigate: (target) => {
        location.hash = `#${target}`;
      },
      setTitle: (text) => {
        setPageTitle(`${text} · Dot`);
      },
    };
    try {
      disposeView = found.route.view.mount(shell.main, ctx);
    } catch (error) {
      console.error("page failed to open", error);
      shell.main.replaceChildren(el("div", { class: "card empty" }, icon("alert"), el("div", null, `This page failed to open: ${error.message}`)));
    }
  }

  const stopPainting = onChange((snapshot) => shell.paint(snapshot));
  shell.paint(getSnapshot());
  const stopAttention = watchAttention();
  window.addEventListener("hashchange", show);
  // Opened at no page, or as a home-screen app added before item 286: the last chat screen, in the address too.
  if (location.hash === "" || location.hash === "#" || (standalone() && location.hash === OLD_START)) {
    history.replaceState(null, "", `${location.pathname}${location.search}#${startPath()}`);
  }
  show();

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
  renderConnecting();
  try {
    await refresh();
    await loadLooks();
    await loadOwnThemes();
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
document.addEventListener(OPEN_FILE_EVENT, (event) => fileDialog(event.detail, event.target));

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  if (reason instanceof ApiError && reason.status === 401) {
    return;
  }
  console.error("unhandled rejection", reason);
  toast("error", reason instanceof Error ? reason.message : String(reason));
});

attempt(boot);
