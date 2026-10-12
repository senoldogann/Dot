// The entry point: sign-in, the frame with its sidebar and live indicators, and routing between the pages.

import { loadLooks } from "./actors.js";
import { ApiError, clearToken, forgetImages, hasToken, loadToken, onAuthLost, setToken } from "./api.js";
import { loadOwnThemes } from "./appearance/own.js";
import { applyStoredSettings } from "./appearance/settings.js";
import { setPageTitle, watchAttention } from "./attention.js";
import { el } from "./dom.js";
import { icon, wordmark } from "./icons.js";
import { buildShell } from "./shell.js";
import { requestCompose } from "./shortcuts.js";
import { clearStage } from "./stage.js";
import { getSnapshot, onChange, refresh, runLifecycle } from "./state.js";
import { applyStoredDensity } from "./layout.js";
import { applyStoredScheme, applyStoredTheme } from "./theme.js";
import { attempt, toast } from "./ui.js";
import * as activity from "./views/activity.js";
import * as agents from "./views/agents.js";
import * as team from "./views/team.js";
import * as campaigns from "./views/campaigns.js";
import * as controls from "./views/controls.js";
import * as run from "./views/run.js";
import * as runs from "./views/runs.js";
import * as settings from "./views/settings.js";

const DEFAULT_ROUTE = "/runs";
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

function currentPath() {
  const fragment = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  return fragment === "" ? DEFAULT_ROUTE : fragment.split("?")[0];
}

/** The token from `#token=...`, which `dotx open` and `dotx url` put in the address; it is moved out of the address bar. */
function takeFragmentToken() {
  const fragment = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const value = new URLSearchParams(fragment).get("token");
  if (value === null || value === "") {
    return false;
  }
  setToken(value);
  history.replaceState(null, "", `${location.pathname}${location.search}#${DEFAULT_ROUTE}`);
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

function renderSignIn(message) {
  const input = el("input", {
    class: "input mono",
    type: "password",
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
    placeholder: "Access token",
    "aria-label": "Access token",
  });
  const submit = () => {
    const value = input.value.trim();
    if (value === "") {
      input.focus();
      return;
    }
    setToken(value);
    attempt(connect);
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  });
  screen(
    el("p", { class: "muted" }, "Paste the access token of your Dot daemon to continue."),
    message === "" ? null : el("div", { class: "note bad" }, icon("alert"), el("span", null, message)),
    input,
    el("button", { class: "btn primary", type: "button", onclick: submit }, "Connect"),
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
  input.focus();
}

function renderUnreachable(message) {
  screen(
    el("div", { class: "note bad" }, icon("alert"), el("span", null, message)),
    el("p", { class: "muted" }, "Make sure the daemon is running (", el("code", { class: "mono" }, "dotx daemon"), ") and try again."),
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
  renderSignIn("The daemon did not accept the saved access token. Paste the current one, or open this page with dotx open.");
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
    renderUnreachable(error.message);
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
// The service worker keeps the shell files for when the Mac is out of reach and shows the pushes of the daemon; a
// browser that has none (or an insecure address) simply has neither.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch((error) => console.warn("the service worker could not be registered", error));
}
onAuthLost(handleAuthLost);

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  if (reason instanceof ApiError && reason.status === 401) {
    return;
  }
  console.error("unhandled rejection", reason);
  toast("error", reason instanceof Error ? reason.message : String(reason));
});

attempt(boot);
