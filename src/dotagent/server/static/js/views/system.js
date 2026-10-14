// The system page: how the daemon is doing, the emergency stop, the health checks and the models each provider offers.

import { DOT_ID, lookOf, rememberLook } from "../actors.js";
import { api, clearToken } from "../api.js";
import { BUSY_SEND, SEND_KEY, setValue, valueOf } from "../appearance/settings.js";
import {
  notificationsAvailable,
  notificationsBlocked,
  notificationsOn,
  setNotifications,
  setTellEvenWhenLooking,
  tellEvenWhenLooking,
} from "../attention.js";
import { compact, copyText, dateTimeFull, duration, el, modelName, plural } from "../dom.js";
import { offersChoice } from "../efforts.js";
import { engageFailsafe, releaseFailsafe } from "../failsafe.js";
import { dotName, NAME_CHARS, setDotName } from "../face.js";
import { OWNER_CHARS, ownerName, setOwnerName } from "../presence.js";
import { icon } from "../icons.js";
import { lookPicker } from "../lookpicker.js";
import { choiceSetting, foldCard, foldGroup, inlineError, inlineLoading, paneHead, segmented, switchSetting } from "../panels.js";
import { providerLabel, providerOf } from "../providers.js";
import { reasoningSelect } from "../reasoning.js";
import { approvalLabel } from "../runmodes.js";
import { scrollCue } from "../scrollcue.js";
import { forgetModels, getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const NOT_SET = "—";
// The roles a model is configured for, and the tab where a missing or refused key is put right.
const MODEL_ROLES = ["main", "vision", "review"];
const KEYS_TAB = "#/settings/keys";

function section(title, actions, body) {
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("span", { class: "grow" }, title), actions), body);
}

function entry(label, value) {
  return el("div", null, el("dt", null, label), el("dd", null, value));
}

/** An entry that takes a whole row of the grid, for a value too long for one cell, such as a path. */
function wideEntry(label, value) {
  return el("div", { class: "kv-wide" }, el("dt", null, label), el("dd", null, value));
}

function orDash(value) {
  return value === null || value === "" ? NOT_SET : String(value);
}

function desktopText(status) {
  if (status.failsafe_engaged) {
    return "blocked by the emergency stop";
  }
  return status.desktop_enabled ? "enabled" : "disabled";
}

/** Item 153: the MCP servers that could not start with the daemon, one line each with the reason; Dot runs without their tools. */
function leftOutServers(failures) {
  return [
    ...failures.map((failure) => el("div", null, el("span", { class: "badge b-warn" }, failure.server), ` ${failure.error}`)),
    el("div", { class: "muted" }, "dotx mcp list shows more; Dot starts them again when it restarts."),
  ];
}

function statusEntries(status) {
  return [
    entry("Version", status.version),
    entry("Process id", String(status.pid)),
    entry("Uptime", duration(Math.max(0, Date.now() / 1000 - status.started_at))),
    entry("Started", dateTimeFull(status.started_at)),
    entry("Address", `${status.host}:${status.port}`),
    entry("Safety preset", status.preset),
    entry("Approvals", approvalLabel(status.approvals)),
    entry("Goal mode", status.goal_mode ? "on" : "off"),
    entry("Scheduler", status.scheduler_running ? "running" : "stopped"),
    entry("Desktop control", desktopText(status)),
    entry("MCP tools", String(status.mcp_tools)),
    entry("Active runs", String(status.active_runs.length)),
    entry("Waiting for you", plural(status.pending_approvals, "approval", "approvals")),
    entry("Main model", status.models.main),
    entry("Vision model", orDash(status.models.vision)),
    entry("Compaction model", "each run's own model"),
    entry("Review model", orDash(status.models.review)),
    entry("Reasoning effort", orDash(status.models.effort)),
    // A whole row, so it comes after the cells, like Home: among them it would cut the row before it short.
    status.mcp_failures.length > 0 ? wideEntry("MCP servers left out", leftOutServers(status.mcp_failures)) : null,
    wideEntry("Home", el("span", { class: "mono" }, status.home)),
  ];
}

