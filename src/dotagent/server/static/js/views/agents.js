// Agents (item 86): the profiles the owner makes, each with a face in its own hue, a name, a role in one line,
// instructions that open the prompt of its runs, and bounds for them (a model, a reasoning level, approvals, tools).
// The list (#/agents) shows Dot first and then the agents, each with its newest chat; an agent's page (#/agents/<id>)
// greets with its face and role, takes a message for it and lists its chats; the editor makes or changes an agent.

import { actorFace, dotFace, fallbackLook, rememberLook } from "../actors.js";
import { api } from "../api.js";
import { wireComposer } from "../composer.js";
import { ago, clip, el, modelName } from "../dom.js";
import { allModels, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { lookPicker } from "../lookpicker.js";
import { emptyPanel, errorPanel, field, loadingPanel, pageHead, statusBadge } from "../panels.js";
import { APPROVAL_OPTIONS } from "../runmodes.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { scoreLine } from "./memory.js";
import { skillRow } from "./skills.js";
import { messageRow } from "./team.js";
import { getSnapshot, loadModels, onLifecycle } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const DOT_ID = "dot";
const GOAL_CHARS = 90;
const MAX_GOAL_PX = 320;
// The face an agent wears in its row: working while its newest run works, at rest otherwise.
const WORKING = new Set(["queued", "running", "blocked"]);

function faceOf(record, latest) {
  if (record.id === DOT_ID) {
    return dotFace("idle");
  }
  return actorFace(record.id, latest !== null && WORKING.has(latest.status) ? "working" : "idle", record.look);
}

function hrefOf(record) {
  return record.id === DOT_ID ? "#/runs" : `#/agents/${record.id}`;
}

/** What the agent's row says under its name: its newest chat, else its role. */
function secondLine(record, latest) {
  if (latest === null) {
    return el("div", { class: "row-text muted" }, record.role === "" ? "No chats yet" : record.role);
  }
  return el("div", { class: "row-text muted" }, clip(latest.goal, GOAL_CHARS));
}

function agentRow({ record, latest }) {
  return el(
    "a",
    { class: "list-row agent-row", href: hrefOf(record), dataset: { agent: record.id } },
    faceOf(record, latest),
    el("div", { class: "row-main" }, el("div", { class: "row-title" }, record.name), secondLine(record, latest)),
    record.made_by === null ? null : el("span", { class: "agent-made" }, "Made by Dot"),
    latest === null ? null : el("span", { class: "agent-when" }, ago(latest.created_at)),
  );
}

// ------------------------------------------------------------------------------------------------- the editor

function select(label, entries, value) {
  const element = el(
    "select",
    { class: "select", "aria-label": label },
    entries.map((entry) => el("option", { value: entry.id }, entry.label)),
  );
  element.value = value;
  return element;
}

/** The checkboxes of the tools; none checked means every tool. */
function toolPicks(names, chosen) {
  const picked = new Set(chosen);
  return el(
    "div",
    { class: "tool-picks" },
    names.map((name) =>
      el(
        "label",
        { class: "tool-pick" },
        el("input", { type: "checkbox", value: name, checked: picked.has(name) ? "" : null }),
        el("span", null, name),
      ),
    ),
  );
}

/**
 * The form that makes an agent (`record` null) or changes one. `onSaved(record)` runs with the saved agent; `onCancel`
 * closes the form.
 */
function agentForm({ record, onSaved, onCancel }) {
  const defaults = getSnapshot().status;
  const name = el("input", { class: "input", type: "text", maxlength: "60", autocomplete: "off", placeholder: "Scout", "aria-label": "Name" });
  const role = el("input", { class: "input", type: "text", maxlength: "120", autocomplete: "off", placeholder: "Finds and checks sources", "aria-label": "Role" });
  const instructions = el("textarea", {
    class: "textarea",
    rows: "5",
    placeholder: "How this agent works, what it cares about, what it never does. It reads this at the start of every chat.",
    "aria-label": "Instructions",
  });
  const model = el(
    "select",
    { class: "select", "aria-label": "Model" },
    el("option", { value: "" }, defaults === null ? "Default model" : `Default (${modelName(defaults.models.main)})`),
    record === null || record.model === "" ? null : el("option", { value: record.model }, record.model),
  );
  model.value = record === null ? "" : record.model;
  const effort = el("select", { class: "select", "aria-label": "Reasoning level" }, el("option", { value: "" }, "Default"));
  const effortField = field("Reasoning", effort, "");
  effortField.hidden = true;
  const approvals = select(
    "Approvals",
    [{ id: "", label: "Default" }, ...APPROVAL_OPTIONS.map((option) => ({ id: option.id, label: `${option.label}: ${option.hint}` }))],
    record === null || record.approvals === null ? "" : record.approvals,
  );
  // The look (item 102): the agent's own, or for a new one the fallback of a face not yet named.
  const look = lookPicker({ look: record === null || record.look === null ? fallbackLook(record === null ? "new" : record.id) : record.look, uid: record === null ? "new" : record.id });
  const lookField = field("Look", look.element, "The face this agent wears everywhere; it moves on its own page and beside its chats.");
  lookField.classList.add("wide");
  const tools = el("div", null, el("span", { class: "muted" }, "Loading the tools"));
  const toolsField = field("Tools", tools, "None picked: every tool. Finishing, asking you and turning tool groups on stay in any case.");
  toolsField.classList.add("wide");
  let models = [];

  function paintEffort() {
    const info = models.find((candidate) => candidate.ref === model.value) ?? null;
    const keep = effort.value === "" ? (record === null ? "" : record.effort ?? "") : effort.value;
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
        ...models.map((candidate) => el("option", { value: candidate.ref }, modelName(candidate.ref))),
      );
      model.value = chosen;
      paintEffort();
    })
    .catch((error) => {
      console.warn("the models could not be listed", error);
    });
  api
    .get("/tools")
    .then((names) => {
      tools.replaceChildren(toolPicks(names, record === null ? [] : record.tools));
    })
    .catch((error) => {
      tools.replaceChildren(el("span", { class: "muted" }, `The tools could not be listed: ${error.message}`));
    });
  if (record !== null) {
    name.value = record.name;
    role.value = record.role;
    instructions.value = record.instructions;
  }
  const save = el("button", { class: "btn primary", type: "button" }, icon(record === null ? "plus" : "check"), record === null ? "Create agent" : "Save changes");
  const cancel = el("button", { class: "btn ghost", type: "button", onclick: onCancel }, "Cancel");

  async function submit() {
    const nameText = name.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    const body = {
      name: nameText,
      role: role.value.trim(),
      instructions: instructions.value.trim(),
      model: model.value,
      effort: effort.value === "" ? null : effort.value,
      approvals: approvals.value === "" ? null : approvals.value,
      tools: [...tools.querySelectorAll("input:checked")].map((input) => input.value),
      look: look.value(),
    };
    save.disabled = true;
    try {
      const saved = record === null ? await api.post("/agents", body) : await api.put(`/agents/${record.id}`, body);
      rememberLook(saved.id, saved.look);
      toast("ok", record === null ? `${saved.name} is ready.` : "Agent saved.");
      onSaved(saved);
    } finally {
      save.disabled = false;
    }
  }

  save.addEventListener("click", () => attempt(submit));
  const element = el(
    "section",
    { class: "card agent-editor", "aria-label": record === null ? "New agent" : "Edit agent" },
    el("div", { class: "card-head" }, el("span", null, record === null ? "New agent" : `Edit ${record.name}`)),
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "form-grid" },
        field("Name", name, ""),
        field("Role", role, "One line, as it shows under the name."),
        Object.assign(field("Instructions", instructions, ""), { className: "field wide" }),
        lookField,
        field("Model", model, ""),
        effortField,
        field("Approvals", approvals, ""),
        toolsField,
      ),
      el("div", { class: "cluster end" }, cancel, save),
    ),
  );
  return { element, focus: () => name.focus(), dispose: look.dispose };
}

