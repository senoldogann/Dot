// Schedules: goals that Dot carries out by itself, once or on a cron schedule, while the daemon runs. A page of the
// sidebar above Customize. Each schedule keeps how its runs start (mode, approvals, goal mode, model, reasoning level,
// time zone) and can be changed later. Run now starts one of its runs by hand, switched on or off, and its Firings
// list its last runs, at its time and by hand (item 455). The owner's schedules lead the page (item 609); under them the
// Morning brief (item 570), a schedule of its own labelled in the list, which its card sets, a line with a button for
// its settings, and the Watchers (item 571), a head and a line while there are none. New schedule and a schedule's Edit
// open the form in a dialog, so that the list keeps its place (item 615).

import { actorFace } from "../actors.js";
import { api } from "../api.js";
import { mountBrief } from "../briefcard.js";
import { mountInitiative } from "../initiativecard.js";
import { mountWatchers } from "../watcherspanel.js";
import { ago, clip, debounce, el, joined, modelName, plural, switchRow } from "../dom.js";
import { allModels, chosenLevel, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { loopRhythm } from "../rhythm.js";
import { emptyPanel, errorPanel, field, loadingPanel, markOpener, pageHead, segmented } from "../panels.js";
import { APPROVAL_OPTIONS, approvalLabel } from "../runmodes.js";
import { getSnapshot, loadModels, onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";

// A run starting or ending, and a schedule switched off after its runs failed in a row (item 458), paint the list again.
const LIFECYCLE_KINDS = new Set(["run_started", "run_finished", "schedule_stopped"]);
const RELOAD_DELAY_MS = 400;
const GOAL_PREVIEW_CHARS = 220;
// Past its third schedule the list scrolls inside (item 634): it shows three rows and the top of the fourth, which says
// that more lie under them.
const SHOWN_ROWS = 3;
const NEXT_ROW_PX = 32;
// A firing's answer is shown by its first line, cut to this.
const FIRING_RESULT_CHARS = 120;
const REPEATS = [
  { id: "cron", label: "Repeats", hint: "Starts again and again on a schedule" },
  { id: "once", label: "Once", hint: "Starts a single time" },
];
const CRON_PRESETS = [
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Daily at 09:00", cron: "0 9 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 09:00", cron: "0 9 * * 1" },
];
const MODES = [
  { id: "task", label: "Task — do the job" },
  { id: "research", label: "Research — look things up, change nothing" },
];
const ZONE_LIST_ID = "schedule-zones";
let formCount = 0;

/** The outcome of the last firing as a badge: green when it finished, red when it failed, amber when it was skipped. */
function lastStatusBadge(status) {
  if (status === "") {
    return null;
  }
  let tone = "b-muted";
  if (status === "done") {
    tone = "b-ok";
  } else if (status === "failed" || status.startsWith("switched off")) {
    tone = "b-bad";
  } else if (status === "starting" || status === "running") {
    tone = "b-info";
  } else if (status.startsWith("skipped")) {
    tone = "b-warn";
  } else if (status.startsWith("failed to start")) {
    // A firing that started nothing is a failure (item 723): it read grey, as a status of no weight.
    tone = "b-bad";
  }
  return el("span", { class: `badge ${tone}`, title: status }, clip(status, 60));
}

/** What the daemon would use for a choice the schedule leaves open, as the label of the "Default" entry. */
function defaultsNow() {
  const { status } = getSnapshot();
  return {
    model: status === null ? null : status.models.main,
    approvals: status === null ? null : status.approvals,
    goalMode: status === null ? null : status.goal_mode,
    historyRuns: status === null ? null : status.schedule_history_runs,
    sameChat: status === null ? null : status.schedule_same_chat,
    stopAfterFailures: status === null ? null : status.schedule_stop_after_failures,
    notify: status === null ? null : status.schedule_notify,
  };
}

// How many earlier runs a schedule's firing may know (item 257); a count saved over the API joins them in the menu.
const HISTORY_COUNTS = [0, 1, 3, 5, 10];
// How the end of a schedule's run is told on the phone and the desktop (item 461).
const NOTIFY_LEVELS = [
  { id: "always", label: "Always" },
  { id: "problems", label: "Only when it fails or needs me" },
  { id: "never", label: "Never" },
];

function notifyLabel(level) {
  return NOTIFY_LEVELS.find((entry) => entry.id === level)?.label ?? level;
}

// How many failed runs in a row switch a repeating schedule off (item 458); 0 is never.
const STOP_COUNTS = [0, 3, 5, 10, 20];

function stopLabel(count) {
  return count === 0 ? "Never" : `After ${count} failed tasks in a row`;
}

/** The row's note of runs that failed in a row, with when the schedule switches itself off, or null. */
function failedInRow(record) {
  if (record.runs_failed_in_row === 0) {
    return null;
  }
  const limit = record.options.stop_after_failures ?? defaultsNow().stopAfterFailures;
  // A schedule switched off, by you or by its failures, switches nothing off again.
  const title =
    record.enabled && record.cron !== null && limit !== null && limit > 0
      ? `It switches itself off at ${limit} failed tasks in a row`
      : "Its last tasks failed one after the other";
  return el("span", { class: "badge b-bad", title }, `${record.runs_failed_in_row} failed in a row`);
}

function historyLabel(count) {
  return count === 0 ? "None" : count === 1 ? "The last task" : `The last ${count} tasks`;
}

function chatLabel(same) {
  return same ? "The same chat" : "A new chat each time";
}

/** The goal of a schedule; a long one shows its beginning with a button to read all of it. */
function goalText(text) {
  if (text.length <= GOAL_PREVIEW_CHARS) {
    return el("div", { class: "row-text muted" }, text);
  }
  const preview = clip(text, GOAL_PREVIEW_CHARS);
  let expanded = false;
  const body = el("div", { class: "row-text muted" }, preview);
  const toggle = el("button", { class: "btn ghost sm", type: "button", "aria-expanded": "false" }, "Show all");
  toggle.addEventListener("click", () => {
    expanded = !expanded;
    body.textContent = expanded ? text : preview;
    toggle.textContent = expanded ? "Show less" : "Show all";
    toggle.setAttribute("aria-expanded", String(expanded));
  });
  return el("div", null, body, toggle);
}

/** The approvals a schedule's runs take where it or its agent chose them (item 462), or null for the configured ones. */
function chosenApprovals(record, agent) {
  return record.options.approvals ?? (agent === null ? null : agent.approvals);
}

/** The chips that say how the schedule's runs start; a choice left open shows what the daemon uses now. */
function optionChips(record, agent) {
  const options = record.options;
  const now = defaultsNow();
  const approvals = chosenApprovals(record, agent);
  const goalMode = options.goal_mode ?? now.goalMode;
  const model = options.model ?? now.model;
  // Only what this schedule chose for itself, and what its agent chose where it left them open (item 462): the
  // defaults it takes are the ones every run takes, and saying them on every row buries what is its own (item 373).
  return [
    el("span", { class: "chip", title: "Mode" }, record.mode),
    agent === null ? null : el("span", { class: "chip", title: "Its tasks run as this agent, within its limits" }, `As ${agent.name}`),
    // A paused agent's schedule fires nothing by itself until the agent is resumed (item 465).
    agent === null || agent.paused_at === null
      ? null
      : el("span", { class: "badge b-warn", title: "It starts nothing by itself until you resume the agent" }, `${agent.name} is paused`),
    approvals === null
      ? null
      : el(
          "span",
          {
            class: "chip",
            title: options.approvals === null ? "Who answers Dot's safety questions: its agent's choice" : "Who answers Dot's safety questions",
          },
          `Approvals: ${approvalLabel(approvals)}`,
        ),
    options.goal_mode === null ? null : el("span", { class: "chip" }, `Goal mode ${goalMode ? "on" : "off"}`),
    options.model === null ? null : el("span", { class: "chip", title: model }, modelName(model)),
    options.effort === null ? null : el("span", { class: "chip" }, `Reasoning ${levelLabel(options.effort)}`),
    options.speed === "fast" ? el("span", { class: "chip", title: "Its tasks start in fast mode" }, "Fast") : null,
    options.history_runs === null ? null : el("span", { class: "chip", title: "Earlier tasks it knows" }, `Knows: ${historyLabel(options.history_runs)}`),
    options.same_chat === null ? null : el("span", { class: "chip" }, chatLabel(options.same_chat)),
    // Only a repeating schedule is switched off by its failures (item 458).
    options.stop_after_failures === null || record.cron === null
      ? null
      : el("span", { class: "chip", title: "When it switches itself off" }, `Switch off: ${stopLabel(options.stop_after_failures).toLowerCase()}`),
    options.notify === null
      ? null
      : el("span", { class: "chip", title: "How the end of its tasks is told" }, `Notifications: ${notifyLabel(options.notify).toLowerCase()}`),
  ];
}

/** What Run now says it starts: real work, as at the schedule's time, with the approvals its run takes. */
function runNowTitle(record, agent) {
  const approvals = chosenApprovals(record, agent) ?? defaultsNow().approvals;
  if (approvals === null) {
    return "Run now: it does real work, as it does at its time";
  }
  const asks = approvals === "yolo" ? ", asking nothing" : "";
  return `Run now: it does real work, as it does at its time (approvals: ${approvalLabel(approvals)}${asks})`;
}

/** A schedule's last firings, newest first: how each went, when, its chat, and the first line of its answer. */
function firingsList(firings) {
  if (firings.length === 0) {
    return el("div", { class: "hint" }, "Not run yet");
  }
  return el(
    "ul",
    { class: "firings-list" },
    firings.map(({ run, chat_title }) =>
      el(
        "li",
        { class: "firing" },
        el(
          "div",
          { class: "row-meta" },
          lastStatusBadge(run.status),
          el("a", { class: "firing-title", href: `#/runs/${run.id}` }, chat_title),
          el("span", { class: "faint" }, ago(run.created_at)),
        ),
        run.result === "" ? null : el("div", { class: "row-text muted" }, clip(run.result.split("\n")[0], FIRING_RESULT_CHARS)),
      ),
    ),
  );
}

function scheduleRow(view, handlers) {
  const { record, agent } = view;
  const runNow = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: runNowTitle(record, agent),
      "aria-label": `Run ${record.name} now`,
      onclick: () => handlers.onRunNow(record),
    },
    icon("play"),
  );
  const open = handlers.firingsOpen(record.id);
  const firings = el("div", { class: "firings", hidden: !open, dataset: { firings: record.id } });
  const firingsToggle = el(
    "button",
    {
      class: "btn ghost sm",
      type: "button",
      "aria-expanded": String(open),
      "aria-label": `History of ${record.name}`,
      onclick: () => handlers.onFirings(record, firingsToggle, firings),
    },
    "History",
  );
  // A one-time schedule that ran at its moment, as the daemon says (item 723): it read "switched off", as one the owner
  // had switched off, and switching it on was refused.
  const done = view.ran_once === true;
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? "Switch off" : "Switch on"} ${record.name}`,
    title: done ? "It ran once and has no run left: Edit gives it a new time" : record.enabled ? "Switch this schedule off" : "Switch this schedule on",
    disabled: done,
    onclick: () => handlers.onToggle(record),
  });
  const edit = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: view.brief ? "Edit the morning brief's settings" : "Edit this schedule",
      "aria-label": `Edit ${record.name}`,
      onclick: () => handlers.onEdit(view),
    },
    icon("pencil"),
  );
  const remove = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Delete this schedule",
      "aria-label": `Delete ${record.name}`,
      onclick: () => handlers.onDelete(record),
    },
    icon("trash"),
  );
  let next = null;
  if (done) {
    // Grey, as it says nothing of how the run went: the badge of its last status beside it says that.
    next = el("span", { class: "badge b-muted", title: "A one-time schedule: it ran at its time, and waits for nothing more" }, "ran once");
  } else if (!record.enabled) {
    next = el("span", { class: "badge b-muted" }, "switched off");
  } else if (view.next_local !== null) {
    next = el("span", null, `Next ${view.next_local}`);
  } else {
    next = el("span", null, "Will not run again");
  }
  const last =
    view.last_local === null
      ? el("span", null, record.last_status === "" ? "Never ran" : "Not run yet")
      : el("span", null, `Last ${view.last_local}`);
  return el(
    "div",
    { class: "list-row", dataset: { schedule: record.id } },
    toggle,
    // A schedule of one of the owner's agents wears the agent's face (item 462); Dot's wears its own.
    agent === null ? actorFace(record.id, "idle", null) : actorFace(agent.id, "idle", agent.look),
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row" },
        el("span", { class: "row-title" }, record.name),
        view.brief ? el("span", { class: "badge b-info", title: "Dot writes it each morning; the Morning brief under your schedules sets it" }, "Brief") : null,
        // A task the owner taught and asked to repeat (item 666): its runs start with that skill.
        (record.options.skills ?? []).length > 0
          ? el("span", { class: "badge b-info", title: `Its runs start with the skill ${record.options.skills.join(", ")}` }, "Taught task")
          : null,
      ),
      el("div", { class: "row-meta" }, optionChips(record, agent)),
      goalText(record.goal),
      // One line of when and how it went, its parts kept by a dot: a phone wraps it in two lines, not one a part.
      el(
        "div",
        { class: "row-meta when-line" },
        ...joined([
          whenWords(view.when),
          next,
          last,
          lastStatusBadge(record.last_status),
          failedInRow(record),
          view.zone === localZone()
            ? null
            : el("span", { class: "faint", title: "The time zone this schedule is read in" }, view.zone),
        ]),
        firingsToggle,
      ),
      firings,
    ),
    el("div", { class: "row-actions" }, runNow, edit, remove),
  );
}

/**
 * Cut the list of schedules `list` under its third row and the top of its fourth (item 634), so that it scrolls inside:
 * measured where the rows stand, since a goal's lines and a schedule's opened Firings make rows of differing heights,
 * and measured again whenever one of the three changes size (the window, Firings). A list of three or fewer is not cut.
 * Returns `dispose()`.
 */
function cutAfterThird(list) {
  if (list.children.length <= SHOWN_ROWS) {
    return () => {};
  }
  const shown = [...list.children].slice(0, SHOWN_ROWS);
  const measure = () => {
    const foot = shown[SHOWN_ROWS - 1].getBoundingClientRect().bottom - list.getBoundingClientRect().top + list.scrollTop;
    list.style.setProperty("--shown-rows", `${Math.ceil(foot) + NEXT_ROW_PX}px`);
  };
  list.classList.add("cut");
  const observer = new ResizeObserver(measure);
  for (const row of shown) {
    observer.observe(row);
  }
  measure();
  return () => observer.disconnect();
}

/** When a schedule runs, in words: "Every day at 08:00" for a repeating one the page can read, else what the daemon says
 * ("cron 5 4 1 * *", "once, 25 Oct 08:00"), the cron form in the tooltip (item 625). */
function whenWords(when) {
  const cron = when.startsWith("cron ") ? when.slice("cron ".length) : null;
  const said = cron === null ? null : loopRhythm(cron);
  const text = said === null ? (cron === null ? `${when.charAt(0).toUpperCase()}${when.slice(1)}` : "On a repeating schedule") : `${said.charAt(0).toUpperCase()}${said.slice(1)}`;
  return el("span", cron === null ? { title: "When it runs" } : { title: `cron ${cron}`, "data-expert": "" }, text);
}

/** This Mac's time zone, as the browser names it. */
function localZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function select(label, entries, value) {
  const element = el(
    "select",
    { class: "select", "aria-label": label },
    entries.map((entry) => el("option", { value: entry.id }, entry.label)),
  );
  element.value = value;
  return element;
}

/** The list of time zone names the zone field suggests; built once per page. */
function zoneList() {
  const names = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return el("datalist", { id: ZONE_LIST_ID }, names.map((name) => el("option", { value: name })));
}

/**
 * The fields that create a schedule (`view` null) or change one, for the dialog that holds them (item 615). Returns the
 * element; `submit()`, which asks the daemon and resolves to `false` when a field the schedule needs is empty (the
 * field takes the focus) and throws when the daemon refuses; and `ready`, which settles once the agents and the models
 * the selects offer are read.
 */
function scheduleForm({ view }) {
  const record = view === null ? null : view.record;
  const options =
    record === null
      ? {
          model: null,
          effort: null,
          speed: null,
          approvals: null,
          goal_mode: null,
          timezone: null,
          history_runs: null,
          same_chat: null,
          stop_after_failures: null,
          notify: null,
          agent_id: null,
        }
      : record.options;
  const now = defaultsNow();
  const id = ++formCount;

  const name = el("input", { class: "input", type: "text", maxlength: "120", autocomplete: "off", placeholder: "Morning briefing", "aria-label": "Name" });
  const goal = el("textarea", {
    class: "textarea",
    rows: record === null ? "3" : "6",
    placeholder: "What should Dot do each time? Be as specific as for a normal task.",
    "aria-label": "Goal",
  });
  const mode = select("Mode", MODES, record === null ? "task" : record.mode);
  const approvals = select(
    "Approvals",
    [{ id: "", label: "Default" }, ...APPROVAL_OPTIONS.map((option) => ({ id: option.id, label: `${option.label} — ${option.hint}` }))],
    options.approvals ?? "",
  );
  // The owner's agent its runs run as (item 462): Dot, or one of theirs, whose model, reasoning and approvals its runs
  // take where the schedule leaves them on Default. The list comes from the daemon; until then the schedule's own.
  const agent = el("select", { class: "select", "aria-label": "Agent" }, el("option", { value: "" }, "Dot"));
  let agents = view === null || view.agent === null ? [] : [view.agent];
  // Once the owner picked one, the list read later keeps their pick.
  let picked = false;
  function paintAgents() {
    const keep = picked ? agent.value : options.agent_id ?? "";
    agent.replaceChildren(el("option", { value: "" }, "Dot"), ...agents.map((record) => el("option", { value: record.id }, record.name)));
    agent.value = agents.some((record) => record.id === keep) ? keep : "";
    paintApprovalsDefault();
  }
  // The Default of the approvals names what the run takes: the agent's choice, else the configured one.
  function paintApprovalsDefault() {
    const chosen = agents.find((record) => record.id === agent.value) ?? null;
    const first = approvals.options[0];
    if (chosen !== null && chosen.approvals !== null) {
      first.textContent = `Default (the agent's: ${approvalLabel(chosen.approvals)})`;
    } else {
      first.textContent = now.approvals === null ? "Default" : `Default (${approvalLabel(now.approvals)})`;
    }
  }
  agent.addEventListener("change", () => {
    picked = true;
    paintApprovalsDefault();
  });
  paintAgents();
  const agentsRead = api
    .get("/agents")
    .then((summaries) => {
      agents = summaries.map((summary) => summary.record).filter((record) => record.id !== "dot");
      paintAgents();
    })
    .catch((error) => {
      console.warn("the agents could not be listed", error);
    });
  const goalMode = select(
    "Goal mode",
    [
      { id: "", label: now.goalMode === null ? "Default" : `Default (${now.goalMode ? "on" : "off"})` },
      { id: "on", label: "On — Dot checks its answer and keeps working until the goal is reached" },
      { id: "off", label: "Off" },
    ],
    options.goal_mode === null ? "" : options.goal_mode ? "on" : "off",
  );
  const counts = [...new Set([...HISTORY_COUNTS, ...(options.history_runs === null ? [] : [options.history_runs])])].sort((a, b) => a - b);
  const history = select(
    "Earlier tasks it knows",
    [
      { id: "", label: now.historyRuns === null ? "Default" : `Default (${historyLabel(now.historyRuns)})` },
      ...counts.map((count) => ({ id: String(count), label: historyLabel(count) })),
    ],
    options.history_runs === null ? "" : String(options.history_runs),
  );
  const stops = [...new Set([...STOP_COUNTS, ...(options.stop_after_failures === null ? [] : [options.stop_after_failures])])].sort(
    (a, b) => a - b,
  );
  const stopAfter = select(
    "Switch off after",
    [
      { id: "", label: now.stopAfterFailures === null ? "Default" : `Default (${stopLabel(now.stopAfterFailures)})` },
      ...stops.map((count) => ({ id: String(count), label: stopLabel(count) })),
    ],
    options.stop_after_failures === null ? "" : String(options.stop_after_failures),
  );
  const notify = select(
    "Notifications",
    [
      { id: "", label: now.notify === null ? "Default" : `Default (${notifyLabel(now.notify)})` },
      ...NOTIFY_LEVELS.map(({ id, label }) => ({ id, label })),
    ],
    options.notify ?? "",
  );
  const chat = select(
    "Chat",
    [
      { id: "", label: now.sameChat === null ? "Default" : `Default (${chatLabel(now.sameChat)})` },
      { id: "new", label: chatLabel(false) },
      { id: "same", label: chatLabel(true) },
    ],
    options.same_chat === null ? "" : options.same_chat ? "same" : "new",
  );
  const model = el(
    "select",
    { class: "select", "aria-label": "Model" },
    el("option", { value: "" }, now.model === null ? "Default model" : `Default (${modelName(now.model)})`),
    options.model === null ? null : el("option", { value: options.model }, options.model),
  );
  model.value = options.model ?? "";
  const effort = el("select", { class: "select", "aria-label": "Reasoning level" }, el("option", { value: "" }, "Default"));
  const effortField = field("Reasoning", effort, "");
  effortField.hidden = true;
  let models = [];

  function paintEffort() {
    const info = models.find((candidate) => candidate.ref === model.value) ?? null;
    const keep = effort.value === "" ? options.effort ?? "" : effort.value;
    if (info === null || info.efforts.length < 2) {
      effort.replaceChildren(el("option", { value: "" }, "Default"));
      effort.value = "";
      effortField.hidden = true;
      return;
    }
    effort.replaceChildren(
      el("option", { value: "" }, info.default_effort === null ? "Default" : `Default (${levelLabel(info.default_effort)})`),
      ...info.efforts.map((level) => el("option", { value: level }, levelLabel(level))),
    );
    effort.value = info.efforts.includes(keep) ? keep : "";
    effortField.hidden = false;
  }

  model.addEventListener("change", paintEffort);
  const modelsRead = loadModels()
    .then((providers) => {
      models = allModels(providers).filter((candidate) => candidate.tools);
      const chosen = model.value;
      const known = new Set(models.map((candidate) => candidate.ref));
      model.replaceChildren(
        model.firstElementChild,
        ...(chosen !== "" && !known.has(chosen) ? [el("option", { value: chosen }, `${chosen} (not offered now)`)] : []),
        ...models.map((candidate) => el("option", { value: candidate.ref }, candidate.ref)),
      );
      model.value = chosen;
      paintEffort();
    })
    .catch((error) => {
      console.warn("the models could not be listed", error);
    });

  const zone = el("input", {
    class: "input",
    type: "text",
    autocomplete: "off",
    spellcheck: "false",
    list: ZONE_LIST_ID,
    placeholder: "Dot's own time zone",
    "aria-label": "Time zone",
  });
  zone.value = options.timezone ?? "";

  const cron = el("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "0 9 * * 1-5", "aria-label": "Custom timing" });
  const presets = el(
    "div",
    { class: "cluster" },
    CRON_PRESETS.map((preset) =>
      el(
        "button",
        {
          class: "btn sm",
          type: "button",
          onclick: () => {
            cron.value = preset.cron;
            cron.focus();
          },
        },
        preset.label,
      ),
    ),
  );
  const cronField = el(
    "div",
    { class: "stack tight wide" },
    field("Custom timing", cron, "Five parts separated by spaces: minute, hour, day of the month, month, day of the week. Very frequent schedules are refused."),
    presets,
  );
  const at = el("input", { class: "input", type: "datetime-local", "aria-label": "Date and time" });
  const atField = field("Date and time", at, "Read in the schedule's time zone.");
  const goalField = field("Goal", goal, "");
  goalField.classList.add("wide");
  const startsOnce = record !== null && record.cron === null;
  const repeats = segmented(REPEATS, startsOnce ? "once" : "cron", (choice) => {
    cronField.hidden = choice !== "cron";
    atField.hidden = choice !== "once";
  });
  cronField.hidden = startsOnce;
  atField.hidden = !startsOnce;
  const enabled = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(record === null || record.enabled), "aria-label": "Switched on" });
  // Whether the owner moved the form's switch: an edit that leaves it alone keeps the schedule on or off as it is then,
  // not as it was when the form opened (item 723).
  let enabledTouched = false;
  enabled.addEventListener("click", () => {
    enabledTouched = true;
    enabled.setAttribute("aria-checked", String(enabled.getAttribute("aria-checked") !== "true"));
  });
  if (record !== null) {
    name.value = record.name;
    goal.value = record.goal;
    cron.value = record.cron ?? "";
    at.value = view.run_at_input ?? "";
  }

  async function submit() {
    const nameText = name.value.trim();
    const goalText = goal.value.trim();
    const recurring = repeats.value() === "cron";
    const cronText = cron.value.trim();
    if (nameText === "") {
      name.focus();
      return false;
    }
    if (goalText === "") {
      goal.focus();
      return false;
    }
    if (recurring && cronText === "") {
      cron.focus();
      return false;
    }
    if (!recurring && at.value === "") {
      at.focus();
      return false;
    }
    const body = {
      name: nameText,
      goal: goalText,
      cron: recurring ? cronText : null,
      at: recurring ? null : at.value,
      mode: mode.value,
      model: model.value === "" ? null : model.value,
      effort: chosenLevel(models, model.value, effort.value, options.model ?? "", options.effort),
      approvals: approvals.value === "" ? null : approvals.value,
      goal_mode: goalMode.value === "" ? null : goalMode.value === "on",
      timezone: zone.value.trim() === "" ? null : zone.value.trim(),
      history_runs: history.value === "" ? null : Number(history.value),
      same_chat: chat.value === "" ? null : chat.value === "same",
      stop_after_failures: stopAfter.value === "" ? null : Number(stopAfter.value),
      notify: notify.value === "" ? null : notify.value,
      agent_id: agent.value === "" ? null : agent.value,
      // The form has no speed of its own: a schedule keeps the one its chat gave it (item 291).
      speed: options.speed,
    };
    if (record === null) {
      // The page makes the form anew for the next one, its choices back at their defaults (item 559).
      await api.post("/schedules", body);
      toast("ok", "Schedule added.");
    } else {
      await api.patch(`/schedules/${record.id}`, enabledTouched ? { ...body, enabled: enabled.getAttribute("aria-checked") === "true" } : body);
      toast("ok", "Schedule saved.");
    }
    return true;
  }

  const element = el(
    "div",
    { class: "stack", dataset: { scheduleForm: String(id) } },
    el(
      "div",
      { class: "form-grid" },
      field("Name", name, ""),
      field("Mode", mode, "Scheduled tasks work on their own."),
      field("Agent", agent, "Its tasks run as this agent: with its instructions and abilities, and its model, reasoning and approvals where these are left on Default."),
      goalField,
      field("Approvals", approvals, "Tasks that work by themselves cannot wait for you: with Ask, a question stops the task until you answer."),
      field("Goal mode", goalMode, ""),
      field("Model", model, "The model every step of the task uses, and the summaries of its older part, until it moves to a fallback model."),
      effortField,
      field("Time zone", zone, "A name such as Europe/Istanbul; empty uses Dot's own time zone."),
      field("Earlier tasks it knows", history, "Each task starts with the dated answers of the last tasks, to build on them instead of doing the same work again."),
      field("Chat", chat, "In the same chat each task reads the conversation of the tasks before it instead."),
      field("Switch off after", stopAfter, "A repeating schedule whose tasks fail so many times in a row is switched off, and you are told; a task you stop counts neither way."),
      field("Notifications", notify, "How the end of its tasks is told on your phone and desktop; a question, a draft and a switch-off are told always, and a task you start by hand too."),
      record === null ? null : switchRow(el("div", { class: "field" }, el("span", { class: "field-label" }, "Switched on"), enabled), enabled),
      el("div", { class: "field wide" }, el("span", { class: "field-label" }, "Repeat"), el("div", { class: "cluster" }, repeats.element)),
      cronField,
      atField,
    ),
  );
  return { element, submit, ready: Promise.all([agentsRead, modelsRead]) };
}