/** Desktop notifications: the one setting that also depends on what the browser allows. */
function buildNotifications() {
  const setting = switchSetting({
    label: "Desktop notifications",
    hint: "",
    read: notificationsOn,
    write: setNotifications,
    onToggled: paint,
  });

  // Item 75: a run on the page you are looking at stays quiet unless you ask otherwise.
  const always = switchSetting({
    label: "Tell me even when I am looking",
    hint: "Otherwise a run that ends or asks on the page you are looking at stays quiet (the tab visible, the window active, the run's page open); the tab title counts either way.",
    read: tellEvenWhenLooking,
    write: async (on) => setTellEvenWhenLooking(on),
    onToggled: paint,
  });

  function paint() {
    setting.paint();
    always.paint();
    const available = notificationsAvailable();
    const blocked = notificationsBlocked();
    setting.toggle.disabled = !available || blocked;
    always.toggle.disabled = !notificationsOn();
    if (!available) {
      setting.note.textContent = "This browser cannot show desktop notifications.";
    } else if (blocked) {
      setting.note.textContent = "The browser blocks notifications for this address. Allow them in its site settings, then reload this page.";
    } else {
      setting.note.textContent =
        "Tell me when Dot needs an answer or a run ends and I am not looking at that run: the tab hidden, the window inactive or another page open. The tab title always counts the approvals that wait.";
    }
  }

  paint();
  return section("Notifications", null, el("div", { class: "card-pad stack" }, setting.element, always.element));
}

/** The composer's keys (item 230), kept in this browser: the key that sends, and what a message to a run at work
 * does. */
function buildComposerCard() {
  const sendKey = choiceSetting({
    label: "Send with",
    hint: "Enter sends and Shift+Enter breaks a line, or ⌘Enter (Ctrl+Enter) sends and Enter breaks a line.",
    options: [
      { id: "enter", label: "Enter", hint: "Enter sends; Shift+Enter breaks a line." },
      { id: "mod-enter", label: "⌘Enter", hint: "⌘Enter or Ctrl+Enter sends; Enter breaks a line." },
    ],
    selected: valueOf(SEND_KEY),
    onSelect: (id) => setValue(SEND_KEY, id),
  });
  const busy = choiceSetting({
    label: "While a run works",
    hint: "A message waits in the chat's queue and starts the next run once this one has ended, or steers the run at once; ⌘⇧Enter sends it the other way. A campaign's session is always steered.",
    options: [
      { id: "queue", label: "Queue", hint: "The message waits in the chat's queue." },
      { id: "steer", label: "Steer", hint: "The message goes to the run at work at once." },
    ],
    selected: valueOf(BUSY_SEND),
    onSelect: (id) => setValue(BUSY_SEND, id),
  });
  return section("Composer", null, el("div", { class: "card-pad stack" }, sendKey.element, busy.element));
}

function buildDaemon() {
  const refreshButton = el(
    "button",
    {
      class: "btn ghost sm",
      type: "button",
      onclick: () => attempt(refresh),
    },
    icon("refresh"),
    "Refresh",
  );
  const body = el("div", null, inlineLoading());
  return {
    element: section("Daemon", refreshButton, body),
    paint(snapshot) {
      if (snapshot.status !== null) {
        body.replaceChildren(el("dl", { class: "kv" }, statusEntries(snapshot.status)));
      }
    },
  };
}

function buildFailsafe() {
  const text = el("div", { class: "grow" });
  const engage = el(
    "button",
    { class: "btn danger solid", type: "button", onclick: () => attempt(engageFailsafe) },
    icon("stop"),
    "Emergency stop",
  );
  const release = el("button", { class: "btn", type: "button", onclick: () => attempt(releaseFailsafe) }, "Release");
  return {
    element: el("section", { class: "card card-pad row" }, text, engage, release),
    paint(snapshot) {
      const engaged = snapshot.status !== null && snapshot.status.failsafe_engaged;
      text.textContent = engaged
        ? "The emergency stop is engaged: every run was cancelled and desktop control is blocked."
        : "The emergency stop cancels every running task and blocks desktop control until you release it.";
      engage.hidden = engaged;
      release.hidden = !engaged;
    },
  };
}

function checkRow(check) {
  let badge = el("span", { class: "badge b-ok" }, "ok");
  if (!check.ok) {
    badge = check.required ? el("span", { class: "badge b-bad" }, "failing") : el("span", { class: "badge b-warn" }, "optional");
  }
  return el("tr", null, el("td", null, check.name), el("td", null, badge), el("td", { class: "muted" }, check.detail));
}