// ------------------------------------------------------------------------------------------------- the list

function mountList(root, ctx) {
  let disposed = false;
  let ticket = 0;
  const holder = el("div", null, loadingPanel());
  const editorSlot = el("div", null);

  async function reload() {
    const mine = ++ticket;
    try {
      const summaries = await api.get("/agents");
      if (disposed || mine !== ticket) {
        return;
      }
      for (const { record } of summaries) {
        rememberLook(record.id, record.look);
      }
      holder.replaceChildren(el("div", { class: "card" }, summaries.map(agentRow)));
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  let editor = null;

  function closeEditor() {
    if (editor !== null) {
      editor.dispose();
      editor = null;
    }
    editorSlot.replaceChildren();
  }

  function openEditor() {
    closeEditor();
    editor = agentForm({
      record: null,
      onSaved: (saved) => ctx.navigate(`/agents/${saved.id}`),
      onCancel: closeEditor,
    });
    editorSlot.replaceChildren(editor.element);
    editor.focus();
  }

  const newButton = el("button", { class: "btn primary", type: "button", onclick: openEditor }, icon("plus"), el("span", { class: "label" }, "New agent"));
  const teamLink = el("a", { class: "btn ghost", href: "#/team", title: "Today's messages between agents" }, icon("branch"), el("span", { class: "label" }, "Team"));
  root.append(
    pageHead("Agents", "Dot, and the agents you made: each with a face, a role and its own chats.", el("div", { class: "cluster" }, teamLink, newButton)),
    editorSlot,
    holder,
  );
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
    }
  });
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
    closeEditor();
  };
}

