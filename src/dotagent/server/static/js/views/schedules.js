// Scheduled tasks (Settings › Scheduled tasks, item 1337): goals that Dot carries out by itself, once or on a cron schedule,
// while the daemon runs. Each schedule keeps how its runs start (mode, approvals, goal mode, model, reasoning level, time
// zone) and can be changed later. The list (`#/settings/schedules`) holds the owner's schedules, each with its switch, Run
// now, Edit and Delete, and Firings under Details (item 455), then the loops of chats (item 281; `?thread=<id>` shows one
// chat's), the Morning brief (item 570), the check-in, the Watchers (item 571) and Dot's own initiative (item 715). New
// schedule and a schedule's Edit open the form as a page of its own, `#/settings/schedules/new` and `.../<id>`, in the
// settings' rows and cards.

import { api } from "../api.js";
import { mountBrief } from "../briefcard.js";
import { mountCheckin } from "../checkincard.js";
import { mountInitiative } from "../initiativecard.js";
import { mountTells } from "../tellspanel.js";
import { mountWatchers } from "../watcherspanel.js";
import { ago, clip, debounce, el, joined, modelName, switchRow } from "../dom.js";
import { allModels, chosenLevel, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { moreButton } from "../menu.js";
import { loopRhythm } from "../rhythm.js";
import { emptyPanel, errorPanel, loadingPanel, markOpener, segmented, setCard, setRow, setSection } from "../panels.js";
import { APPROVAL_OPTIONS, approvalLabel } from "../runmodes.js";
import { getSnapshot, loadModels, onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { explain } from "../errors.js";
import { stateOfControls } from "../formflow.js";
import { t, tCount } from "../i18n.js";

// A run starting or ending, and a schedule switched off after its runs failed in a row (item 458), paint the list again.
const LIFECYCLE_KINDS = new Set(["run_started", "run_finished", "schedule_stopped"]);
const RELOAD_DELAY_MS = 400;
const GOAL_PREVIEW_CHARS = 220;
// A firing's answer is shown by its first line, cut to this.
const FIRING_RESULT_CHARS = 120;
const REPEATS = [
  { id: "cron", label: t("views.schedules.repeats"), hint: t("views.schedules.startsAgainAndAgainOn") },
  { id: "once", label: t("views.schedules.once"), hint: t("views.schedules.startsASingleTime") },
];
const CRON_PRESETS = [
  { label: t("views.schedules.everyHour"), cron: "0 * * * *" },
  { label: t("views.schedules.dailyAt0900"), cron: "0 9 * * *" },
  { label: t("views.schedules.weekdaysAt0900"), cron: "0 9 * * 1-5" },
  { label: t("views.schedules.mondaysAt0900"), cron: "0 9 * * 1" },
];
const MODES = [
  { id: "task", label: t("views.schedules.taskDoTheJob") },
  { id: "research", label: t("views.schedules.researchLookThingsUpChange") },
];
const ZONE_LIST_ID = "schedule-zones";

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
  { id: "always", label: t("views.schedules.always") },
  { id: "problems", label: t("views.schedules.onlyWhenItFailsOr") },
  { id: "never", label: t("views.schedules.never") },
];

function notifyLabel(level) {
  return NOTIFY_LEVELS.find((entry) => entry.id === level)?.label ?? level;
}

// How many failed runs in a row switch a repeating schedule off (item 458); 0 is never.
const STOP_COUNTS = [0, 3, 5, 10, 20];

function stopLabel(count) {
  return count === 0 ? t("views.schedules.never") : t("views.schedules.afterCountFailedTasksIn", { count });
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
      ? t("views.schedules.itSwitchesItselfOffAt", { limit })
      : t("views.schedules.itsLastTasksFailedOne");
  return el("span", { class: "badge b-bad", title }, t("views.schedules.runsFailedInRowFailed", { runs_failed_in_row: record.runs_failed_in_row }));
}

/** The row's note of firings skipped one after the other (item 1401), from two on and while the last status is a skip,
 * or null: one skip is the last status's to say, beside it. A skipped firing starts no task, and the count ends with the
 * first firing that does. */
function skippedInRow(record) {
  const count = record.skipped_in_row ?? 0;
  if (count < 2 || !record.last_status.startsWith("skipped")) {
    return null;
  }
  return el("span", { class: "badge b-warn", title: t("views.schedules.itsLastFiringsWereSkipped") }, t("views.schedules.skippedCountInARow", { count }));
}

function historyLabel(count) {
  return count === 0 ? t("views.schedules.none") : count === 1 ? t("views.schedules.theLastTask") : t("views.schedules.theLastCountTasks", { count });
}

function chatLabel(same) {
  return same ? t("views.schedules.theSameChat") : t("views.schedules.aNewChatEachTime");
}

/** The goal of a schedule; a long one shows its beginning with a button to read all of it. */
function goalText(text) {
  if (text.length <= GOAL_PREVIEW_CHARS) {
    return el("div", { class: "row-text muted" }, text);
  }
  const preview = clip(text, GOAL_PREVIEW_CHARS);
  let expanded = false;
  const body = el("div", { class: "row-text muted" }, preview);
  const toggle = el("button", { class: "btn ghost sm", type: "button", "aria-expanded": "false" }, t("views.schedules.showAll"));
  toggle.addEventListener("click", () => {
    expanded = !expanded;
    body.textContent = expanded ? text : preview;
    toggle.textContent = expanded ? t("views.schedules.showLess") : t("views.schedules.showAll");
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
    el("span", { class: "chip", title: t("views.schedules.mode") }, record.mode),
    agent === null ? null : el("span", { class: "chip", title: t("views.schedules.itsTasksRunAsThis") }, t("views.schedules.asName", { name: agent.name })),
    approvals === null
      ? null
      : el(
          "span",
          {
            class: "chip",
            title: options.approvals === null ? t("views.schedules.whoAnswersDotSSafety") : t("views.schedules.whoAnswersDotSSafety2"),
          },
          t("views.schedules.approvalsApprovallabel", { approvalLabel: approvalLabel(approvals) }),
        ),
    options.goal_mode === null ? null : el("span", { class: "chip" }, goalMode ? t("views.schedules.goalModeOn") : t("views.schedules.goalModeOff")),
    options.model === null ? null : el("span", { class: "chip", title: model }, modelName(model)),
    options.effort === null ? null : el("span", { class: "chip" }, t("views.schedules.reasoningLevellabel", { levelLabel: levelLabel(options.effort) })),
    options.speed === "fast" ? el("span", { class: "chip", title: t("views.schedules.itsTasksStartInFast") }, t("views.schedules.fast")) : null,
    options.history_runs === null ? null : el("span", { class: "chip", title: t("views.schedules.earlierTasksItKnows") }, t("views.schedules.knowsHistorylabel", { historyLabel: historyLabel(options.history_runs) })),
    options.same_chat === null ? null : el("span", { class: "chip" }, chatLabel(options.same_chat)),
    // Only a repeating schedule is switched off by its failures (item 458).
    options.stop_after_failures === null || record.cron === null
      ? null
      : el("span", { class: "chip", title: t("views.schedules.whenItSwitchesItselfOff") }, t("views.schedules.switchOffTolowercase", { toLowerCase: stopLabel(options.stop_after_failures).toLowerCase() })),
    options.notify === null
      ? null
      : el("span", { class: "chip", title: t("views.schedules.howTheEndOfIts") }, t("views.schedules.notificationsTolowercase", { toLowerCase: notifyLabel(options.notify).toLowerCase() })),
    options.precheck === null || options.precheck === undefined
      ? null
      : el("span", { class: "chip mono", title: t("views.schedules.aCheckRunsFirstWithout") }, t("views.schedules.checkPrecheck", { precheck: options.precheck })),
  ];
}

/** A paused agent's schedule fires nothing by itself until the agent is resumed (item 465): its row's line says so, in
 * sight as a failed last run is (item 1136), or null. */
function pausedBadge(agent) {
  return agent === null || agent.paused_at === null
    ? null
    : el("span", { class: "badge b-warn", title: t("views.schedules.itStartsNothingByItself") }, t("views.schedules.nameIsPaused", { name: agent.name }));
}

/** What Run now says it starts: real work, as at the schedule's time, with the approvals its run takes. */
function runNowTitle(record, agent) {
  const approvals = chosenApprovals(record, agent) ?? defaultsNow().approvals;
  if (approvals === null) {
    return t("views.schedules.runNowItDoesReal");
  }
  const asks = approvals === "yolo" ? t("views.schedules.askingOnlyToPayOr") : "";
  return t("views.schedules.runNowItDoesReal2", { approvalLabel: approvalLabel(approvals), asks });
}

/** A schedule's last firings, newest first: how each went, when, its chat, and the first line of its answer. */
function firingsList(firings) {
  if (firings.length === 0) {
    return el("div", { class: "hint" }, t("views.schedules.notRunYet"));
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
  const open = handlers.firingsOpen(record.id);
  // A row is its name and one line of when and how it went (item 1136); how its tasks start, its goal and its history
  // stand behind Details, which a repaint keeps open.
  const firings = el("div", { class: "firings", dataset: { firings: record.id } });
  const details = el(
    "div",
    { class: "schedule-details", hidden: !open },
    el("div", { class: "row-meta" }, optionChips(record, agent)),
    goalText(record.goal),
    el("h4", { class: "schedule-details-label" }, t("views.schedules.history")),
    firings,
  );
  const detailsToggle = el(
    "button",
    {
      class: "btn ghost sm schedule-details-toggle",
      type: "button",
      "aria-expanded": String(open),
      "aria-label": t("views.schedules.detailsOfName", { name: record.name }),
      onclick: () => handlers.onFirings(record, detailsToggle, details),
    },
    t("views.schedules.details"),
  );
  // A one-time schedule that ran at its moment, as the daemon says (item 723): it read "switched off", as one the owner
  // had switched off, and switching it on was refused.
  const done = view.ran_once === true;
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? t("views.schedules.switchOff") : t("views.schedules.switchOn")} ${record.name}`,
    title: done ? t("views.schedules.itRanOnceAndHas") : record.enabled ? t("views.schedules.switchThisScheduleOff") : t("views.schedules.switchThisScheduleOn"),
    disabled: done,
    onclick: () => handlers.onToggle(record),
  });
  // Its actions at the right (item 1337): Run now (whose words say it does real work, as at its time, under the approvals
  // the row's chips name), Edit, the switch, and "…" for Delete.
  const runNow = el(
    "button",
    { class: "btn ghost sm", type: "button", title: runNowTitle(record, agent), "aria-label": t("views.schedules.runNowName", { name: record.name }), onclick: () => handlers.onRunNow(record) },
    icon("play"),
    t("views.schedules.runNow"),
  );
  const edit = view.brief
    ? el("button", { class: "btn ghost sm", type: "button", "aria-label": t("views.schedules.editName", { name: record.name }), title: t("views.schedules.editTheBriefSSettings"), onclick: () => handlers.onEdit(view) }, t("views.schedules.edit"))
    : el("a", { class: "btn ghost sm", href: `#/settings/schedules/${record.id}`, "aria-label": t("views.schedules.editName", { name: record.name }) }, t("views.schedules.edit"));
  const more = moreButton({
    name: record.name,
    items: () => [{ label: t("views.schedules.delete"), icon: "trash", danger: true, run: () => handlers.onDelete(record) }],
  });
  let next = null;
  if (done) {
    // Grey, as it says nothing of how the run went: the badge of its last status beside it says that.
    next = el("span", { class: "badge b-muted", title: t("views.schedules.aOneTimeScheduleIt") }, t("views.schedules.ranOnce"));
  } else if (!record.enabled) {
    next = el("span", { class: "badge b-muted" }, t("views.schedules.paused"));
  } else if (view.next_local !== null) {
    // A time keeps one line: its date's hyphens let a phone break it as "2026-10-" over "28 05:15" (item 778).
    next = el("span", { class: "nowrap" }, t("views.schedules.nextNextLocal", { next_local: view.next_local }));
  } else {
    next = el("span", null, t("views.schedules.willNotRunAgain"));
  }
  const last =
    view.last_local === null
      ? el("span", null, record.last_status === "" ? t("views.schedules.neverRan") : t("views.schedules.notRunYet"))
      : el("span", { class: "nowrap" }, t("views.schedules.lastLastLocal", { last_local: view.last_local }));
  const row = setRow({
    title: el(
      "span",
      { class: "sched-title" },
      el("span", { class: "row-title" }, record.name),
      view.brief ? el("span", { class: "badge b-info", title: t("views.schedules.dotWritesItEachMorning") }, t("views.schedules.brief")) : null,
      record.thread_id === null ? null : el("span", { class: "badge b-muted", title: t("views.schedules.madeInAChatIt") }, t("views.schedules.loop")),
      // A task the owner taught and asked to repeat (item 666): its runs start with that skill.
      (record.options.skills ?? []).length > 0
        ? el("span", { class: "badge b-info", title: t("views.schedules.itsRunsStartWithThe", { join: record.options.skills.join(", ") }) }, t("views.schedules.taughtTask"))
        : null,
      agent === null ? null : el("span", { class: "badge b-muted", title: t("views.schedules.itsTasksRunAsThis2") }, agent.name),
    ),
    // One line of when and how it went, its parts kept by a dot: a phone wraps it in two lines, not one a part.
    lead: el(
      "div",
      { class: "row-meta when-line" },
      ...joined([
        whenWords(view.when),
        next,
        last,
        lastStatusBadge(record.last_status),
        failedInRow(record),
        skippedInRow(record),
        pausedBadge(agent),
        view.zone === localZone() ? null : el("span", { class: "faint", title: t("views.schedules.theTimeZoneThisSchedule") }, view.zone),
      ]),
      detailsToggle,
    ),
    control: el("div", { class: "sched-actions" }, runNow, edit, toggle, more),
    body: details,
  });
  row.dataset.schedule = record.id;
  return row;
}

/** When a schedule runs, in words: "Every day at 08:00" for a repeating one the page can read, else what the daemon says
 * ("cron 5 4 1 * *", "once, 25 Oct 08:00"), the cron form in the tooltip (item 625). */
function whenWords(when) {
  const cron = when.startsWith("cron ") ? when.slice("cron ".length) : null;
  const said = cron === null ? null : loopRhythm(cron);
  const text = said === null ? (cron === null ? `${when.charAt(0).toUpperCase()}${when.slice(1)}` : t("views.schedules.onARepeatingSchedule")) : `${said.charAt(0).toUpperCase()}${said.slice(1)}`;
  return el("span", cron === null ? { title: t("views.schedules.whenItRuns") } : { title: `cron ${cron}`, "data-expert": "" }, text);
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

// The controls whose state says whether the editor's form was changed (formflow.js `stateOfControls`, as a dialog's form).
const FORM_CONTROLS = "input, textarea, select, [role='switch'], [aria-pressed]";

/**
 * The fields that create a schedule (`view` null) or change one, for the editor's page (items 615 and 1337). Returns
 * `sections`, the rows of the form in their sections; `submit()`, which asks the daemon and resolves to `false` when a
 * field the schedule needs is empty (the field takes the focus) and throws when the daemon refuses; `ready`, which settles
 * once the agents and the models the selects offer are read; `zones`, the list the time zone field suggests; and `state()`,
 * what the fields hold now, to tell a form that was changed.
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
          precheck: null,
        }
      : record.options;
  const now = defaultsNow();

  const name = el("input", { class: "input", type: "text", maxlength: "120", autocomplete: "off", placeholder: t("views.schedules.morningBriefing"), "aria-label": t("views.schedules.name") });
  const goal = el("textarea", {
    class: "textarea",
    rows: record === null ? "3" : "6",
    placeholder: t("views.schedules.whatShouldDotDoEach"),
    "aria-label": t("views.schedules.goal"),
  });
  const mode = select(t("views.schedules.mode"), MODES, record === null ? "task" : record.mode);
  const approvals = select(
    t("views.schedules.approvals"),
    [{ id: "", label: t("views.schedules.default") }, ...APPROVAL_OPTIONS.map((option) => ({ id: option.id, label: `${option.label} — ${option.hint}` }))],
    options.approvals ?? "",
  );
  // The owner's agent its runs run as (item 462): Dot, or one of theirs, whose model, reasoning and approvals its runs
  // take where the schedule leaves them on Default. The list comes from the daemon; until then the schedule's own.
  const agent = el("select", { class: "select", "aria-label": t("views.schedules.agent") }, el("option", { value: "" }, "Dot"));
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
      first.textContent = t("views.schedules.defaultTheAgentSApprovallabel", { approvalLabel: approvalLabel(chosen.approvals) });
    } else {
      first.textContent = now.approvals === null ? t("views.schedules.default") : t("views.schedules.defaultApprovallabel", { approvalLabel: approvalLabel(now.approvals) });
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
    t("views.schedules.goalMode"),
    [
      { id: "", label: now.goalMode === null ? t("views.schedules.default") : now.goalMode ? t("views.schedules.defaultGoalModeOn") : t("views.schedules.defaultGoalModeOff") },
      { id: "on", label: t("views.schedules.onDotChecksItsAnswer") },
      { id: "off", label: t("views.schedules.off") },
    ],
    options.goal_mode === null ? "" : options.goal_mode ? "on" : "off",
  );
  const counts = [...new Set([...HISTORY_COUNTS, ...(options.history_runs === null ? [] : [options.history_runs])])].sort((a, b) => a - b);
  const history = select(
    t("views.schedules.earlierTasksItKnows"),
    [
      { id: "", label: now.historyRuns === null ? t("views.schedules.default") : t("views.schedules.defaultHistorylabel", { historyLabel: historyLabel(now.historyRuns) }) },
      ...counts.map((count) => ({ id: String(count), label: historyLabel(count) })),
    ],
    options.history_runs === null ? "" : String(options.history_runs),
  );
  const stops = [...new Set([...STOP_COUNTS, ...(options.stop_after_failures === null ? [] : [options.stop_after_failures])])].sort(
    (a, b) => a - b,
  );
  const stopAfter = select(
    t("views.schedules.switchOffAfter"),
    [
      { id: "", label: now.stopAfterFailures === null ? t("views.schedules.default") : t("views.schedules.defaultStoplabel", { stopLabel: stopLabel(now.stopAfterFailures) }) },
      ...stops.map((count) => ({ id: String(count), label: stopLabel(count) })),
    ],
    options.stop_after_failures === null ? "" : String(options.stop_after_failures),
  );
  const notify = select(
    t("views.schedules.notifications"),
    [
      { id: "", label: now.notify === null ? t("views.schedules.default") : t("views.schedules.defaultNotifylabel", { notifyLabel: notifyLabel(now.notify) }) },
      ...NOTIFY_LEVELS.map(({ id, label }) => ({ id, label })),
    ],
    options.notify ?? "",
  );
  const chat = select(
    t("views.schedules.chat"),
    [
      { id: "", label: now.sameChat === null ? t("views.schedules.default") : t("views.schedules.defaultChatlabel", { chatLabel: chatLabel(now.sameChat) }) },
      { id: "new", label: chatLabel(false) },
      { id: "same", label: chatLabel(true) },
    ],
    options.same_chat === null ? "" : options.same_chat ? "same" : "new",
  );
  const model = el(
    "select",
    { class: "select", "aria-label": t("views.schedules.model") },
    el("option", { value: "" }, now.model === null ? t("views.schedules.defaultModel") : t("views.schedules.defaultModelname", { modelName: modelName(now.model) })),
    options.model === null ? null : el("option", { value: options.model }, options.model),
  );
  model.value = options.model ?? "";
  const effort = el("select", { class: "select", "aria-label": t("views.schedules.reasoningLevel") }, el("option", { value: "" }, t("views.schedules.default")));
  const effortRow = setRow({ title: t("views.schedules.reasoning"), lead: t("views.schedules.howMuchTheModelThinks"), control: effort });
  effortRow.hidden = true;
  let models = [];

  function paintEffort() {
    const info = models.find((candidate) => candidate.ref === model.value) ?? null;
    const keep = effort.value === "" ? options.effort ?? "" : effort.value;
    if (info === null || info.efforts.length < 2) {
      effort.replaceChildren(el("option", { value: "" }, t("views.schedules.default")));
      effort.value = "";
      effortRow.hidden = true;
      return;
    }
    effort.replaceChildren(
      el("option", { value: "" }, info.default_effort === null ? t("views.schedules.default") : t("views.schedules.defaultLevellabel", { levelLabel: levelLabel(info.default_effort) })),
      ...info.efforts.map((level) => el("option", { value: level }, levelLabel(level))),
    );
    effort.value = info.efforts.includes(keep) ? keep : "";
    effortRow.hidden = false;
  }

  model.addEventListener("change", paintEffort);
  const modelsRead = loadModels()
    .then((providers) => {
      models = allModels(providers).filter((candidate) => candidate.tools);
      const chosen = model.value;
      const known = new Set(models.map((candidate) => candidate.ref));
      model.replaceChildren(
        model.firstElementChild,
        ...(chosen !== "" && !known.has(chosen) ? [el("option", { value: chosen }, t("views.schedules.chosenNotOfferedNow", { chosen }))] : []),
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
    placeholder: t("views.schedules.dotSOwnTimeZone"),
    "aria-label": t("views.schedules.timeZone"),
  });
  zone.value = options.timezone ?? "";

  const cron = el("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "0 9 * * 1-5", "aria-label": t("views.schedules.customTiming") });
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
  const cronRow = setRow({
    title: t("views.schedules.customTiming"),
    lead: t("views.schedules.fivePartsSeparatedBySpaces"),
    control: cron,
    body: el("div", { class: "set-row-body" }, presets),
  });
  const at = el("input", { class: "input", type: "datetime-local", "aria-label": t("views.schedules.dateAndTime") });
  const atRow = setRow({ title: t("views.schedules.dateAndTime"), lead: t("views.schedules.readInTheScheduleS"), control: at });
  const startsOnce = record !== null && record.cron === null;
  const repeats = segmented(REPEATS, startsOnce ? "once" : "cron", (choice) => {
    cronRow.hidden = choice !== "cron";
    atRow.hidden = choice !== "once";
    precheckRow.hidden = choice !== "cron";
  });
  // A check that runs first, without a model (item 1855): a file name in Dot's checks folder; a repeating schedule only.
  const precheck = el("input", {
    class: "input mono",
    type: "text",
    maxlength: "80",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "price-drop.sh",
    "aria-label": t("views.schedules.check"),
  });
  precheck.value = options.precheck ?? "";
  const precheckRow = setRow({
    title: t("views.schedules.checkFirst"),
    lead: t("views.schedules.optionalTheFileNameOf"),
    control: precheck,
  });
  cronRow.hidden = startsOnce;
  atRow.hidden = !startsOnce;
  precheckRow.hidden = startsOnce;
  const enabled = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(record === null || record.enabled), "aria-label": t("views.schedules.switchedOn") });
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
      precheck: recurring && precheck.value.trim() !== "" ? precheck.value.trim() : null,
      // The form has no speed of its own: a schedule keeps the one its chat gave it (item 291).
      speed: options.speed,
    };
    if (record === null) {
      // The page makes the form anew for the next one, its choices back at their defaults (item 559).
      await api.post("/schedules", body);
      toast("ok", t("views.schedules.scheduleAdded"));
    } else {
      await api.patch(`/schedules/${record.id}`, enabledTouched ? { ...body, enabled: enabled.getAttribute("aria-checked") === "true" } : body);
      toast("ok", t("views.schedules.scheduleSaved"));
    }
    return true;
  }

  // The fields stand as the settings' rows, in sections (item 1337): what it does, when, how its tasks run, and what they
  // leave. A control with room to spare goes beside its title; the goal and the custom timing go under theirs.
  const sections = [
    setSection({
      title: t("views.schedules.task"),
      body: setCard(
        setRow({ title: t("views.schedules.name"), lead: t("views.schedules.whatTheScheduleIsCalled"), control: name }),
        setRow({ title: t("views.schedules.goal"), lead: t("views.schedules.whatDotDoesEachTime"), body: el("div", { class: "set-row-body" }, goal) }),
        setRow({ title: t("views.schedules.agent"), lead: t("views.schedules.itsTasksRunAsThis3"), control: agent }),
      ),
    }),
    setSection({
      title: t("views.schedules.when"),
      body: setCard(
        setRow({ title: t("views.schedules.repeat"), lead: t("views.schedules.aRepeatingScheduleStartsAgain"), control: repeats.element }),
        cronRow,
        atRow,
        precheckRow,
        setRow({ title: t("views.schedules.timeZone"), lead: t("views.schedules.aNameSuchAsEurope"), control: zone }),
        record === null
          ? null
          : switchRow(setRow({ title: t("views.schedules.switchedOn"), lead: t("views.schedules.aScheduleThatIsPaused"), control: enabled }), enabled),
      ),
    }),
    setSection({
      title: t("views.schedules.howItsTasksRun"),
      body: setCard(
        setRow({ title: t("views.schedules.mode"), lead: t("views.schedules.scheduledTasksWorkOnTheir"), control: mode }),
        setRow({ title: t("views.schedules.model"), lead: t("views.schedules.theModelEveryStepOf"), control: model }),
        effortRow,
        setRow({ title: t("views.schedules.approvals"), lead: t("views.schedules.tasksThatWorkByThemselves"), control: approvals }),
        setRow({ title: t("views.schedules.goalMode"), lead: t("views.schedules.dotChecksItsAnswerAnd"), control: goalMode }),
      ),
    }),
    setSection({
      title: t("views.schedules.whatItsTasksLeave"),
      body: setCard(
        setRow({ title: t("views.schedules.earlierTasksItKnows"), lead: t("views.schedules.eachTaskStartsWithThe"), control: history }),
        setRow({ title: t("views.schedules.chat"), lead: t("views.schedules.inTheSameChatEach"), control: chat }),
        setRow({ title: t("views.schedules.notifications"), lead: t("views.schedules.howTheEndOfIts2"), control: notify }),
        setRow({ title: t("views.schedules.switchOffAfter"), lead: t("views.schedules.aRepeatingScheduleWhoseTasks"), control: stopAfter }),
      ),
    }),
  ];
  const state = () => stateOfControls(sections.flatMap((section) => [...section.querySelectorAll(FORM_CONTROLS)]));
  return { sections, submit, ready: Promise.all([agentsRead, modelsRead]), zones: zoneList(), state };
}

/** The schedules `views` that a chat's loops are (made in a chat, item 281) and the rest. */
function splitLoops(views) {
  return { loops: views.filter((view) => view.record.thread_id !== null), plain: views.filter((view) => view.record.thread_id === null) };
}

function mountList(root, ctx) {
  let disposed = false;
  // Grows with every load, so a slow answer never overwrites a newer one.
  let ticket = 0;
  let views = null;
  // The schedules whose Firings are open: they stay open, and are read again, as the list is painted again.
  const openFirings = new Set();
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  // The schedule a line of a conversation led here to (item 665, `#/settings/schedules?schedule=ID`): marked and in sight
  // once, when the list is first painted; the chat whose tasks a panel's "open all" asks for (`?thread=ID`) is the only
  // one shown.
  let askedId = params.get("schedule") ?? "";
  const thread = params.get("thread") ?? "";
  const holder = el("div", { class: "set-sections" }, loadingPanel());
  // The form that adds a schedule is a page of its own (item 1337); the list is what this page is for.
  const addButton = el("a", { class: "btn primary sm", href: "#/settings/schedules/new" });
  markOpener(addButton, t("views.schedules.newSchedule"));
  ctx.head.setLead(t("views.schedules.goalsThatDotWorksOn"));
  ctx.head.setActions(addButton);
  const schedulerNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, t("views.schedules.schedulesArePausedSoNothing")));
  const filterNote = el(
    "div",
    { class: "note", hidden: thread === "" },
    icon("refresh"),
    el("span", null, t("views.schedules.showingTheTasksThatRepeat")),
    el("a", { class: "btn ghost sm", href: "#/settings/schedules" }, t("views.schedules.showAll")),
  );
  // The brief's card reads again whenever the list does: a switch in the list, a run's end, a failure that switched it off.
  const brief = mountBrief(ctx, () => reload());
  const checkin = mountCheckin(() => reload());
  // The watchers (item 571), under the brief: what Dot tells you of when it happens.
  const watchers = mountWatchers();
  // Dot's own initiative (item 715), under the watchers: what Dot does for you on its own while the Mac is idle.
  const initiative = mountInitiative();
  // What you do with the tells (item 1743), under the initiative: it only counts.
  const tells = mountTells();
  const others = thread === "" ? [brief.element, checkin.element, watchers.element, initiative.element, tells.element] : [];

  function listOf(shown, handlers) {
    return setCard(...shown.map((view) => scheduleRow(view, handlers)));
  }

  function paint() {
    if (views === null) {
      return;
    }
    const chosen = thread === "" ? views : views.filter((view) => view.record.thread_id === thread);
    const { loops, plain } = splitLoops(chosen);
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
      onEdit: () => brief.open(),
      onRunNow: (record) => attempt(() => runNow(record)),
      onFirings: (record, button, list) => attempt(() => toggleFirings(record, button, list)),
      firingsOpen: (id) => openFirings.has(id),
    };
    const sections = [];
    if (thread === "") {
      sections.push(
        setSection({
          title: t("views.schedules.schedules"),
          lead: plain.length === 0 ? "" : tCount(plain.length, "views.schedules.countSchedule", "views.schedules.countSchedules"),
          body:
            plain.length === 0
              ? el("div", { class: "set-card" }, emptyPanel("calendar", t("views.schedules.noSchedulesYet"), t("views.schedules.addOneWithNewSchedule")))
              : listOf(plain, handlers),
        }),
      );
    }
    if (loops.length > 0 || thread !== "") {
      sections.push(
        setSection({
          title: thread === "" ? t("views.schedules.loops") : t("views.schedules.repeatingInThisChat"),
          lead: t("views.schedules.madeInAChatWith"),
          body: loops.length === 0 ? el("div", { class: "set-card" }, emptyPanel("refresh", t("views.schedules.nothingRepeatsInThisChat"), t("views.schedules.writeLoop30mMessageIn"))) : listOf(loops, handlers),
        }),
      );
    }
    holder.replaceChildren(...sections);
    const asked = askedId === "" ? null : holder.querySelector(`[data-schedule="${CSS.escape(askedId)}"]`);
    askedId = "";
    if (asked !== null) {
      asked.classList.add("asked");
      asked.scrollIntoView({ block: "nearest" });
    }
    for (const list of holder.querySelectorAll("[data-firings]")) {
      if (openFirings.has(list.dataset.firings)) {
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

  // A row's Details: how its tasks start, its goal, and its history, read when they open.
  async function toggleFirings(record, button, details) {
    const open = !openFirings.has(record.id);
    if (open) {
      openFirings.add(record.id);
    } else {
      openFirings.delete(record.id);
    }
    button.setAttribute("aria-expanded", String(open));
    details.hidden = !open;
    if (open) {
      const list = details.querySelector("[data-firings]");
      list.replaceChildren(loadingPanel());
      await loadFirings(record.id, list);
    }
  }

  // No question first: the button says what it starts, and its run's page, where it can be stopped, opens at once.
  async function runNow(record) {
    const run = await api.post(`/schedules/${record.id}/run`, null);
    toast("ok", t("views.schedules.startedName", { name: record.name }));
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
      attempt(checkin.reload);
      attempt(initiative.reload);
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      if (views === null) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      } else {
        readFailed("readFailed.schedules", error);
      }
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await reload();
  }

  async function toggle(record) {
    await api.post(`/schedules/${record.id}/${record.enabled ? "disable" : "enable"}`, null);
    await reload();
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: t("views.schedules.deleteThisSchedule"),
      message: t("views.schedules.clipWillNotRunAgain", { clip: clip(record.name, 80) }),
      confirmLabel: t("views.schedules.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/schedules/${record.id}`);
    toast("ok", t("views.schedules.scheduleDeleted"));
    await reload();
  }

  const reloadSoon = debounce(() => {
    if (!disposed) {
      reload();
    }
  }, RELOAD_DELAY_MS);

  const status = getSnapshot().status;
  schedulerNote.hidden = status === null || status.scheduler_running;
  ctx.setTitle(t("views.schedules.scheduledTasksSettings"));
  root.append(schedulerNote, filterNote, holder, ...others, zoneList());
  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      reloadSoon();
    }
  });
  const stopBack = onStreamBack(reloadSoon);
  attempt(reload);
  return () => {
    disposed = true;
    brief.dispose();
    checkin.dispose();
    watchers.dispose();
    initiative.dispose();
    tells.dispose();
    stopLifecycle();
    stopBack();
  };
}