/** What the folded Health checks card shows: how many checks failed, which ones are optional, or that all passed. */
function doctorSummary(checks) {
  const failing = checks.filter((check) => !check.ok && check.required).length;
  const optional = checks.filter((check) => !check.ok && !check.required).length;
  const verdicts = [];
  if (failing > 0) {
    verdicts.push(el("span", { class: "badge b-bad" }, `${failing} failing`));
  }
  if (optional > 0) {
    verdicts.push(el("span", { class: "badge b-warn" }, `${optional} optional`));
  }
  if (verdicts.length === 0) {
    verdicts.push(el("span", { class: "badge b-ok" }, "all ok"));
  }
  return [...verdicts, el("span", null, plural(checks.length, "check", "checks"))];
}

function buildDoctor(isDisposed) {
  const body = el("div", null, inlineLoading());
  const again = el(
    "button",
    { class: "btn ghost sm", type: "button", title: "Run the health checks again", "aria-label": "Run again", onclick: () => attempt(run) },
    icon("refresh"),
    el("span", { class: "label" }, "Run again"),
  );
  const card = foldCard({ id: "system-doctor", title: "Health checks", actions: again, body, openByDefault: false });

  async function run() {
    body.replaceChildren(inlineLoading());
    card.setSummary();
    try {
      const checks = await api.get("/doctor");
      if (isDisposed()) {
        return;
      }
      card.setSummary(...doctorSummary(checks));
      body.replaceChildren(
        el(
          "div",
          { class: "table-scroll" },
          el(
            "table",
            { class: "table" },
            el("thead", null, el("tr", null, el("th", null, "Check"), el("th", null, "Result"), el("th", null, "Detail"))),
            el("tbody", null, checks.map(checkRow)),
          ),
        ),
      );
    } catch (error) {
      if (!isDisposed()) {
        console.error("the health checks failed", error);
        card.setSummary(el("span", { class: "badge b-bad" }, "could not run"));
        body.replaceChildren(inlineError(error, () => attempt(run)));
      }
    }
  }

  return { element: card.element, run };
}

function capabilities(model) {
  return el(
    "div",
    { class: "cluster" },
    model.vision ? el("span", { class: "chip" }, "vision") : null,
    model.tools ? el("span", { class: "chip" }, "tools") : null,
    model.thinking ? el("span", { class: "chip" }, "thinking") : null,
    model.free ? el("span", { class: "chip" }, "free") : null,
    model.fast_mode ? el("span", { class: "chip", title: "Fast mode, chosen in the composer's Reasoning menu" }, "fast mode") : null,
  );
}

/** The reasoning level of a model, or a dash when it has fewer than two levels and so nothing to choose. */
function reasoningControl(model) {
  if (!offersChoice(model)) {
    return el("span", { class: "faint", title: "This model has no reasoning levels to choose from" }, NOT_SET);
  }
  return reasoningSelect(model, `Reasoning level of ${model.ref}`);
}

/** The window a model gets, and its own beside it when Dot asks for less of it (item 221). */
function contextCell(model) {
  if (model.context_length === null) {
    return el("td", { class: "muted" }, NOT_SET);
  }
  const used = `${compact(model.context_length)} tokens`;
  if (model.own_context_length === null) {
    return el("td", { class: "muted" }, used);
  }
  const title = `Dot asks for ${model.context_length.toLocaleString()} tokens ([providers.ollama] num_ctx or num_ctx_cloud); the model takes ${model.own_context_length.toLocaleString()}.`;
  return el("td", { class: "muted", title }, `${used} of ${compact(model.own_context_length)}`);
}

function modelRow(model, mainModel) {
  return el(
    "tr",
    null,
    el(
      "td",
      null,
      el("span", { class: "mono" }, model.ref),
      model.ref === mainModel ? el("span", { class: "badge b-info" }, "default") : null,
    ),
    el("td", null, capabilities(model)),
    el("td", { class: "reasoning-cell" }, reasoningControl(model)),
    contextCell(model),
  );
}

function modelTable(provider, mainModel) {
  return el(
    "div",
    { class: "table-scroll" },
    el(
      "table",
      { class: "table wide" },
      el("thead", null, el("tr", null, el("th", null, "Model"), el("th", null, "Can"), el("th", null, "Reasoning"), el("th", null, "Context"))),
      el("tbody", null, provider.models.map((model) => modelRow(model, mainModel))),
    ),
  );
}