// ------------------------------------------------------------------------------------------------- the page

function runRow(run) {
  return el(
    "div",
    { class: "list-row", dataset: { run: run.id } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("a", { class: "row-title", href: `#/runs/${run.id}` }, clip(run.goal, GOAL_CHARS)), statusBadge(run.status)),
      el("div", { class: "row-meta" }, el("span", null, ago(run.created_at)), el("span", null, modelName(run.model))),
    ),
  );
}

/** The composer of an agent's page: a message for the agent, sent with Enter; the run takes the agent's bounds. */
function agentComposer(record, ctx) {
  const goal = el("textarea", { class: "textarea", rows: "3", placeholder: `Message ${record.name}`, "aria-label": "Message" });
  const start = el(
    "button",
    { class: "btn primary send", type: "button", title: "Start (Enter)", "aria-label": "Start" },
    icon("arrowUp"),
    el("span", { class: "label" }, "Start"),
  );
  let starting = false;

  async function submit() {
    const text = goal.value.trim();
    if (starting || text === "") {
      goal.focus();
      return;
    }
    starting = true;
    start.disabled = true;
    try {
      await attempt(async () => {
        const created = await api.post("/runs", {
          goal: text,
          mode: "task",
          model: null,
          effort: null,
          approvals: null,
          goal_mode: null,
          standing: false,
          thread_id: null,
          attachments: [],
          agent_id: record.id,
        });
        ctx.navigate(`/runs/${created.id}`);
      });
    } finally {
      starting = false;
      start.disabled = false;
    }
  }

  start.addEventListener("click", submit);
  const composer = wireComposer(goal, { maxPx: MAX_GOAL_PX, send: submit, commands: [] });
  const element = el(
    "section",
    { class: "card composer", "aria-label": `Message ${record.name}` },
    composer.menu,
    goal,
    el("div", { class: "composer-bar" }, el("div", { class: "composer-options" }), start),
  );
  return { element, focus: () => goal.focus(), dispose: composer.dispose };
}

/** The agent's greeting: its face, name and role, like Dot's on the home page. */
function who(record) {
  return el(
    "div",
    { class: "home-who agent-who" },
    actorFace(record.id, "idle", record.look),
    el("div", { class: "home-name" }, record.name),
    record.role === "" ? null : el("p", { class: "home-greeting" }, record.role),
    record.made_by === null ? null : el("p", { class: "home-greeting agent-made" }, "Made by Dot in one of its runs"),
  );
}

/** A lesson the agent learned: the note, how the agent's own runs fared after it, and Forget. */
function lessonRow(record, score, onForget) {
  const forget = el("button", { class: "btn ghost sm", type: "button" }, "Forget");
  forget.addEventListener("click", () => onForget(record));
  return el(
    "div",
    { class: "list-row", "data-lesson": record.id },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-text" }, record.content),
      el("div", { class: "row-meta" }, el("span", null, `Saved ${ago(record.created_at)}`), scoreLine(score)),
    ),
    el("div", { class: "row-actions" }, forget),
  );
}

