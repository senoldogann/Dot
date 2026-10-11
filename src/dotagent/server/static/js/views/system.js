// The system page: how the daemon is doing, the emergency stop, the health checks and the models each provider offers.

import { DOT_ID, facesMove, lookOf, rememberLook, setFacesMove } from "../actors.js";
import { api, clearToken } from "../api.js";
import {
  notificationsAvailable,
  notificationsBlocked,
  notificationsOn,
  setNotifications,
  setTellEvenWhenLooking,
  tellEvenWhenLooking,
} from "../attention.js";
import { compact, copyText, dateTimeFull, duration, el, plural } from "../dom.js";
import { offersChoice } from "../efforts.js";
import { engageFailsafe, releaseFailsafe } from "../failsafe.js";
import { dotName, NAME_CHARS, setDotName } from "../face.js";
import { OWNER_CHARS, ownerName, setOwnerName } from "../presence.js";
import { icon } from "../icons.js";
import { lookPicker } from "../lookpicker.js";
import { chooseDensity, currentDensity, DENSITIES, followTheme, foldSteps, hasOwnLayout, setFoldSteps, setShowInspector, showInspector } from "../layout.js";
import { foldCard, foldGroup, inlineError, inlineLoading, paneHead, segmented } from "../panels.js";
import { providerLabel, providerOf } from "../providers.js";
import { reasoningSelect } from "../reasoning.js";
import { approvalLabel } from "../runmodes.js";
import { scrollCue } from "../scrollcue.js";
import { forgetModels, getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { SCHEMES, THEMES, chooseScheme, chooseTheme, currentScheme, currentTheme, onThemeChange, themeInUse } from "../theme.js";
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
    wideEntry("Home", el("span", { class: "mono" }, status.home)),
  ];
}

/**
 * A labelled on/off switch; `write` may be asynchronous. `paint()` shows the current `read()` again after something else
 * changed it; `toggle` and `note` are the button and the hint line, for settings that depend on more than their value.
 */
function switchSetting({ label, hint, read, write, onToggled }) {
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": "false",
    "aria-label": label,
    onclick: () =>
      attempt(async () => {
        await write(!read());
        onToggled();
      }),
  });
  const note = el("div", { class: "hint" }, hint);
  const element = el("div", { class: "setting" }, el("div", { class: "row" }, el("span", { class: "grow" }, label), toggle), note);
  return {
    element,
    toggle,
    note,
    paint: () => toggle.setAttribute("aria-checked", String(read())),
  };
}

/** The look and the layout choices. Returns the card and the function that stops it listening for theme changes. */
function buildAppearance() {
  const detail = el("div", { class: "hint" });
  const layoutNote = el("div", { class: "hint grow" });

  function describe(id) {
    const theme = THEMES.find((candidate) => candidate.id === id);
    detail.textContent = `${theme.hint} The choice is kept in this browser.`;
  }

  const fold = switchSetting({
    label: "Fold tool steps",
    hint: "The tool calls of a turn collapse into one “Worked for…” line; open it to see every step.",
    read: foldSteps,
    write: setFoldSteps,
    onToggled: paintLayout,
  });
  const inspector = switchSetting({
    label: "Details panel",
    hint: "On wide windows the run page shows its details, plan and screenshots in a panel beside the conversation.",
    read: showInspector,
    write: setShowInspector,
    onToggled: paintLayout,
  });
  // The faces that move (item 103): the home's, the run page's and the previews; off leaves every face still.
  const faces = switchSetting({
    label: "Faces that move",
    hint: "Dot's face on the home and the face beside a chat move while they work and rest; off, every face is a still frame.",
    read: facesMove,
    write: setFacesMove,
    onToggled: () => faces.paint(),
  });
  const reset = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      onclick: () => {
        followTheme();
        paintLayout();
      },
    },
    "Follow the look",
  );

  function paintLayout() {
    fold.paint();
    inspector.paint();
    faces.paint();
    reset.disabled = !hasOwnLayout();
    layoutNote.textContent = hasOwnLayout()
      ? "You set these two yourself; they stay as they are when you change the look."
      : `These two follow the ${themeInUse().label} look until you change one of them.`;
  }

  const options = THEMES.map((theme) => ({ id: theme.id, label: theme.label, hint: theme.hint }));
  const choice = segmented(options, currentTheme(), chooseTheme);
  const scheme = segmented(
    SCHEMES.map((option) => ({ id: option.id, label: option.label, hint: option.hint })),
    currentScheme(),
    chooseScheme,
  );
  scheme.element.setAttribute("aria-label", "Colour scheme");
  const density = segmented(
    DENSITIES.map((option) => ({ id: option.id, label: option.label, hint: option.hint })),
    currentDensity(),
    chooseDensity,
  );
  density.element.setAttribute("aria-label", "Density");
  const stopListening = onThemeChange((id) => {
    choice.set(id);
    describe(id);
    paintLayout();
  });
  describe(currentTheme());
  paintLayout();
  const element = section(
    "Appearance",
    null,
    el(
      "div",
      { class: "card-pad stack" },
      el("div", { class: "setting" }, el("div", { class: "row" }, el("span", { class: "grow" }, "Look"), choice.element), detail),
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "Colour scheme"), scheme.element),
        el("div", { class: "hint" }, "System follows light and dark as your system is; Light or Dark holds one. Kept in this browser."),
      ),
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "Density"), density.element),
        el("div", { class: "hint" }, "How close the rows, cards and lists sit. Kept in this browser."),
      ),
      fold.element,
      inspector.element,
      el("div", { class: "setting" }, el("div", { class: "row" }, layoutNote, reset)),
      faces.element,
    ),
  );
  return { element, dispose: stopListening };
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
  );
}

/** The reasoning level of a model, or a dash when it has fewer than two levels and so nothing to choose. */
function reasoningControl(model) {
  if (!offersChoice(model)) {
    return el("span", { class: "faint", title: "This model has no reasoning levels to choose from" }, NOT_SET);
  }
  return reasoningSelect(model, `Reasoning level of ${model.ref}`);
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
    el("td", { class: "muted" }, model.context_length === null ? NOT_SET : `${compact(model.context_length)} tokens`),
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

function problemBadge(provider, inUse) {
  if (provider.error === null) {
    return null;
  }
  if (provider.problem === "no_key") {
    return el("span", { class: inUse ? "badge b-bad" : "badge b-muted" }, "no key");
  }
  return el("span", { class: "badge b-bad" }, provider.problem === "key_refused" ? "key refused" : "unavailable");
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

function buildModels(isDisposed) {
  const body = el("div", null, inlineLoading());
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
    body.replaceChildren(...providers.map((provider) => providerBlock(provider, mainModel, inUse.includes(provider.provider))));
    cueTables();
  }

  async function load() {
    forgetModels();
    painted = null;
    body.replaceChildren(inlineLoading());
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
        body.replaceChildren(inlineError(error, () => attempt(load)));
      }
    }
  }

  // On a phone the tables scroll sideways: a cue on each says while more lies to the right.
  let cues = [];
  function cueTables() {
    for (const stop of cues) {
      stop();
    }
    cues = [...body.querySelectorAll(".table-scroll")].map(scrollCue);
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
  const appearance = buildAppearance();
  const look = buildDotLookCard();
  root.append(
    paneHead("The look of this browser, Dot's own face, how it draws your attention, and the token it is signed in with.", null),
    appearance.element,
    buildNameCard(),
    look.element,
    buildNotifications(),
    buildTokenCard(),
  );
  return () => {
    appearance.dispose();
    look.dispose();
  };
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