/** The providers the configured models (main, vision and review) run on: a key missing for one of them stops runs. */
function providersInUse(status) {
  if (status === null) {
    return [];
  }
  return MODEL_ROLES.map((role) => providerOf(status.models[role]));
}

/** Whether a provider that did not answer is a problem: one without a key is not, unless a configured model runs on it. */
function isFailing(provider, inUse) {
  return provider.error !== null && (provider.problem !== "no_key" || inUse);
}

/** Why a provider listed no models; a missing or refused key links to the tab that saves keys. */
function problemNote(provider, inUse) {
  if (provider.problem === "no_key") {
    return el(
      "div",
      { class: inUse ? "note bad" : "note" },
      icon("key"),
      el("span", null, `No ${providerLabel(provider.provider)} key is set, so its models cannot be listed.`),
      el("a", { href: KEYS_TAB }, "Add a key"),
    );
  }
  return el(
    "div",
    { class: "note bad" },
    icon("alert"),
    el("span", null, provider.error),
    provider.problem === "key_refused" ? el("a", { href: KEYS_TAB }, "Check the key") : null,
  );
}

function providerBody(provider, mainModel, inUse) {
  if (provider.error !== null) {
    return problemNote(provider, inUse);
  }
  if (provider.models.length === 0) {
    return el("div", { class: "hint" }, "This provider lists no models.");
  }
  return modelTable(provider, mainModel);
}

// The badge of a provider that answered with a refusal: of its key (HTTP 401) or of the access (HTTP 403).
const PROBLEM_BADGES = { key_refused: "key refused", access_refused: "access refused" };

function problemBadge(provider, inUse) {
  if (provider.error === null) {
    return null;
  }
  if (provider.problem === "no_key") {
    return el("span", { class: inUse ? "badge b-bad" : "badge b-muted" }, "no key");
  }
  return el("span", { class: "badge b-bad" }, PROBLEM_BADGES[provider.problem] ?? "unavailable");
}

/** One provider as a fold: open when it owns the default model or fails, so the cause is in sight. */
function providerBlock(provider, mainModel, inUse) {
  const ownsMain = provider.models.some((model) => model.ref === mainModel);
  return foldGroup({
    id: `system-models-${provider.provider}`,
    head: el(
      "span",
      { class: "row grow" },
      el("strong", null, providerLabel(provider.provider)),
      provider.error === null ? el("span", { class: "hint" }, plural(provider.models.length, "model", "models")) : null,
      problemBadge(provider, inUse),
    ),
    body: providerBody(provider, mainModel, inUse),
    openByDefault: ownsMain || isFailing(provider, inUse),
  });
}

/** What the folded Models card shows: the providers, the models, any provider that fails and any without a key. */
function modelsSummary(providers, inUse) {
  const total = providers.reduce((count, provider) => count + provider.models.length, 0);
  const failing = providers.filter((provider) => isFailing(provider, inUse.includes(provider.provider))).length;
  const keyless = providers.filter((provider) => provider.problem === "no_key" && !inUse.includes(provider.provider)).length;
  return [
    el("span", null, plural(providers.length, "provider", "providers")),
    el("span", null, plural(total, "model", "models")),
    failing > 0 ? el("span", { class: "badge b-bad" }, `${failing} unavailable`) : null,
    keyless > 0 ? el("span", { class: "badge b-muted" }, `${keyless} without a key`) : null,
  ];
}

/**
 * The default model (item 199): the one a run that names none starts on, a new chat's among them. It offers the models
 * that can call tools, as the composer does, and the one that is the default now, listed or not. `paint(providers,
 * mainModel)` fills it again; the select stays, and so does the focus on it.
 */
