// The system page: how the daemon is doing, the emergency stop, the health checks and the models each provider offers.

import { DOT_ID, lookOf, rememberLook } from "../actors.js";
import { api, clearToken } from "../api.js";
import { AUTO_LINES, BUSY_SEND, SEND_KEY, setValue, valueOf } from "../appearance/settings.js";
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
    entry("Fast model", orDash(status.models.fast)),
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

/** What a chat shows of the actions that ran without asking you (item 346), kept in this browser. */
function buildChatCard() {
  const lines = switchSetting({
    label: "Show what ran without asking",
    hint: "A line under the step for each action YOLO let through without asking you, or the reviewer approved. Off, the chat leaves them out; a refusal still shows. Kept in this browser.",
    read: () => valueOf(AUTO_LINES) === "shown",
    write: async (on) => setValue(AUTO_LINES, on ? "shown" : "hidden"),
    onToggled: () => lines.paint(),
  });
  lines.paint();
  return section("Chat", null, el("div", { class: "card-pad stack" }, lines.element));
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
  // Item 231: whether the daemon's next start resumes on its own the runs it cut off, kept in the config file.
  let resume = null;
  const goOn = switchSetting({
    label: "Go on after a restart",
    hint: "",
    read: () => resume !== null && resume.resume === "safe",
    write: async (on) => {
      resume = await api.put("/agent/resume", { resume: on ? "safe" : "off" });
      toast("ok", on ? "From the daemon's next start, the runs it cut off go on by themselves." : "From the daemon's next start, the runs it cut off wait for you.");
      await refresh();
    },
    onToggled: () => paintResume(),
  });
  goOn.element.hidden = true;

  function paintResume() {
    goOn.paint();
    if (resume !== null) {
      const minutes = Math.round(resume.resume_within_s / 60);
      goOn.note.textContent = `When the daemon starts again, a run you started here that its stop or a crash cut off in the last ${minutes} min goes on by itself, as long as every call it cut off only reads; any other run waits for you to continue it. Kept in the config file as [agent] resume.`;
    }
  }

  return {
    element: section("Daemon", refreshButton, el("div", null, body, el("div", { class: "card-pad" }, goOn.element))),
    paint(snapshot) {
      if (snapshot.status !== null) {
        body.replaceChildren(el("dl", { class: "kv" }, statusEntries(snapshot.status)));
        resume = snapshot.status.resume;
        goOn.element.hidden = false;
        paintResume();
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
    model.learns ? el("span", { class: "chip", title: "Its provider may use what it is sent to improve its models" }, "may train on your data") : null,
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

// The days of runs the context evidence of a model is drawn from (routes_system.py).
const EVIDENCE_DAYS = 30;
// Where a model's window comes from (llm/types.py WindowSource), as the context facts say it.
const WINDOW_SOURCES = {
  provider: (provider) => `${providerLabel(provider)} gives it for the model.`,
  published: (provider) => `${providerLabel(provider)} publishes it; Dot keeps it in its table of models.`,
  assumed: (provider) => `${providerLabel(provider)} does not say it, so Dot assumes it.`,
  asked: () => "It is the window Dot asks Ollama to give the model: [providers.ollama] num_ctx, or num_ctx_cloud for a cloud model.",
};

function tokens(count) {
  return count.toLocaleString("en-GB");
}

/** The sizes a model's context may be given (item 250): Dot's default first, then the others, the largest marked. */
function sizeOptions(context) {
  return [
    el("option", { value: "" }, `${compact(context.default)} (default)`),
    ...context.sizes.map((size) => el("option", { value: String(size) }, size === context.ceiling ? `${compact(size)} (most)` : compact(size))),
  ];
}

/** What a model's context size does and where its numbers come from (item 250). */
function contextFacts(model) {
  const context = model.context;
  const provider = providerOf(model.ref);
  const own = model.own_context_length === null ? "" : ` The model's own window is ${tokens(model.own_context_length)} tokens; a larger num_ctx gives it more of it.`;
  const cut = context.default < context.configured ? `; three quarters of this model's window cut it to ${tokens(context.default)}` : "";
  return [
    el(
      "p",
      null,
      "Each step sends the model the whole run again: its instructions, its tools, the request and all that was done since. ",
      `Once a step sends more than ${tokens(context.summarise_at)} tokens, Dot summarizes the run's older part, so the run stays within its ${tokens(context.tokens)}.`,
    ),
    el(
      "p",
      null,
      "A larger size means fewer summaries, and each summary drops detail of the older part and takes a call of the fast model. ",
      "But every step then sends more, so it is answered more slowly and costs more on a key that is billed by the token. A smaller size works the other way round.",
    ),
    el(
      "dl",
      { class: "context-facts" },
      el("dt", null, "Window"),
      el(
        "dd",
        null,
        model.context_length === null
          ? `${providerLabel(provider)} does not say how many tokens the model holds, so no size can be chosen for it.`
          : `${tokens(model.context_length)} tokens. ${WINDOW_SOURCES[model.window_source](provider)}${own}`,
      ),
      el("dt", null, "Largest size"),
      el(
        "dd",
        null,
        context.ceiling === null
          ? NOT_SET
          : `${tokens(context.ceiling)} tokens, three quarters of the window: the rest is room for the reply and for the error of Dot's count of the tokens.`,
      ),
      el("dt", null, "Dot's default"),
      el(
        "dd",
        null,
        `${tokens(context.configured)} tokens, for every model without a size of its own${cut}. `,
        "Dot comes with 96,000: in a run of 41 steps it measured, 48,000 summarized twice and 96,000 not once, while each step sent a quarter more. ",
        el("span", { class: "faint" }, "In the config file: [agent] context_tokens."),
      ),
      el("dt", null, "Smallest size"),
      el("dd", null, `${tokens(context.floor)} tokens: a run's first step alone sends its instructions, its tools and the request.`),
    ),
    // The native replaceChildren writes a null as text: the note of a window too small for any size is left out instead.
    ...(context.sizes.length === 0 && context.ceiling !== null
      ? [el("p", { class: "hint" }, "Three quarters of this window are below the smallest size, so its runs keep Dot's default; for an Ollama model a larger num_ctx gives it more.")]
      : []),
  ];
}

/** The seconds of a step by its input, as a small table: the buckets the model's steps fell in. */
function bucketTable(buckets) {
  const rows = buckets.map((bucket) => {
    const label = bucket.up_to === null ? `over ${compact(bucket.above)}` : bucket.above === 0 ? `up to ${compact(bucket.up_to)}` : `${compact(bucket.above)} to ${compact(bucket.up_to)}`;
    return el("tr", null, el("td", null, label), el("td", null, tokens(bucket.steps)), el("td", null, `${bucket.seconds_median.toFixed(1)} s`));
  });
  return el(
    "table",
    { class: "table context-buckets" },
    el("thead", null, el("tr", null, el("th", null, "A step's input"), el("th", null, "Steps"), el("th", null, "Seconds at the median"))),
    el("tbody", null, rows),
  );
}

/** What the model's runs of the last 30 days measured (GET /api/models/context). */
function evidenceFacts(evidence) {
  if (evidence.steps === 0) {
    return [el("p", { class: "hint" }, `Nothing is measured yet: no step of this model answered in the last ${EVIDENCE_DAYS} days.`)];
  }
  const sent = `A step sent ${tokens(evidence.input_median)} tokens at the median, ${tokens(evidence.input_p95)} or fewer in 95% of steps and ${tokens(evidence.input_max)} at most.`;
  const cached = evidence.cached_share === null ? "" : ` ${Math.round(evidence.cached_share * 100)}% of it the provider served from its cache.`;
  const first = evidence.first_step_median === null ? "" : ` A run's first step sent ${tokens(evidence.first_step_median)} at the median.`;
  const summaries =
    evidence.compactions === 0
      ? "No run was summarized."
      : `${plural(evidence.compactions, "summary", "summaries")} in ${plural(evidence.compacted_runs, "run", "runs")}.`;
  return [
    el("p", null, `${plural(evidence.steps, "step", "steps")} in ${plural(evidence.runs, "run", "runs")}. ${sent}${cached}${first}`),
    el("p", null, summaries),
    bucketTable(evidence.buckets),
  ];
}

/**
 * The context size of a model's runs (item 250): a menu of the sizes it may be given, Dot's default first, beside its
 * window, and a Why button that opens a row under the model's with what the size does and what the model's runs of the
 * last 30 days measured. A model whose window allows no other size shows its budget as text. A choice holds from the
 * model's next run; the menu shows what the daemon answered, and `remember(model)` keeps it for the models' list.
 */
function contextControl(initial, remember) {
  let model = initial;
  const name = modelName(model.ref);
  // Every other character spelled out by its code, so that two references never share an id.
  const whyId = `context-why-${[...model.ref].map((char) => (/[A-Za-z0-9-]/.test(char) ? char : `_${char.codePointAt(0).toString(16)}_`)).join("")}`;
  const select = el("select", { class: "select sm", "aria-label": `Context size of ${model.ref}` });
  const why = el("button", { class: "btn ghost sm", type: "button", "aria-expanded": "false", "aria-controls": whyId, "aria-label": `Why: the context size of ${model.ref}` }, "Why");
  const evidence = el("div", { class: "context-evidence" });
  const facts = el("div", { class: "context-why-facts" });
  const body = el("div", { class: "context-why-body" }, el("h4", null, `Context size of ${name}`), facts, el("h4", null, `This model's runs, last ${EVIDENCE_DAYS} days`), evidence);
  const row = el("tr", { class: "context-why", id: whyId, hidden: true }, el("td", { colspan: "4" }, body));
  const text = el("span", null);
  const windowText = el("span", { class: "muted" });
  const cell = el("td", { class: "context-cell" }, el("span", { class: "row" }, select, text, windowText, why));

  function paint() {
    const context = model.context;
    const choosable = context.sizes.length > 0;
    select.hidden = !choosable;
    text.hidden = choosable;
    if (choosable) {
      select.replaceChildren(...sizeOptions(context));
      select.value = context.chosen === null ? "" : String(context.chosen);
    }
    text.textContent = compact(context.tokens);
    // The window, and the model's own beside it when Dot asks Ollama for less of it (item 221).
    const own = model.context_length === null || model.own_context_length === null ? null : model.own_context_length;
    windowText.textContent = model.context_length === null ? "" : own === null ? `of ${compact(model.context_length)}` : `of ${compact(model.context_length)} (own ${compact(own)})`;
    windowText.title = own === null ? "" : `Dot asks for ${tokens(model.context_length)} tokens ([providers.ollama] num_ctx or num_ctx_cloud); the model takes ${tokens(own)}.`;
    facts.replaceChildren(...contextFacts(model));
  }

  async function loadEvidence() {
    evidence.replaceChildren(inlineLoading());
    try {
      const found = await api.get(`/models/context?model=${encodeURIComponent(model.ref)}`);
      evidence.replaceChildren(...evidenceFacts(found));
    } catch (error) {
      evidence.replaceChildren(inlineError(error, () => attempt(loadEvidence)));
    }
  }

  why.addEventListener("click", () => {
    const open = row.hidden;
    row.hidden = !open;
    why.setAttribute("aria-expanded", String(open));
    if (open) {
      attempt(loadEvidence);
    }
  });

  // Each choice is sent once the one before it was answered, so the last one made holds.
  let making = Promise.resolve();
  select.addEventListener("change", () => {
    const size = select.value === "" ? null : Number(select.value);
    making = making.then(() =>
      attempt(async () => {
        try {
          model = await api.put("/models/context", { model: model.ref, tokens: size });
          remember(model);
          forgetModels();
          toast("ok", size === null ? `${name} gets Dot's default context size from its next run.` : `${name} gets a context size of ${tokens(size)} tokens from its next run.`);
        } finally {
          paint();
        }
      }),
    );
  });

  paint();
  return { cell, row };
}

// OpenCode's agent keeps its own window and summarizes it by itself (item 283): Dot's context size does not reach it.
function ownWindow() {
  return { cell: el("td", { class: "hint" }, "OpenCode's own"), row: null };
}

function modelRows(model, mainModel, remember) {
  const context = providerOf(model.ref) === "opencode" ? ownWindow() : contextControl(model, remember);
  return [
    el(
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
      context.cell,
    ),
    context.row,
  ];
}

function modelTable(provider, mainModel, remember) {
  return el(
    "div",
    { class: "table-scroll models-scroll" },
    el(
      "table",
      { class: "table wide" },
      el("thead", null, el("tr", null, el("th", null, "Model"), el("th", null, "Can"), el("th", null, "Reasoning"), el("th", null, "Context"))),
      el("tbody", null, provider.models.map((model) => modelRows(model, mainModel, remember))),
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

// The problems that are the usual state of a provider the owner does not use: no key, or no program to run it on.
const QUIET_PROBLEMS = new Set(["no_key", "not_installed"]);

/** Whether a provider that did not answer is a problem: one without a key or a program is not, unless a configured
 * model runs on it. */
function isFailing(provider, inUse) {
  return provider.error !== null && (!QUIET_PROBLEMS.has(provider.problem) || inUse);
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
  if (provider.problem === "not_installed") {
    return el(
      "div",
      { class: inUse ? "note bad" : "note" },
      icon("download"),
      el("span", null, provider.error),
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

function providerBody(provider, mainModel, inUse, remember) {
  if (provider.error !== null) {
    return problemNote(provider, inUse);
  }
  if (provider.models.length === 0) {
    return el("div", { class: "hint" }, "This provider lists no models.");
  }
  return modelTable(provider, mainModel, remember);
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
  if (provider.problem === "not_installed") {
    return el("span", { class: inUse ? "badge b-bad" : "badge b-muted" }, "not installed");
  }
  return el("span", { class: "badge b-bad" }, PROBLEM_BADGES[provider.problem] ?? "unavailable");
}

/** One provider as a fold: open when it owns the default model or fails, so the cause is in sight. */
function providerBlock(provider, mainModel, inUse, remember) {
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
    body: providerBody(provider, mainModel, inUse, remember),
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

// The models of Dot's own work (item 232), which Settings › Models chooses under the default one: what each does, and
// the models that can do it.
const ROLE_SETTINGS = [
  {
    role: "fast",
    label: "Fast model",
    hint: "Writes the summary of a long run first (the run's own model does when it cannot) and reads pages for the facts asked of them.",
    fits: (model) => model.tools,
  },
  {
    role: "review",
    label: "Review model",
    hint: "Checks runs (their goal and a second opinion), draws their lessons and skills, and drafts rules from your words.",
    fits: (model) => model.tools,
  },
  {
    role: "vision",
    label: "Vision model",
    hint: "Reads images and screenshots for a model that cannot see them.",
    fits: (model) => model.vision,
  },
];

/**
 * A model that Settings › Models chooses: the default one (item 199), the one a run that names none starts on, a new
 * chat's among them, or one of Dot's own work (item 232). It offers the models that `fits` (the default one, as the
 * composer does, and the fast and review models call tools; the vision model sees), and the one chosen now, listed or
 * not; `choose(ref)` makes the choice. `paint(providers, chosen)` fills it again; the select stays, and so does the
 * focus on it.
 */
function modelSetting({ label, hint, className, fits, choose }) {
  let shown = "";
  const select = el("select", { class: "select sm", "aria-label": label });
  const element = el(
    "div",
    { class: `setting ${className}`, hidden: true },
    el("div", { class: "row" }, el("span", { class: "grow" }, label), select),
    el("div", { class: "hint" }, hint),
  );
  // Each pick is sent once the one before it was answered, so the last one made holds; the select shows the model the
  // status says after each.
  let making = Promise.resolve();
  select.addEventListener("change", () => {
    const ref = select.value;
    making = making.then(() =>
      attempt(async () => {
        try {
          await choose(ref);
          await refresh();
        } finally {
          select.value = shown;
        }
      }),
    );
  });

  function paint(providers, chosen) {
    const groups = providers
      .map((provider) => ({ provider: provider.provider, models: provider.models.filter(fits) }))
      .filter((group) => group.models.length > 0);
    const listed = groups.some((group) => group.models.some((model) => model.ref === chosen));
    // The native replaceChildren writes a null as text: the option of a model not listed is left out instead.
    const unlisted = listed || chosen === "" ? [] : [el("option", { value: chosen }, `${modelName(chosen)} (not listed now)`)];
    select.replaceChildren(
      ...unlisted,
      ...groups.map((group) =>
        el("optgroup", { label: providerLabel(group.provider) }, group.models.map((model) => el("option", { value: model.ref }, modelName(model.ref)))),
      ),
    );
    shown = chosen;
    select.value = chosen;
    element.hidden = false;
  }

  function hide() {
    element.hidden = true;
  }

  return { element, paint, hide };
}

function buildModels(isDisposed) {
  const list = el("div", null, inlineLoading());
  const main = modelSetting({
    label: "Default model",
    hint: "New chats start on it, and so does any run that names no model. A chat keeps the model it has; Default in its composer follows this one.",
    className: "models-default",
    fits: (model) => model.tools,
    choose: async (ref) => {
      await api.post("/models/main", { model: ref });
      toast("ok", `New chats start on ${modelName(ref)}.`);
    },
  });
  const roles = ROLE_SETTINGS.map((setting) => ({
    role: setting.role,
    control: modelSetting({
      label: setting.label,
      hint: setting.hint,
      className: "models-role",
      fits: setting.fits,
      choose: async (ref) => {
        await api.post("/models/roles", { role: setting.role, model: ref });
        toast("ok", `The ${setting.label.toLowerCase()} is ${modelName(ref)} from its next call.`);
      },
    }),
  }));
  const body = el("div", null, main.element, ...roles.map(({ control }) => control.element), list);
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
    const chosen = roles.map(({ role }) => (status === null ? "" : status.models[role])).join(" ");
    const inUse = providersInUse(status);
    const usage = inUse.join(" ");
    if (painted !== null && painted.providers === providers && painted.mainModel === mainModel && painted.chosen === chosen && painted.usage === usage) {
      return;
    }
    painted = { providers, mainModel, chosen, usage };
    card.setSummary(...modelsSummary(providers, inUse));
    main.paint(providers, mainModel);
    for (const { role, control } of roles) {
      control.paint(providers, status === null ? "" : status.models[role]);
    }
    list.replaceChildren(...providers.map((provider) => providerBlock(provider, mainModel, inUse.includes(provider.provider), remember)));
    cueTables();
  }

  // A context size chosen in a model's row (item 250): the list keeps the model as the daemon answered, so the tables
  // built again later show it; the rows built now show it already.
  function remember(updated) {
    providers = providers.map((provider) => ({ ...provider, models: provider.models.map((model) => (model.ref === updated.ref ? updated : model)) }));
    if (painted !== null) {
      painted = { ...painted, providers };
    }
  }

  async function load() {
    forgetModels();
    painted = null;
    main.hide();
    for (const { control } of roles) {
      control.hide();
    }
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

  // On a phone the tables scroll sideways: a cue on each says while more lies to the right. Leaving the page stops
  // them all, and starts none.
  let cues = [];
  function stopCues() {
    for (const stop of cues) {
      stop();
    }
    cues = [];
  }
  function cueTables() {
    stopCues();
    cues = [...list.querySelectorAll(".table-scroll")].map(scrollCue);
  }

  return { element: card.element, load, paint, dispose: stopCues };
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
  { id: "attach", label: "My Chrome", hint: "Your Chrome, with your profiles and sign-ins; Dot opens it when it is closed" },
];

/**
 * Which Chrome the agent browses with. Switching to "My Chrome" needs Chrome to allow remote debugging, which only the
 * user can switch on; the card shows what Chrome allows right now and how to change it. A Chrome that is closed is
 * opened by the switch (item 261), and while the switch is under way the card says what it waits for. The DevTools
 * port (item 265) has Dot's own Chrome take connections from a run's scripts too. With Dot's own Chrome the card opens
 * its profile in a plain Chrome window, where the owner signs in to sites (item 344).
 */
// How often the browser card reads again how the Chrome of the user stands, while it is open (item 341).
const BROWSER_POLL_MS = 3000;

/** How Dot stands with the Chrome of the user (item 341): what the card's connection says, and the one line that says
 * what to do, with its tone; null in Dot's own Chrome. */
function attachStanding(view) {
  if (view.mode !== "attach") {
    return null;
  }
  if (view.connected) {
    return { word: "connected", line: "Dot is connected to your Chrome; later runs connect with no click.", tone: "ok" };
  }
  if (view.waiting_allow) {
    return { word: "waiting for Allow", line: "Chrome asks whether Dot may connect: choose Allow in Chrome.", tone: "warn" };
  }
  if (!view.chrome_running) {
    return { word: "not connected", line: "Chrome is closed: open it, and Dot connects on its next step.", tone: "warn" };
  }
  if (!view.remote_debugging) {
    return { word: "not connected", line: `Remote debugging is off in Chrome: switch it on at ${view.inspect_page}.`, tone: "warn" };
  }
  if (view.lost) {
    return { word: "Chrome restarted", line: "Chrome was closed or restarted: the next step connects again, and Chrome asks you to Allow.", tone: "warn" };
  }
  return { word: "not connected", line: "Dot connects on the next step that needs Chrome; Chrome asks you to Allow once.", tone: "" };
}

function buildBrowser(isDisposed) {
  let view = null;
  let polling = null;
  // Whether the card last saw the sign-in window open, so that its closing is told once.
  let signing = false;
  const facts = el("dl", { class: "kv kv-pairs" }, inlineLoading());
  const standing = el("div", { class: "note attach-standing", "aria-live": "polite", hidden: true }, icon("plug"), el("span"));
  const problem = el("div", { class: "note bad", hidden: true }, icon("alert"), el("span"));
  const waiting = el(
    "div",
    { class: "note", role: "status", hidden: true },
    icon("clock"),
    el("span", null, "Connecting to your Chrome; Dot opens it when it is closed. When Chrome asks whether Dot may connect, choose Allow."),
  );
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
  const port = el("input", {
    class: "input port-input mono",
    type: "text",
    inputmode: "numeric",
    maxlength: "5",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "0",
    disabled: true,
    "aria-label": "DevTools port",
  });
  port.addEventListener("change", () => attempt(() => choosePort(port.value)));
  port.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      port.blur();
    }
  });
  const portHint = el("div", { class: "hint" });
  const signIn = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      title: "Sign in to sites in Dot's Chrome",
      "aria-label": "Sign in…",
      onclick: () => attempt(openSignIn),
    },
    icon("key"),
    el("span", { class: "label" }, "Sign in…"),
  );
  const signInSetting = el(
    "div",
    { class: "setting", hidden: true },
    el("div", { class: "row" }, el("span", { class: "grow" }, "Sign in to sites in Dot's Chrome"), signIn),
    el(
      "div",
      { class: "hint" },
      "Opens Dot's own profile in a plain Chrome window: sign in there to the sites Dot should use, then quit that Chrome (⌘Q). Dot keeps the cookies and never sees your passwords.",
    ),
  );
  const signingIn = el(
    "div",
    { class: "note signing-in", "aria-live": "polite", hidden: true },
    icon("key"),
    el("span", null, "Chrome is open on Dot's profile: sign in there, then quit that Chrome (⌘Q). Until then runs cannot use Dot's Chrome."),
  );
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

  // The port as the daemon has it, and where a run's scripts reach Dot's Chrome, or why they do not.
  function paintPort() {
    port.value = String(view.debug_port);
    port.disabled = false;
    if (view.devtools_address !== null) {
      portHint.replaceChildren(
        "A run's scripts drive Dot's Chrome at ",
        el("code", { class: "mono" }, view.devtools_address),
        " with the Chrome DevTools Protocol, and the run is told so. 0 turns it off.",
      );
    } else if (view.debug_port !== 0) {
      portHint.textContent =
        "Runs that work in Dot's own Chrome, such as a campaign's, are told this port; with My Chrome, scripts reach your Chrome through its own remote debugging. 0 turns it off.";
    } else {
      portHint.textContent =
        "A port of this Mac, from 1024 up, on which Dot's own Chrome also takes DevTools connections, so a run's scripts can drive it (Playwright, Puppeteer). It asks for no token: any program on this Mac can use it while Dot's Chrome runs. Dot's Chrome does not start while another program holds the port.";
    }
  }

  async function choosePort(text) {
    if (view === null) {
      return;
    }
    const wanted = text.trim() === "" ? 0 : Number(text.trim());
    if (!Number.isInteger(wanted) || (wanted !== 0 && (wanted < 1024 || wanted > 65535))) {
      paintPort();
      toast("error", "A port is a whole number from 1024 to 65535, or 0 for none.");
      return;
    }
    if (wanted === view.debug_port) {
      paintPort();
      return;
    }
    port.disabled = true;
    let changed;
    try {
      changed = await api.post("/browser/debug-port", { debug_port: wanted });
    } catch (error) {
      if (!isDisposed()) {
        paintPort();
      }
      throw error;
    }
    if (isDisposed()) {
      return;
    }
    view = changed;
    paint();
    toast("ok", wanted === 0 ? "Dot's Chrome takes no DevTools connections." : `Dot's Chrome takes DevTools connections on port ${wanted} from its next start.`);
  }

  function paint() {
    modes.set(view.mode);
    raise.paint();
    paintPort();
    signInSetting.hidden = view.mode !== "own";
    signIn.disabled = view.signing_in;
    signingIn.hidden = !view.signing_in;
    if (signing && !view.signing_in) {
      toast("ok", "Chrome closed; the sign-ins are kept in Dot's profile.");
    }
    signing = view.signing_in;
    const port = view.port === null ? "" : `, port ${view.port}`;
    const stands = attachStanding(view);
    facts.replaceChildren(
      entry("Chrome", view.chrome_running ? "running" : "not running"),
      entry("Remote debugging", view.remote_debugging ? `on${port}` : "off"),
      entry("Agent connection", stands === null ? (view.connected ? "connected" : "not connected") : stands.word),
      entry("Attach timeout", `${view.attach_timeout_s} s`),
    );
    standing.hidden = stands === null;
    if (stands !== null) {
      standing.className = stands.tone === "" ? "note attach-standing" : `note attach-standing ${stands.tone}`;
      standing.lastChild.textContent = stands.line;
    }
    steps.replaceChildren(
      copyStep(),
      el("li", null, "Chrome asks once whether Dot may connect; choose Allow."),
      el("li", null, "Choose My Chrome above (Dot opens Chrome when it is closed), or run ", el("code", { class: "mono" }, "dotx browser attach"), " in a terminal while Chrome is open."),
    );
  }

  // Read how Chrome stands now and paint it; false when the card is gone or the read failed, whose error it shows.
  async function read() {
    try {
      const found = await api.get("/browser");
      if (isDisposed()) {
        return false;
      }
      view = found;
    } catch (error) {
      if (!isDisposed()) {
        facts.replaceChildren(inlineError(error, () => attempt(load)));
      }
      return false;
    }
    paint();
    return true;
  }

  async function load() {
    if (await read()) {
      problem.hidden = true;
    }
    keepReading();
  }

  // With My Chrome the card reads again how Chrome stands while it is open, so a restart of Chrome, its Allow and the
  // connection show as they happen; so it does while the sign-in window is open, which the owner quits.
  function keepReading() {
    clearTimeout(polling);
    polling = setTimeout(async () => {
      if (isDisposed()) {
        return;
      }
      if (view !== null && (view.mode === "attach" || view.signing_in)) {
        await read();
      }
      keepReading();
    }, BROWSER_POLL_MS);
  }

  // Dot's own Chrome closes for the window the owner signs in in; when it is open the owner is asked first.
  async function openSignIn() {
    if (view === null || view.signing_in) {
      return;
    }
    if (view.connected) {
      const sure = await confirmDialog({
        title: "Close Dot's Chrome?",
        message:
          "Dot's Chrome is open. It closes so that its profile can open in a plain window for you to sign in; Dot opens it again on the next step that needs it.",
        confirmLabel: "Close and open",
      });
      if (!sure || isDisposed()) {
        return;
      }
    }
    signIn.disabled = true;
    let changed;
    try {
      changed = await api.post("/browser/sign-in", {});
    } catch (error) {
      if (!isDisposed()) {
        signIn.disabled = false;
      }
      throw error;
    }
    if (isDisposed()) {
      return;
    }
    view = changed;
    paint();
    keepReading();
  }

  async function choose(id) {
    if (view === null || id === view.mode) {
      return;
    }
    problem.hidden = true;
    waiting.hidden = id !== "attach";
    let changed;
    try {
      changed = await api.post("/browser/mode", { mode: id });
    } catch (error) {
      if (!isDisposed()) {
        waiting.hidden = true;
        modes.set(view.mode);
        complain(error.message);
        // The switch may have opened Chrome meanwhile: the facts say how it stands now.
        await read();
      }
      return;
    }
    if (isDisposed()) {
      return;
    }
    waiting.hidden = true;
    view = changed;
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
      signInSetting,
      raise.element,
      el(
        "div",
        { class: "setting" },
        el("div", { class: "row" }, el("span", { class: "grow" }, "DevTools port of Dot's Chrome"), port),
        portHint,
      ),
      facts,
      standing,
      signingIn,
      waiting,
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
    paneHead("What Dot is called and calls you, its own face, how it draws your attention, the composer's keys, what a chat shows of what ran without asking, and the token this browser is signed in with.", null),
    buildNameCard(),
    look.element,
    buildNotifications(),
    buildComposerCard(),
    buildChatCard(),
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