/** The editor of one schedule (`#/settings/schedules/<id>`) or of a new one (`#/settings/schedules/new`, `id` is "new"): the
 * form's sections, and at their foot Cancel and the button that saves; saved, it goes back to the list. Run now and Delete
 * of an existing one stand at the head. */
function mountEditor(root, ctx, id) {
  let disposed = false;
  const list = "/settings/schedules";
  const holder = el("div", { class: "set-sections" }, loadingPanel());
  root.append(holder);
  // Set once the form is drawn: whether the owner may leave with what the fields hold (a changed form asks). The router
  // asks it before it swaps the page, whatever changed the address (`ctx.onBeforeLeave`, item 1337), the browser asks
  // before the tab closes. `saved` is a form that was saved or a schedule that was deleted, `leaving` a way out the owner
  // confirmed already (Run now).
  let saved = false;
  let leaving = false;
  // True at once when nothing needs asking, else the owner's answer to the question on screen.
  /** @type {() => boolean | Promise<boolean>} */
  let asksBeforeLeaving = () => true;
  let onClose = null;
  const stopAsking = ctx.onBeforeLeave(() => asksBeforeLeaving());
  ctx.setTitle(id === "new" ? t("views.schedules.newScheduleSettings") : t("views.schedules.editScheduleSettings"));
  ctx.setCrumb(id === "new" ? t("views.schedules.newSchedule") : t("views.schedules.editSchedule"));

  async function open() {
    let view = null;
    if (id !== "new") {
      view = await api.get(`/schedules/${encodeURIComponent(id)}`);
      if (disposed) {
        return;
      }
      if (view.brief) {
        // The morning brief is set by its card in the list, not by this form.
        ctx.navigate(list);
        return;
      }
      ctx.setCrumb(clip(view.record.name, 60));
    }
    const form = scheduleForm({ view });
    const record = view === null ? null : view.record;
    ctx.head.setLead(
      record === null
        ? t("views.schedules.dotWorksOnTheGoal")
        : t("views.schedules.changeItHereItsNext"),
    );
    const problem = el("div", { class: "note bad", role: "alert", hidden: true });
    const save = el("button", { class: "btn primary", type: "button" }, record === null ? t("views.schedules.addSchedule") : t("views.schedules.saveChanges"));
    const cancel = el("a", { class: "btn ghost", href: `#${list}` }, t("views.schedules.cancel"));
    // What the owner has typed or picked, against the fields as they stood when the page opened (and, once the agents and
    // the models the selects offer are read, as they stood then, unless a field was touched by then).
    let baseline = form.state();
    let touched = false;
    for (const kind of ["input", "change", "click"]) {
      holder.addEventListener(kind, () => {
        touched = true;
      });
    }
    const changed = () => !saved && !leaving && form.state() !== baseline;
    asksBeforeLeaving = () => {
      if (!changed()) {
        return true;
      }
      return confirmDialog({
        title: t("dialog.discardTitle"),
        message: t("dialog.discardMessage"),
        confirmLabel: t("dialog.discard"),
        cancelLabel: t("dialog.keepEditing"),
        danger: true,
        focusCancel: true,
      });
    };
    // Whatever leaves the page with a changed field asks first, through the router: a link (the nav, the breadcrumb,
    // Cancel), the bar's Back, Enter in the settings' search, Connect apps, a shortcut and the browser's own Back, which
    // has changed the address already and is put back when the owner keeps editing. Closing or reloading the tab is
    // asked by the browser; an address typed in the bar that loads the page anew cannot be held back.
    onClose = (event) => {
      if (changed()) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", onClose);
    save.addEventListener("click", async () => {
      save.disabled = true;
      problem.hidden = true;
      try {
        if ((await form.submit()) !== false) {
          saved = true;
          ctx.navigate(list);
          return;
        }
      } catch (error) {
        problem.textContent = explain(error).text;
        problem.hidden = false;
      }
      save.disabled = false;
    });
    if (record !== null) {
      ctx.head.setActions(
        el("button", { class: "btn ghost sm", type: "button", onclick: () => attempt(() => runNow(record)) }, icon("play"), t("views.schedules.runNow")),
        el("button", { class: "btn ghost sm danger", type: "button", onclick: () => attempt(() => remove(record)) }, icon("trash"), t("views.schedules.delete")),
      );
    }
    holder.replaceChildren(...form.sections, problem, el("div", { class: "set-savebar" }, cancel, save), form.zones);
    baseline = form.state();
    touched = false;
    const settled = () => {
      if (!touched && !disposed) {
        baseline = form.state();
      }
    };
    await form.ready.then(settled, settled);
  }

  async function runNow(record) {
    // Run now starts the schedule as it is saved: the page, and what was typed in it, is left for the run, so the
    // question comes before the run starts, and the router does not ask it again on the way to the run's page.
    if (!(await asksBeforeLeaving())) {
      return;
    }
    leaving = true;
    try {
      const run = await api.post(`/schedules/${record.id}/run`, null);
      toast("ok", t("views.schedules.startedName", { name: record.name }));
      ctx.navigate(`/runs/${run.id}`);
    } catch (error) {
      leaving = false;
      throw error;
    }
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: t("views.schedules.deleteThisSchedule"),
      message: t("views.schedules.clipWillNotRunAgain", { clip: clip(record.name, 80) }),
      confirmLabel: t("views.schedules.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    await api.del(`/schedules/${record.id}`);
    // The schedule is gone: there is nothing left to keep what was typed for.
    saved = true;
    toast("ok", t("views.schedules.scheduleDeleted"));
    ctx.navigate(list);
  }

  attempt(async () => {
    try {
      await open();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(open)));
      }
    }
  });
  return () => {
    disposed = true;
    stopAsking();
    if (onClose !== null) {
      window.removeEventListener("beforeunload", onClose);
    }
  };
}

/** Scheduled tasks (`#/settings/schedules`), a schedule's editor (`#/settings/schedules/<id>`) and the form of a new one
 * (`#/settings/schedules/new`). `ctx.params` is `["schedules", id]`. */
export function mount(root, ctx) {
  const id = ctx.params[1];
  return id === undefined ? mountList(root, ctx) : mountEditor(root, ctx, id);
}