function defaultModelSetting() {
  let shown = "";
  const select = el("select", { class: "select sm", "aria-label": "Default model" });
  const element = el(
    "div",
    { class: "setting models-default", hidden: true },
    el("div", { class: "row" }, el("span", { class: "grow" }, "Default model"), select),
    el("div", { class: "hint" }, "New chats start on it, and so does any run that names no model. A chat keeps the model it has; Default in its composer follows this one."),
  );
  // Each pick is sent once the one before it was answered, so the last one made is the default; the select shows the
  // default the status says after each.
  let making = Promise.resolve();
  select.addEventListener("change", () => {
    const ref = select.value;
    making = making.then(() =>
      attempt(async () => {
        try {
          await api.post("/models/main", { model: ref });
          toast("ok", `New chats start on ${modelName(ref)}.`);
          await refresh();
        } finally {
          select.value = shown;
        }
      }),
    );
  });

  function paint(providers, mainModel) {
    const groups = providers
      .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools) }))
      .filter((group) => group.models.length > 0);
    const listed = groups.some((group) => group.models.some((model) => model.ref === mainModel));
    // The native replaceChildren writes a null as text: the option of a default not listed is left out instead.
    const unlisted = listed || mainModel === "" ? [] : [el("option", { value: mainModel }, `${modelName(mainModel)} (not listed now)`)];
    select.replaceChildren(
      ...unlisted,
      ...groups.map((group) =>
        el("optgroup", { label: providerLabel(group.provider) }, group.models.map((model) => el("option", { value: model.ref }, modelName(model.ref)))),
      ),
    );
    shown = mainModel;
    select.value = mainModel;
    element.hidden = false;
  }

  function hide() {
    element.hidden = true;
  }

  return { element, paint, hide };
}

function buildModels(isDisposed) {
  const list = el("div", null, inlineLoading());
  const main = defaultModelSetting();
  const body = el("div", null, main.element, list);
  let providers = null;
  const reload = el(
    "button",
    { class: "btn ghost sm", type: "button", title: "Ask the providers for their models again", "aria-label": "Ask again", onclick: () => attempt(load) },
    icon("refresh"),
    el("span", { class: "label" }, "Ask again"),
  );
  const card = foldCard({ id: "system-models", title: "Models", actions: reload, body, openByDefault: true });
  let painted = null;

  // Called on every status update; the tables are only rebuilt when the list, the default model or the providers the
  // configured models run on changed, so a menu that is open stays open.
  function paint() {
    if (providers === null) {
      return;
    }
    const status = getSnapshot().status;
    const mainModel = status === null ? "" : status.models.main;
    const inUse = providersInUse(status);
    const usage = inUse.join(" ");
    if (painted !== null && painted.providers === providers && painted.mainModel === mainModel && painted.usage === usage) {
      return;
    }
    painted = { providers, mainModel, usage };
    card.setSummary(...modelsSummary(providers, inUse));
    main.paint(providers, mainModel);
    list.replaceChildren(...providers.map((provider) => providerBlock(provider, mainModel, inUse.includes(provider.provider))));
    cueTables();
  }

  async function load() {
    forgetModels();
    painted = null;
    main.hide();
    list.replaceChildren(inlineLoading());
    card.setSummary();
    try {
      const found = await loadModels();
      if (isDisposed()) {
        return;
      }
      providers = found;
      paint();
    } catch (error) {
      if (!isDisposed()) {
        console.error("the models could not be listed", error);
        card.setSummary(el("span", { class: "badge b-bad" }, "could not list"));
        list.replaceChildren(inlineError(error, () => attempt(load)));
      }
    }
  }

  // On a phone the tables scroll sideways: a cue on each says while more lies to the right.
  let cues = [];
  function cueTables() {
    for (const stop of cues) {
      stop();
    }
    cues = [...list.querySelectorAll(".table-scroll")].map(scrollCue);
  }

  return { element: card.element, load, paint, dispose: cueTables };
}

function buildTokenCard() {
  async function forgetToken() {
    const confirmed = await confirmDialog({
      title: "Forget the access token?",
      message: "This browser signs out of Dot. Open the page again with dotx open to sign back in.",
      confirmLabel: "Forget it",
      danger: true,
      focusCancel: false,
    });
    if (confirmed) {
      clearToken();
      location.reload();
    }
  }

  return el(
    "section",
    { class: "card card-pad row" },
    el("div", { class: "grow" }, "This browser keeps the access token in its local storage, so you stay signed in."),
    el("button", { class: "btn", type: "button", onclick: () => attempt(forgetToken) }, "Forget the token here"),
  );
}

const BROWSER_MODES = [
  { id: "own", label: "Own Chrome", hint: "A Chrome profile of Dot's own, signed in to nothing" },
  { id: "attach", label: "My Chrome", hint: "The Chrome you have open, with your profiles and sign-ins" },
];