/** What the agent learned for itself (item 94): its lessons, then its skills; empty when it learned nothing yet. */
function learnedSection(record, learned, actions) {
  const parts = [el("div", { class: "section-label" }, "What it learned")];
  if (learned.lessons.length === 0 && learned.skills.length === 0) {
    parts.push(
      emptyPanel(
        "Nothing yet",
        `${record.name}'s lessons and the skills it learns from its own work will be listed here, for its runs alone.`,
      ),
    );
    return parts;
  }
  if (learned.lessons.length > 0) {
    parts.push(
      el("div", { class: "card" }, learned.lessons.map((lesson) => lessonRow(lesson, learned.scores[lesson.id], actions.forget))),
    );
  }
  if (learned.skills.length > 0) {
    parts.push(
      el("div", { class: "section-label" }, "Skills"),
      el(
        "div",
        { class: "card" },
        learned.skills.map((skill) => skillRow({ ...skill, agentName: record.name }, actions.skillsChanged)),
      ),
    );
  }
  return parts;
}

function mountPage(root, ctx, agentId) {
  let disposed = false;
  let ticket = 0;
  let composer = null;
  let form = null;
  const top = el("div", { class: "stack agent-top" });
  const runs = el("div", { class: "stack" });
  const messages = el("div", { class: "stack" });
  const learned = el("div", { class: "stack agent-learned" });
  const page = el("div", { class: "stack" }, loadingPanel());

  function stageOf(record) {
    setStageTitle(actorFace(record.id, "idle", record.look), el("span", { class: "stage-name" }, record.name));
    setStageTools(
      el("button", { class: "btn ghost sm", type: "button", title: "Edit the agent", onclick: () => edit(record) }, icon("pencil"), el("span", { class: "label" }, "Edit")),
      el("button", { class: "btn ghost sm", type: "button", title: "Delete the agent", onclick: () => attempt(() => remove(record)) }, icon("trash"), el("span", { class: "label" }, "Delete")),
    );
  }

  function closeForm() {
    if (form !== null) {
      form.dispose();
      form = null;
    }
  }

  function greet(record) {
    if (composer !== null) {
      composer.dispose();
    }
    closeForm();
    composer = agentComposer(record, ctx);
    top.replaceChildren(who(record), composer.element);
    stageOf(record);
  }

  function edit(record) {
    closeForm();
    form = agentForm({
      record,
      onSaved: () => attempt(reload),
      onCancel: () => greet(record),
    });
    top.replaceChildren(form.element);
    form.focus();
  }

  async function remove(record) {
    const confirmed = await confirmDialog({
      title: `Delete ${record.name}?`,
      message: "Its chats stay, as Dot's. The agent's face, role and instructions are gone.",
      confirmLabel: "Delete",
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await api.del(`/agents/${record.id}`);
      toast("ok", `${record.name} deleted.`);
      ctx.navigate("/agents");
    }
  }

  async function forget(record) {
    const confirmed = await confirmDialog({
      title: "Forget this lesson?",
      message: "The note is removed from memory; no run is handed it again.",
      confirmLabel: "Forget",
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await api.del(`/memories/${record.id}`);
      toast("ok", "Lesson forgotten.");
      await reload();
    }
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const detail = await api.get(`/agents/${agentId}`);
      if (disposed || mine !== ticket) {
        return;
      }
      greet(detail.record);
      learned.replaceChildren(
        ...learnedSection(detail.record, detail.learned, {
          forget: (record) => attempt(() => forget(record)),
          skillsChanged: (list) => {
            if (list !== null) {
              attempt(reload);
            }
          },
        }),
      );
      runs.replaceChildren(
        el("div", { class: "section-label" }, "Chats"),
        detail.runs.length === 0
          ? emptyPanel("No chats yet", `Send ${detail.record.name} a message above.`)
          : el("div", { class: "card" }, detail.runs.map(runRow)),
      );
      messages.replaceChildren(
        ...(detail.messages.length === 0
          ? []
          : [el("div", { class: "section-label" }, "Messages"), el("div", { class: "card" }, detail.messages.map(messageRow))]),
      );
      page.replaceChildren(top, runs, learned, messages);
      composer.focus();
    } catch (error) {
      if (!disposed && mine === ticket) {
        page.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(el("div", { class: "agent-page" }, el("a", { class: "back", href: "#/agents" }, icon("arrowLeft"), "All agents"), page));
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
    }
  });
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
    if (composer !== null) {
      composer.dispose();
    }
    closeForm();
  };
}

export function mount(root, ctx) {
  const agentId = ctx.params[0];
  return agentId === undefined ? mountList(root, ctx) : mountPage(root, ctx, agentId);
}
