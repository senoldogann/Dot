// Agents (item 86): the profiles the owner makes, each with a face in its own hue, a name, a role in one line,
// instructions that open the prompt of its runs, and bounds for them (a model, a reasoning level, approvals, tools).
// The list (#/agents) shows Dot first and then the agents, each with its newest chat, and under them the templates an
// agent starts from in one click (item 315); an agent's page (#/agents/<id>) greets with its face and role, takes a
// message for it and lists its chats; the editor makes or changes an agent. An agent goes out as a file from its page
// and comes back, in this home or another, from the list's Import.

import { actorFace, dotFace, fallbackLook, rememberLook } from "../actors.js";
import { api } from "../api.js";
import { askForWords, readyToSend } from "../attachments.js";
import { composerFrame } from "../composer.js";
import { ago, clip, el, modelName } from "../dom.js";
import { allModels, chosenLevel, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { touchFirst } from "../layout.js";
import { lookPicker } from "../lookpicker.js";
import { emptyPanel, errorPanel, field, loadingPanel, pageHead, statusBadge } from "../panels.js";
import { APPROVAL_OPTIONS } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { scoreLine } from "./memory.js";
import { skillRow } from "./skills.js";
import { messageRow } from "./team.js";
import { getSnapshot, loadModels, onLifecycle, onStreamBack } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const DOT_ID = "dot";
const GOAL_CHARS = 90;
const MAX_GOAL_PX = 320;
// The face an agent wears in its row: working while its newest run works, at rest otherwise.
const WORKING = new Set(["queued", "running", "blocked"]);
// The largest agent file Import reads: its instructions take at most 50,000 characters, of up to four bytes each.
const MOST_AGENT_FILE_BYTES = 256 * 1024;

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

// ------------------------------------------------------------------------------------------------- templates and files

function templateFact(label, text) {
  return el("div", null, el("dt", null, label), el("dd", null, text));
}

/** A template's card (item 315): its face, name and role, what it needs of the Mac and the schedule it suggests. */
function templateCard(template, onUse) {
  const use = el("button", { class: "btn sm", type: "button", "aria-label": `Use the template ${template.name}` }, icon("plus"), "Use template");
  use.addEventListener("click", async () => {
    use.disabled = true;
    try {
      await attempt(() => onUse(template));
    } finally {
      use.disabled = false;
    }
  });
  return el(
    "article",
    { class: "card template-card", dataset: { template: template.id } },
    el(
      "div",
      { class: "template-head" },
      actorFace(`template-${template.id}`, "idle", template.look),
      el("div", { class: "row-main" }, el("div", { class: "template-name" }, template.name), el("div", { class: "template-role" }, template.role)),
    ),
    el(
      "dl",
      { class: "template-facts" },
      templateFact("Needs", template.needs),
      templateFact("Schedule", template.schedule === "" ? "When you ask" : template.schedule),
    ),
    use,
  );
}

/** The name of an agent's file: its name in lower case with dashes, or its id when the name has no letter or digit. */
function agentFileName(record) {
  const slug = record.name
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug === "" ? record.id : slug}.dot-agent.json`;
}

/** The agent as a file to keep or to import in another home (item 315); the daemon refuses one that holds a key. */
async function exportAgent(record) {
  const file = await api.get(`/agents/${record.id}/export`);
  const name = agentFileName(record);
  const url = URL.createObjectURL(new Blob([`${JSON.stringify(file, null, 2)}\n`], { type: "application/json" }));
  const link = el("a", { href: url, download: name, hidden: true });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("ok", `${record.name} is saved as ${name}.`);
}

/** An agent made from its file (item 315); the daemon refuses a file that is not one, or that names tools not here. */
async function importAgent(file) {
  if (file.size > MOST_AGENT_FILE_BYTES) {
    throw new Error(`${file.name} is larger than an agent file can be.`);
  }
  const text = await file.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`${file.name} is not an agent file: ${error.message}`);
  }
  const saved = await api.post("/agents/import", parsed);
  rememberLook(saved.id, saved.look);
  toast("ok", `${saved.name} is imported.`);
  return saved;
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
        el("input", { type: "checkbox", value: name, checked: picked.has(name) }),
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
  const modelsListed = loadModels()
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
  // Saved only once the tools are listed: without them the form cannot say which tools the agent may use.
  let toolsListed = false;
  const toolsLoaded = api
    .get("/tools")
    .then((names) => {
      tools.replaceChildren(toolPicks(names, record === null ? [] : record.tools));
      toolsListed = true;
    })
    .catch((error) => {
      tools.replaceChildren(el("span", { class: "muted" }, `The tools could not be listed: ${error.message}`));
    });
  if (record !== null) {
    name.value = record.name;
    role.value = record.role;
    instructions.value = record.instructions;
  }
  const save = el("button", { class: "btn primary", type: "button", disabled: true }, icon(record === null ? "plus" : "check"), record === null ? "Create agent" : "Save changes");
  Promise.allSettled([modelsListed, toolsLoaded]).then(() => {
    save.disabled = !toolsListed;
  });
  const cancel = el("button", { class: "btn ghost", type: "button", onclick: onCancel }, "Cancel");

  async function submit() {
    const nameText = name.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    const picked = [...tools.querySelectorAll("input:checked")].map((input) => input.value);
    const body = {
      name: nameText,
      role: role.value.trim(),
      instructions: instructions.value.trim(),
      model: model.value,
      effort: chosenLevel(models, model.value, effort.value, record === null ? "" : record.model, record === null ? null : record.effort),
      approvals: approvals.value === "" ? null : approvals.value,
      // None picked is every tool, said as null: an empty list keeps the tools an agent has (the code review's Y5).
      tools: picked.length === 0 ? null : picked,
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
  const templates = el("section", { class: "stack", "aria-label": "Templates" }, loadingPanel());

  async function loadTemplates() {
    try {
      const listed = await api.get("/agent-templates");
      if (!disposed) {
        templates.replaceChildren(
          el("div", { class: "section-label" }, "Start from a template"),
          el("p", { class: "hint" }, "Each becomes an agent of yours, to change as you like. What one writes to a person is a draft that you send."),
          el("div", { class: "template-grid" }, listed.map((template) => templateCard(template, useTemplate))),
        );
      }
    } catch (error) {
      if (!disposed) {
        templates.replaceChildren(errorPanel(error, () => attempt(loadTemplates)));
      }
    }
  }

  async function useTemplate(template) {
    const saved = await api.post(`/agent-templates/${template.id}/use`, null);
    rememberLook(saved.id, saved.look);
    toast("ok", `${saved.name} is ready.`);
    ctx.navigate(`/agents/${saved.id}`);
  }

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
  const chooser = el("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Agent file" });
  const importButton = el(
    "button",
    { class: "btn ghost", type: "button", title: "Make an agent from a file exported from Dot", onclick: () => chooser.click() },
    icon("upload"),
    el("span", { class: "label" }, "Import"),
  );
  chooser.addEventListener("change", () => {
    const [file] = chooser.files;
    chooser.value = "";
    if (file !== undefined) {
      attempt(async () => {
        const saved = await importAgent(file);
        ctx.navigate(`/agents/${saved.id}`);
      });
    }
  });
  root.append(
    pageHead("Agents", "Dot, and the agents you or Dot made: each with a face, a role and its own chats.", el("div", { class: "cluster" }, teamLink, importButton, chooser, newButton)),
    editorSlot,
    holder,
    templates,
  );
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
    }
  });
  const stopBack = onStreamBack(() => attempt(reload));
  attempt(reload);
  attempt(loadTemplates);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
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
  const frame = composerFrame({
    label: `Message ${record.name}`,
    boxLabel: "Message",
    placeholder: `Message ${record.name}`,
    maxPx: MAX_GOAL_PX,
    commands: [],
    send: submit,
    sendLabel: "Start",
    draftKey: null,
    listener: null,
  });
  const { textarea: goal, attachments } = frame;
  let starting = false;
  const keys = messageKeys();

  async function submit() {
    const text = goal.value.trim();
    if (text === "" && !starting) {
      askForWords(attachments);
    }
    if (starting || text === "") {
      goal.focus();
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    starting = true;
    frame.sendButton.disabled = true;
    try {
      await attempt(async () => {
        const body = {
          goal: text,
          mode: "task",
          model: null,
          effort: null,
          approvals: null,
          goal_mode: null,
          standing: false,
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: record.id,
        };
        const created = await api.post("/runs", { ...body, key: keys.of("run", body) });
        keys.sent();
        frame.clear();
        ctx.navigate(`/runs/${created.id}`);
      });
    } finally {
      starting = false;
      frame.sendButton.disabled = false;
    }
  }

  return { element: frame.element, focus: () => goal.focus(), dispose: frame.dispose };
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
      el("button", { class: "btn ghost sm", type: "button", title: "Save the agent as a file, to import in another home", onclick: () => attempt(() => exportAgent(record)) }, icon("download"), el("span", { class: "label" }, "Export")),
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
      await refresh();
    }
  }

  // The agent as the daemon has it now; null when the page went or a later load overtook this one. A failure takes
  // the place of the page, with a way to try again.
  async function load() {
    const mine = ++ticket;
    try {
      const detail = await api.get(`/agents/${agentId}`);
      return disposed || mine !== ticket ? null : detail;
    } catch (error) {
      if (!disposed && mine === ticket) {
        page.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
      return null;
    }
  }

  // The lists under the top: chats, lessons and skills, messages. The top stays where it is, so the focus and what is
  // typed in it stay too.
  function paintLists(detail) {
    learned.replaceChildren(
      ...learnedSection(detail.record, detail.learned, {
        forget: (record) => attempt(() => forget(record)),
        skillsChanged: (list) => {
          if (list !== null) {
            attempt(refresh);
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
    if (top.parentNode !== page) {
      page.replaceChildren(top, runs, learned, messages);
    }
  }

  // The whole page: the top built again (the composer, after a save) and the lists.
  async function reload() {
    const detail = await load();
    if (detail !== null) {
      greet(detail.record);
      paintLists(detail);
      // On a touch screen the caret waits for a tap, as on the home (item 293).
      if (!touchFirst()) {
        composer.focus();
      }
    }
  }

  // Only the lists, when a run starts or ends or a lesson goes: a message being typed or an open form stays (item 116).
  async function refresh() {
    const detail = await load();
    if (detail !== null) {
      paintLists(detail);
    }
  }

  root.append(el("div", { class: "agent-page" }, el("a", { class: "back", href: "#/agents" }, icon("arrowLeft"), "All agents"), page));
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(refresh);
    }
  });
  const stopBack = onStreamBack(() => attempt(refresh));
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
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