/**
 * Which Chrome the agent browses with. Switching to "My Chrome" needs Chrome to allow remote debugging, which only the
 * user can switch on; the card shows what Chrome allows right now and how to change it.
 */
function buildBrowser(isDisposed) {
  let view = null;
  const facts = el("dl", { class: "kv kv-pairs" }, inlineLoading());
  const problem = el("div", { class: "note bad", hidden: true }, icon("alert"), el("span"));
  const steps = el("ol", { class: "browser-steps" });
  const modes = segmented(BROWSER_MODES, BROWSER_MODES[0].id, (id) => attempt(() => choose(id)));
  const raise = switchSetting({
    label: "Bring Chrome to the front while working",
    hint:
      "Off, Dot's Chrome stays where it is while a run works and the app you were in keeps the focus; the run's page shows the page under Dot's computer either way, and Take over always brings the tab to the front.",
    read: () => view !== null && view.raise_window,
    write: async (on) => {
      view = await api.post("/browser/raise", { raise_window: on });
    },
    onToggled: () => {
      if (!isDisposed()) {
        raise.paint();
        toast("ok", view.raise_window ? "Chrome comes to the front while working." : "Chrome stays in the background.");
      }
    },
  });
  const reload = el(
    "button",
    { class: "btn ghost sm", type: "button", title: "Look at Chrome again", "aria-label": "Look again", onclick: () => attempt(load) },
    icon("refresh"),
    el("span", { class: "label" }, "Look again"),
  );

  function complain(message) {
    problem.lastChild.textContent = message;
    problem.hidden = false;
  }

  function copyStep() {
    return el(
      "li",
      null,
      "Open this address in Chrome and switch on “Allow remote debugging for this browser instance”:",
      el(
        "div",
        { class: "browser-copy" },
        el("code", { class: "code-line" }, view.inspect_page),
        el(
          "button",
          {
            class: "btn sm",
            type: "button",
            onclick: () =>
              attempt(async () => {
                await copyText(view.inspect_page);
                toast("ok", "Address copied.");
              }),
          },
          icon("copy"),
          "Copy",
        ),
      ),
    );
  }

  function paint() {
    modes.set(view.mode);
    raise.paint();
    const port = view.port === null ? "" : `, port ${view.port}`;
    facts.replaceChildren(
      entry("Chrome", view.chrome_running ? "running" : "not running"),
      entry("Remote debugging", view.remote_debugging ? `on${port}` : "off"),
      entry("Agent connection", view.connected ? "connected" : "not connected"),
      entry("Attach timeout", `${view.attach_timeout_s} s`),
    );
    steps.replaceChildren(
      copyStep(),
      el("li", null, "Chrome asks once whether Dot may connect; choose Allow."),
      el("li", null, "Choose My Chrome above, or run ", el("code", { class: "mono" }, "dotx browser attach"), " in a terminal."),
    );
  }

  async function load() {
    try {
      const found = await api.get("/browser");
      if (isDisposed()) {
        return;
      }
      view = found;
    } catch (error) {
      if (!isDisposed()) {
        facts.replaceChildren(inlineError(error, () => attempt(load)));
      }
      return;
    }
    problem.hidden = true;
    paint();
  }

  async function choose(id) {
    if (view === null || id === view.mode) {
      return;
    }
    let changed;
    try {
      changed = await api.post("/browser/mode", { mode: id });
    } catch (error) {
      if (!isDisposed()) {
        modes.set(view.mode);
        complain(error.message);
      }
      return;
    }
    if (isDisposed()) {
      return;
    }
    view = changed;
    problem.hidden = true;
    paint();
    toast("ok", `Browsing with ${BROWSER_MODES.find((mode) => mode.id === view.mode).label}.`);
  }

  const element = section(
    "Browser",
    reload,
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "Browse with"), modes.element),
        el(
          "div",
          { class: "hint" },
          "My Chrome opens tabs in the Chrome you already use, signed in as you are. Own Chrome keeps a profile apart from yours.",
        ),
      ),
      raise.element,
      facts,
      problem,
      el("div", { class: "stack tight" }, el("div", { class: "section-label" }, "To let Dot join your Chrome"), steps),
    ),
  );
  return { element, load };
}

