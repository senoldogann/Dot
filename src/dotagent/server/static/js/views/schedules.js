// Schedules: goals that Dot carries out by itself, once or on a cron schedule, while the daemon runs. A page of the
// sidebar above Customize. Each schedule keeps how its runs start (mode, approvals, goal mode, model, reasoning level,
// time zone) and can be changed later.

import { actorFace } from "../actors.js";
import { api } from "../api.js";
import { clip, debounce, el, joined, modelName, plural, switchRow } from "../dom.js";
import { allModels, chosenLevel, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, pageHead, paintOpener, segmented } from "../panels.js";
import { APPROVAL_OPTIONS, approvalLabel } from "../runmodes.js";
import { getSnapshot, loadModels, onLifecycle, onStreamBack, readFailed } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const LIFECYCLE_KINDS = new Set(["run_started", "run_finished"]);
const RELOAD_DELAY_MS = 400;
const GOAL_PREVIEW_CHARS = 220;
const REPEATS = [
  { id: "cron", label: "Repeats", hint: "Fires again and again on a schedule" },
  { id: "once", label: "Once", hint: "Fires a single time" },
];
const CRON_PRESETS = [
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Daily at 09:00", cron: "0 9 * * *" },
  { label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays at 09:00", cron: "0 9 * * 1" },
];
const MODES = [
  { id: "task", label: "Task — carry the goal out" },
  { id: "research", label: "Research — read-only" },
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
  } else if (status === "failed") {
    tone = "b-bad";
  } else if (status === "starting" || status === "running") {
    tone = "b-info";
  } else if (status.startsWith("skipped")) {
    tone = "b-warn";
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
  };
}

// How many earlier runs a schedule's firing may know (item 257); a count saved over the API joins them in the menu.
const HISTORY_COUNTS = [0, 1, 3, 5, 10];