export function mount(root, ctx) {
  let disposed = false;
  // Grows with every load, so a slow answer never overwrites a newer one.
  let ticket = 0;
  let views = null;
  // The schedules whose Firings are open: they stay open, and are read again, as the list is painted again.
  const openFirings = new Set();
  // The schedule a line of a conversation led here to (item 665, `#/schedules?schedule=ID`): marked and in sight once,
  // when the list is first painted.
  let askedId = new URLSearchParams(location.hash.split("?")[1] ?? "").get("schedule") ?? "";
  // What stops measuring the list's cut after its third row (item 634), when it is painted again or the page goes.
  let stopCut = () => {};
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "hint" });
  // The form that adds a schedule opens in a dialog when New schedule asks for it; the list is what this page is for.
  const addButton = el("button", { class: "btn primary sm", type: "button" });
  markOpener(addButton, "New schedule");
  addButton.addEventListener("click", () => openForm(null));
  const schedulerNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "Schedules are paused, so nothing starts until Dot starts again."));
  // The brief's card reads again whenever the list does: a switch in the list, a run's end, a failure that switched it off.
  const brief = mountBrief(ctx, () => reload());
  // The watchers (item 571), under the brief: what Dot tells you of when it happens.
  const watchers = mountWatchers();
  // Dot's own initiative (item 715), under the watchers: what Dot does for you on its own while the Mac is idle.
  const initiative = mountInitiative();

  function paint() {
    if (views === null) {
      return;
    }
    // A list painted again keeps where it was scrolled to inside (item 634): a run's start or end paints it.
    const scrolled = holder.querySelector(".schedule-list")?.scrollTop ?? 0;
    stopCut();
    stopCut = () => {};
    if (views.length === 0) {
      holder.replaceChildren(emptyPanel("calendar", "No schedules yet", "Add one with New schedule and Dot will start it by itself."));
      return;
    }
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
      onEdit: (view) => (view.brief ? brief.open() : openForm(view)),
      onRunNow: (record) => attempt(() => runNow(record)),
      onFirings: (record, button, list) => attempt(() => toggleFirings(record, button, list)),
      firingsOpen: (id) => openFirings.has(id),
    };
    const list = el("div", { class: "schedule-list" }, views.map((view) => scheduleRow(view, handlers)));
    holder.replaceChildren(list);
    stopCut = cutAfterThird(list);
    list.scrollTop = scrolled;
    const asked = askedId === "" ? null : list.querySelector(`[data-schedule="${CSS.escape(askedId)}"]`);
    askedId = "";
    if (asked !== null) {
      asked.classList.add("asked");
      asked.scrollIntoView({ block: "nearest" });
    }
    count.textContent = plural(views.length, "schedule", "schedules");
    for (const list of holder.querySelectorAll("[data-firings]")) {
      if (!list.hidden) {
        attempt(() => loadFirings(list.dataset.firings, list));
      }
    }
  }

  async function loadFirings(id, list) {
    const firings = await api.get(`/schedules/${id}/firings`);
    if (!disposed && list.isConnected) {
      list.replaceChildren(firingsList(firings));
    }
  }

  async function toggleFirings(record, button, list) {
    const open = !openFirings.has(record.id);
    if (open) {
      openFirings.add(record.id);
    } else {
      openFirings.delete(record.id);
    }
    button.setAttribute("aria-expanded", String(open));
    list.hidden = !open;
    if (open) {
      list.replaceChildren(loadingPanel());
      await loadFirings(record.id, list);
    }
  }

  // No question first: the button says what it starts, and its run's page, where it can be stopped, opens at once.
  async function runNow(record) {
    const run = await api.post(`/schedules/${record.id}/run`, null);
    toast("ok", run.approvals === "yolo" ? `Started ${record.name}: it asks nothing.` : `Started ${record.name}.`);
    ctx.navigate(`/runs/${run.id}`);
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const fresh = await api.get("/schedules");
      if (disposed || mine !== ticket) {
        return;
      }
      views = fresh;
      paint();
      attempt(brief.reload);
      attempt(initiative.reload);
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (views === null) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      } else {
        readFailed("The schedules", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  /** The form that adds a schedule (`view` null) or changes `view`'s, in a dialog; the list is read again once the daemon
   * took it. The dialog holds the form, so a run starting or ending paints the list behind it without touching what is
   * typed. A form is made anew each time, so the next schedule starts from the defaults, not from the last one's choices
   * (its approvals and its agent among them), which a schedule that runs unattended would take unless the owner noticed
   * them (item 559). */
  function openForm(view) {
    const form = scheduleForm({ view });
    const record = view === null ? null : view.record;
    formDialog({
      title: record === null ? "New schedule" : "Edit schedule",
      description:
        record === null
          ? "Dot works on the goal by itself, once or on a repeating schedule, while Dot is running."
          : `Change “${clip(record.name, 60)}”. Its next task starts with the new choices.`,
      body: form.element,
      submitLabel: record === null ? "Add schedule" : "Save changes",
      size: "wide",
      ready: form.ready,
      // A schedule's row is painted again once it is saved: its Edit button is found anew.
      returnFocus: () => (record === null ? addButton : holder.querySelector(`[data-schedule="${record.id}"] [aria-label^="Edit "]`)),
      onSubmit: async () => {
        if ((await form.submit()) === false) {
          return false;
        }
        await reload();
        return true;
      },
    });
  }

  async function toggle(record) {
    await api.post(`/schedules/${record.id}/${record.enabled ? "disable" : "enable"}`, null);
    await reload();
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: "Delete this schedule?",
      message: `“${clip(record.name, 80)}” will not run again. Tasks it started earlier stay in the history.`,
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/schedules/${record.id}`);
    toast("ok", "Schedule deleted.");
    await reload();
  }

  const reloadSoon = debounce(() => {
    if (!disposed) {
      reload();
    }
  }, RELOAD_DELAY_MS);

  const status = getSnapshot().status;
  schedulerNote.hidden = status === null || status.scheduler_running;
  ctx.setTitle("Schedules");
  root.append(
    pageHead("Schedules", "Goals that Dot works on by itself, once or on a repeating schedule.", null),
    el(
      "div",
      { class: "note" },
      icon("clock"),
      el("span", null, "Schedules work only while Dot is running and this Mac is awake. A time that passes while Dot is off is skipped, not made up later."),
    ),
    schedulerNote,
    el(
      "section",
      { class: "memory-panel notes-panel schedule-panel" },
      el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "Your schedules"), count, el("span", { class: "spacer" }), addButton),
      holder,
    ),
    brief.element,
    watchers.element,
    initiative.element,
    zoneList(),
  );
  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      reloadSoon();
    }
  });
  const stopBack = onStreamBack(reloadSoon);
  attempt(reload);
  return () => {
    disposed = true;
    stopCut();
    brief.dispose();
    watchers.dispose();
    initiative.dispose();
    stopLifecycle();
    stopBack();
  };
}