/** The look of this browser, how it draws your attention, and the token it is signed in with. */
/** What Dot is called in the greeting of a new chat and at the head of a run, and what it calls you (item 72): both
 * preferences of this browser. */
function buildNameCard() {
  const owner = el("input", {
    class: "input name-input",
    type: "text",
    maxlength: String(OWNER_CHARS),
    autocomplete: "off",
    placeholder: "Your name",
    value: ownerName(),
    "aria-label": "Your name",
  });
  owner.addEventListener("change", () => {
    owner.value = setOwnerName(owner.value);
    toast("ok", owner.value === "" ? "The greeting says no name." : `The greeting says “${owner.value}”.`);
  });
  owner.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      owner.blur();
    }
  });
  const input = el("input", {
    class: "input name-input",
    type: "text",
    maxlength: String(NAME_CHARS),
    autocomplete: "off",
    placeholder: "Dot",
    value: dotName(),
    "aria-label": "Name",
  });
  input.addEventListener("change", () => {
    input.value = setDotName(input.value);
    toast("ok", `Here, Dot answers to “${dotName()}”.`);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  return section(
    "Name",
    null,
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "What Dot is called"), input),
        el("div", { class: "hint" }, "In the greeting of a new chat and at the head of a run. Kept in this browser; the daemon and the model never see it."),
      ),
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "What Dot calls you"), owner),
        el("div", { class: "hint" }, "In the greeting of the home (“Good morning, Ada.”). Kept in this browser too; blank greets without a name."),
      ),
    ),
  );
}

/** Dot's own face (item 102): the picker and Save, kept by the daemon for every browser. */
function buildDotLookCard() {
  const picker = lookPicker({ look: lookOf(DOT_ID, null), uid: "dot" });
  const save = el("button", { class: "btn primary", type: "button" }, icon("check"), "Save the look");
  save.addEventListener("click", () =>
    attempt(async () => {
      save.disabled = true;
      try {
        const record = await api.put(`/agents/${DOT_ID}/look`, picker.value());
        rememberLook(DOT_ID, record.look);
        toast("ok", "Dot's look saved.");
      } finally {
        save.disabled = false;
      }
    }),
  );
  const element = el(
    "section",
    { class: "card", "aria-label": "Dot's look" },
    el("div", { class: "card-head" }, el("span", null, "Dot's look")),
    el("div", { class: "card-pad stack" }, picker.element, el("div", { class: "cluster end" }, save)),
  );
  return { element, dispose: picker.dispose };
}

export function mountGeneral(root, ctx) {
  const look = buildDotLookCard();
  root.append(
    paneHead("What Dot is called and calls you, its own face, how it draws your attention, the composer's keys, and the token this browser is signed in with.", null),
    buildNameCard(),
    look.element,
    buildNotifications(),
    buildComposerCard(),
    buildTokenCard(),
  );
  return look.dispose;
}

/** The providers Dot can reach and the models each of them offers. */
export function mountModels(root, ctx) {
  let disposed = false;
  const models = buildModels(() => disposed);
  root.append(paneHead("The providers Dot can reach, the models each of them offers and the reasoning effort of each.", null), models.element);
  const stop = onChange(() => models.paint());
  models.paint();
  attempt(refresh);
  attempt(models.load);
  return () => {
    disposed = true;
    stop();
    models.dispose();
  };
}

/** Which Chrome the agent browses with. */
export function mountBrowser(root, ctx) {
  let disposed = false;
  const browser = buildBrowser(() => disposed);
  root.append(paneHead("Which Chrome the agent browses with, and what Chrome has to allow first.", null), browser.element);
  attempt(browser.load);
  return () => {
    disposed = true;
  };
}

/** The daemon, its emergency stop and its health checks. */
export function mountSystem(root, ctx) {
  let disposed = false;
  const daemon = buildDaemon();
  const failsafe = buildFailsafe();
  const doctor = buildDoctor(() => disposed);

  function paint(snapshot) {
    daemon.paint(snapshot);
    failsafe.paint(snapshot);
  }

  root.append(paneHead("The daemon, its emergency stop and its health checks.", null), daemon.element, failsafe.element, doctor.element);
  const stop = onChange(paint);
  paint(getSnapshot());
  attempt(refresh);
  attempt(doctor.run);
  return () => {
    disposed = true;
    stop();
  };
}