function historyLabel(count) {
  return count === 0 ? "None" : count === 1 ? "The last run" : `The last ${count} runs`;
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

/** The chips that say how the schedule's runs start; a choice left open shows what the daemon uses now. */
function optionChips(record) {
  const options = record.options;
  const now = defaultsNow();
  const approvals = options.approvals ?? now.approvals;
  const goalMode = options.goal_mode ?? now.goalMode;
  const model = options.model ?? now.model;
  // Only what this schedule chose for itself: the defaults it takes are the ones every run takes, and saying them on
  // every row buries what is its own (item 373).
  return [
    el("span", { class: "chip", title: "Mode" }, record.mode),
    options.approvals === null
      ? null
      : el(
          "span",
          { class: approvals === "yolo" ? "badge b-warn" : "chip", title: "Who answers the safety policy's questions" },
          `Approvals: ${approvalLabel(approvals)}`,
        ),
    options.goal_mode === null ? null : el("span", { class: "chip" }, `Goal mode ${goalMode ? "on" : "off"}`),
    options.model === null ? null : el("span", { class: "chip", title: model }, modelName(model)),
    options.effort === null ? null : el("span", { class: "chip" }, `Reasoning ${levelLabel(options.effort)}`),
    options.speed === "fast" ? el("span", { class: "chip", title: "Its runs start in fast mode" }, "Fast") : null,
    options.history_runs === null ? null : el("span", { class: "chip", title: "Earlier runs it knows" }, `Knows: ${historyLabel(options.history_runs)}`),
    options.same_chat === null ? null : el("span", { class: "chip" }, chatLabel(options.same_chat)),
  ];
}

function scheduleRow(view, handlers) {
  const { record } = view;
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? "Switch off" : "Switch on"} ${record.name}`,
    title: record.enabled ? "Switch this schedule off" : "Switch this schedule on",
    onclick: () => handlers.onToggle(record),
  });
  const edit = el(
    "button",
    { class: "btn ghost sm icon-only", type: "button", title: "Edit this schedule", "aria-label": `Edit ${record.name}`, onclick: () => handlers.onEdit(view) },
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
  if (!record.enabled) {
    next = el("span", { class: "badge b-muted" }, "switched off");
  } else if (view.next_local !== null) {
    next = el("span", null, `Next ${view.next_local}`);
  } else {
    next = el("span", null, "No further runs");
  }
  const last =
    view.last_local === null
      ? el("span", null, record.last_status === "" ? "Never ran" : "Not run yet")
      : el("span", null, `Last ${view.last_local}`);
  return el(
    "div",
    { class: "list-row", dataset: { schedule: record.id } },
    toggle,
    actorFace(record.id, "idle", null),
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("span", { class: "row-title" }, record.name)),
      el("div", { class: "row-meta" }, optionChips(record)),
      goalText(record.goal),
      // One line of when and how it went, its parts kept by a dot: a phone wraps it in two lines, not one a part.
      el(
        "div",
        { class: "row-meta when-line" },
        ...joined([
          el("span", { class: "mono", title: "When it runs" }, view.when),
          next,
          last,
          lastStatusBadge(record.last_status),
          view.zone === localZone()
            ? null
            : el("span", { class: "faint", title: "The time zone this schedule is read in" }, view.zone),
        ]),
      ),
    ),
    el("div", { class: "row-actions" }, edit, remove),
  );
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
 * The form that creates a schedule (`view` null) or changes one. `onSaved()` runs after the daemon accepted it;
 * `onCancel` is null for the creation form, which stays on the page.
 */
function scheduleForm({ view, onSaved, onCancel }) {
  const record = view === null ? null : view.record;
  const options =
    record === null
      ? { model: null, effort: null, speed: null, approvals: null, goal_mode: null, timezone: null, history_runs: null, same_chat: null }
      : record.options;
  const now = defaultsNow();
  const id = ++formCount;

  const name = el("input", { class: "input", type: "text", maxlength: "120", autocomplete: "off", placeholder: "Morning briefing", "aria-label": "Name" });
  const goal = el("textarea", {
    class: "textarea",
    rows: record === null ? "3" : "6",
    placeholder: "What should Dot do each time? Be as specific as for a normal run.",
    "aria-label": "Goal",
  });
  const mode = select("Mode", MODES, record === null ? "task" : record.mode);
  const approvals = select(
    "Approvals",
    [{ id: "", label: now.approvals === null ? "Default" : `Default (${approvalLabel(now.approvals)})` }, ...APPROVAL_OPTIONS.map((option) => ({ id: option.id, label: `${option.label} — ${option.hint}` }))],
    options.approvals ?? "",
  );
  const goalMode = select(
    "Goal mode",
    [
      { id: "", label: now.goalMode === null ? "Default" : `Default (${now.goalMode ? "on" : "off"})` },
      { id: "on", label: "On — a checker sends the run back until the goal is reached" },
      { id: "off", label: "Off" },
    ],
    options.goal_mode === null ? "" : options.goal_mode ? "on" : "off",
  );
  const counts = [...new Set([...HISTORY_COUNTS, ...(options.history_runs === null ? [] : [options.history_runs])])].sort((a, b) => a - b);
  const history = select(
    "Earlier runs it knows",
    [
      { id: "", label: now.historyRuns === null ? "Default" : `Default (${historyLabel(now.historyRuns)})` },
      ...counts.map((count) => ({ id: String(count), label: historyLabel(count) })),
    ],
    options.history_runs === null ? "" : String(options.history_runs),
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
  loadModels()
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
    placeholder: "The configured zone",
    "aria-label": "Time zone",
  });
  zone.value = options.timezone ?? "";

  const cron = el("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "0 9 * * 1-5", "aria-label": "Cron expression" });
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
    field("Cron expression", cron, "Minute, hour, day of month, month, day of week. Very frequent schedules are refused."),
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
  enabled.addEventListener("click", () => {
    enabled.setAttribute("aria-checked", String(enabled.getAttribute("aria-checked") !== "true"));
  });
  if (record !== null) {
    name.value = record.name;
    goal.value = record.goal;
    cron.value = record.cron ?? "";
    at.value = view.run_at_input ?? "";
  }
  const save = el("button", { class: "btn primary", type: "button" }, icon(record === null ? "plus" : "check"), record === null ? "Add schedule" : "Save changes");
  const cancel = onCancel === null ? null : el("button", { class: "btn ghost", type: "button", onclick: onCancel }, "Cancel");

  async function submit() {
    const nameText = name.value.trim();
    const goalText = goal.value.trim();
    const recurring = repeats.value() === "cron";
    const cronText = cron.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    if (goalText === "") {
      goal.focus();
      return;
    }
    if (recurring && cronText === "") {
      cron.focus();
      return;
    }
    if (!recurring && at.value === "") {
      at.focus();
      return;
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
      // The form has no speed of its own: a schedule keeps the one its chat gave it (item 291).
      speed: options.speed,
    };
    save.disabled = true;
    try {
      if (record === null) {
        await api.post("/schedules", body);
        name.value = "";
        goal.value = "";
        cron.value = "";
        at.value = "";
        toast("ok", "Schedule added.");
      } else {
        await api.patch(`/schedules/${record.id}`, { ...body, enabled: enabled.getAttribute("aria-checked") === "true" });
        toast("ok", "Schedule saved.");
      }
      await onSaved();
    } finally {
      save.disabled = false;
    }
  }

  save.addEventListener("click", () => attempt(submit));
  const element = el(
    "section",
    { class: record === null ? "card card-pad stack" : "stack grow", dataset: { scheduleForm: String(id) } },
    el(
      "div",
      { class: "form-grid" },
      field("Name", name, ""),
      field("Mode", mode, "Scheduled runs work unattended."),
      goalField,
      field("Approvals", approvals, "Unattended runs cannot wait for you: with Ask, a question stops the run until you answer."),
      field("Goal mode", goalMode, ""),
      field("Model", model, "The model every step and every compaction of the run uses, until it moves to a standby model."),
      effortField,
      field("Time zone", zone, "A name such as Europe/Istanbul; empty uses the configured zone."),
      field("Earlier runs it knows", history, "Each run starts with the dated answers of the last runs, to build on them instead of doing the same work again."),
      field("Chat", chat, "In the same chat each run reads the conversation of the runs before it instead."),
      record === null ? null : switchRow(el("div", { class: "field" }, el("span", { class: "field-label" }, "Switched on"), enabled), enabled),
      el("div", { class: "field wide" }, el("span", { class: "field-label" }, "Runs"), el("div", { class: "cluster" }, repeats.element)),
      cronField,
      atField,
    ),
    el("div", { class: "row" }, el("span", { class: "spacer" }), cancel, save),
  );
  return { element };
}

export function mount(root, ctx) {
  let disposed = false;
  // Grows with every load, so a slow answer never overwrites a newer one.
  let ticket = 0;
  let views = null;
  // The id of the schedule whose form is open, or null.
  let editing = null;
  const holder = el("div", null, loadingPanel());
  const count = el("span", { class: "hint" });
  // The form that adds a schedule stays away until New schedule asks for it; the list is what this page is for.
  const formSlot = el("div", { class: "schedule-form-slot", hidden: true });
  const addButton = el("button", { class: "btn primary sm", type: "button" });
  paintOpener(addButton, "New schedule", false);
  addButton.addEventListener("click", () => {
    formSlot.hidden = !formSlot.hidden;
    paintOpener(addButton, "New schedule", !formSlot.hidden);
    if (!formSlot.hidden) {
      formSlot.scrollIntoView({ block: "nearest" });
      formSlot.querySelector("input")?.focus();
    }
  });
  const schedulerNote = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span", null, "The scheduler is not running, so nothing fires until the daemon restarts."));

  function paint() {
    if (views === null || editing !== null) {
      return;
    }
    if (views.length === 0) {
      holder.replaceChildren(emptyPanel("calendar", "No schedules yet", "Add one with New schedule and Dot will run it by itself."));
      return;
    }
    const handlers = {
      onToggle: (record) => attempt(() => toggle(record)),
      onDelete: (record) => attempt(() => remove(record)),
      onEdit: (view) => startEditing(view),
    };
    holder.replaceChildren(el("div", { class: "schedule-list" }, views.map((view) => scheduleRow(view, handlers))));
    count.textContent = plural(views.length, "schedule", "schedules");
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

  // The row of the schedule turns into its form; saving or cancelling paints the list again.
  // While a form is open the list is not painted again, so a run starting or ending does not throw the edit away.
  function startEditing(view) {
    const row = holder.querySelector(`[data-schedule="${view.record.id}"]`);
    if (row === null) {
      return;
    }
    editing = view.record.id;
    const form = scheduleForm({
      view,
      onSaved: async () => {
        editing = null;
        await reload();
      },
      onCancel: () => {
        editing = null;
        paint();
      },
    });
    row.replaceChildren(form.element);
  }

  async function toggle(record) {
    await api.post(`/schedules/${record.id}/${record.enabled ? "disable" : "enable"}`, null);
    await reload();
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: "Delete this schedule?",
      message: `“${clip(record.name, 80)}” will not run again. Runs it started earlier stay in the history.`,
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
  const closeForm = () => {
    formSlot.hidden = true;
    paintOpener(addButton, "New schedule", false);
  };
  const form = scheduleForm({
    view: null,
    onSaved: async () => {
      closeForm();
      await reload();
    },
    onCancel: closeForm,
  });
  formSlot.append(form.element);
  ctx.setTitle("Schedules");
  root.append(
    pageHead("Schedules", "Goals that Dot runs by itself, once or on a repeating schedule.", null),
    el(
      "div",
      { class: "note" },
      icon("clock"),
      el("span", null, "Schedules fire only while the daemon runs and this Mac is awake. A firing that was missed while the daemon was down is skipped, not replayed."),
    ),
    schedulerNote,
    formSlot,
    el(
      "section",
      { class: "memory-panel notes-panel schedule-panel" },
      el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "Your schedules"), count, el("span", { class: "spacer" }), addButton),
      holder,
    ),
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
    stopLifecycle();
    stopBack();
  };
}
