// Agents (item 86): the profiles the owner makes, each with a face in its own hue, a name, a role in one line,
// instructions that open the prompt of its runs, and bounds for them (a model, a reasoning level, approvals, tools).
// The list (#/agents) shows Dot first and then the agents, each with its newest chat, and under them the teams (item
// 504) and the templates (item 315) agents start from in one click; an agent's page (#/agents/<id>) greets with its
// face and role, takes a message for it and lists its chats; the editor makes or changes an agent. An agent goes out as
// a file from its page and comes back, in this home or another, from the list's Import.

import { actorFace, dotFace, fallbackLook, liveFace, rememberLook } from "../actors.js";
import { api } from "../api.js";
import { messageText, readyToSend } from "../attachments.js";
import { MODES, runChoices } from "../choices.js";
import { composerFrame } from "../composer.js";
import { ago, clip, el, modelName, plural, switchRow } from "../dom.js";
import { allModels, chosenLevel, KEPT_SPEED, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { touchFirst } from "../layout.js";
import { readFolds, writePref } from "../prefs.js";
import { lookPicker } from "../lookpicker.js";
import { errorPanel, field, loadingPanel, pageHead, statusBadge } from "../panels.js";
import { APPROVAL_OPTIONS, heldChoice, KEPT_GOAL_MODE } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { scoreLine } from "./memory.js";
import { skillRow } from "./skills.js";
import { messageRow } from "./team.js";
import { getSnapshot, loadModels, onChange, onLifecycle, onStreamBack } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const DOT_ID = "dot";
const GOAL_CHARS = 90;
const MAX_GOAL_PX = 320;
// The face an agent wears in its row: working while its newest run works, at rest otherwise.
const WORKING = new Set(["queued", "running", "blocked"]);
// The largest agent file Import reads: its instructions take at most 50,000 characters, of up to four bytes each.
const MOST_AGENT_FILE_BYTES = 256 * 1024;
// Names in one line of words, as a toast says them: "A", "A and B", "A, B, and C".
const AND_LIST = new Intl.ListFormat("en", { style: "long", type: "conjunction" });

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
    // A paused agent (item 465) says so in the list, as on its page.
    record.paused_at === null ? null : el("span", { class: "badge b-warn", title: "Its schedules start nothing by themselves until you resume it" }, "Paused"),
    latest === null ? null : el("span", { class: "agent-when" }, ago(latest.created_at)),
  );
}

// ------------------------------------------------------------------------------------------------- templates and files

/** A line of a template's card (item 509): an icon, and the fact in words, its name kept for a reader of the screen. */
function templateFact(name, glyph, text) {
  return el("div", { class: "template-fact", title: name }, icon(glyph), el("span", { class: "visually-hidden" }, `${name}: `), el("span", null, text));
}

/** A template's tools (item 425): how many it may use, and the names themselves a click away, so that the owner sees
    what a click gives before taking it — the card said what a template needs of the Mac, not what it may touch. A tool
    this home does not have is marked, with the reason, since such a template is refused on use, not quietly cut. */
function templateTools(template, known) {
  return el(
    "details",
    { class: "template-tools" },
    el("summary", null, `Tools (${template.tools.length})`),
    el(
      "div",
      { class: "template-tool-chips" },
      template.tools.map((name) =>
        el(
          "code",
          known !== null && !known.has(name)
            ? { class: "chip missing", title: "This home has no such tool: its group is off in the configuration, or its app is not here" }
            : { class: "chip" },
          name,
        ),
      ),
    ),
  );
}

/** A template's card (item 315): its face, name and role, what it needs of the Mac, the schedule it suggests and the
    tools its agent may use. One that suggests a schedule offers to add it with the agent (item 464), switched off until
    the owner has read its goal; `onUse(template, alsoSchedule)` makes them. */
function templateCard(template, onUse, known) {
  // The app's own switch, which a finger finds on a phone (item 488), as every other on and off of the pages.
  const alsoWords = "Also add this schedule, switched off until you read its goal";
  const also = template.cron === "" ? null : el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": alsoWords });
  also?.addEventListener("click", () => also.setAttribute("aria-checked", String(also.getAttribute("aria-checked") !== "true")));
  const alsoRow = also === null ? null : switchRow(el("div", { class: "template-also" }, also, el("span", null, "Add its schedule too, switched off")), also);
  const use = el(
    "button",
    { class: "btn sm template-use", type: "button", "aria-label": `Use the template ${template.name}` },
    icon("plus"),
    el("span", { class: "use-long" }, "Use template"),
    el("span", { class: "use-short" }, "Use"),
  );
  use.addEventListener("click", async () => {
    use.disabled = true;
    try {
      await attempt(() => onUse(template, also !== null && also.getAttribute("aria-checked") === "true"));
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
      "div",
      { class: "template-facts" },
      templateFact("Needs", "plug", template.needs),
      templateFact("Schedule", "clock", template.schedule === "" ? "When you ask" : template.schedule),
    ),
    alsoRow,
    templateTools(template, known),
    use,
  );
}

/** A team's card (item 504): its name and what it does, the faces and names of its agents, and Use team, which makes
    the ones the owner lacks. A team one of whose agents suggests a schedule offers to add the schedules of the agents
    it makes, switched off as a template's (item 464); `onUse(team, members, alsoSchedules)` makes them. */
function teamCard(team, templates, onUse) {
  const members = team.members.map((id) => templates.get(id));
  const alsoWords = "Also add their schedules, switched off until you read their goals";
  const also = members.some((template) => template.cron !== "")
    ? el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": alsoWords })
    : null;
  also?.addEventListener("click", () => also.setAttribute("aria-checked", String(also.getAttribute("aria-checked") !== "true")));
  const alsoRow = also === null ? null : switchRow(el("div", { class: "template-also" }, also, el("span", null, "Add their schedules too, switched off")), also);
  const use = el(
    "button",
    { class: "btn sm template-use", type: "button", "aria-label": `Use the team ${team.name}` },
    icon("plus"),
    el("span", { class: "use-long" }, "Use team"),
    el("span", { class: "use-short" }, "Use"),
  );
  use.addEventListener("click", async () => {
    use.disabled = true;
    try {
      await attempt(() => onUse(team, members, also !== null && also.getAttribute("aria-checked") === "true"));
    } finally {
      use.disabled = false;
    }
  });
  return el(
    "article",
    { class: "card team-card", dataset: { team: team.id } },
    el(
      "div",
      { class: "template-head" },
      el("div", { class: "row-main" }, el("div", { class: "template-name" }, team.name), el("div", { class: "template-role" }, team.purpose)),
    ),
    el(
      "ul",
      { class: "team-members", "aria-label": `The agents of the ${team.name}` },
      members.map((template) => el("li", null, actorFace(`template-${template.id}`, "idle", template.look), el("span", null, template.name))),
    ),
    alsoRow,
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

// Which of the ready-made agents the Agents page shows (item 509), the teams or the single agents: the one chosen last,
// while the app is open.
let shownPane = "teams";

function mountList(root, ctx) {
  let disposed = false;
  let ticket = 0;
  const holder = el("div", null, loadingPanel());
  const editorSlot = el("div", null);
  // The page in two parts (item 509): the agents the owner has, then the ready-made ones to add, a team or one agent
  // at a time, one of the two shown.
  const mineCount = el("span", { class: "agents-count" });
  const yours = el(
    "section",
    { class: "agents-part", "aria-label": "Your agents" },
    el("div", { class: "agents-part-head" }, el("h2", { class: "section-label" }, "Your agents", mineCount)),
    el("p", { class: "hint" }, "Open one to give it work, read its chats or change it. Ask Dot for a big job and it hands parts to them."),
    holder,
  );
  const templates = el("section", { class: "agents-part", "aria-label": "Templates" }, loadingPanel());

  async function loadTemplates() {
    try {
      // The tools this home has, so that a card marks one it lacks. A home that cannot list them still shows the
      // templates, their tools unmarked, and says above them that it could not tell which are missing.
      const [listed, teams, named] = await Promise.all([
        api.get("/agent-templates"),
        api.get("/agent-teams"),
        api.get("/tools").then(
          (names) => ({ names, problem: null }),
          (error) => ({ names: null, problem: error }),
        ),
      ]);
      const known = named.names === null ? null : new Set(named.names);
      const listingProblem = named.problem;
      const byId = new Map(listed.map((template) => [template.id, template]));
      if (!disposed) {
        // `replaceChildren` draws a null as the word "null": the cards' note is a child only when there is one.
        const panes = {
          teams: el(
            "div",
            { class: "stack tight", dataset: { pane: "teams" } },
            el("p", { class: "hint" }, "A few agents that work together, made in one click. An agent of that name you have already is kept as it is."),
            el("div", { class: "template-grid" }, teams.map((team) => teamCard(team, byId, useTeam))),
          ),
          single: el(
            "div",
            { class: "stack tight", dataset: { pane: "single" } },
            el("p", { class: "hint" }, "One agent of yours, to change as you like. What one writes to a person is a draft that you send."),
            ...(listingProblem === null
              ? []
              : [
                  el(
                    "p",
                    { class: "hint", dataset: { templatesToolsUnknown: "" } },
                    `The tools could not be listed: ${listingProblem.message}. A template that names one this home lacks is refused when used.`,
                  ),
                ]),
            el("div", { class: "template-grid" }, listed.map((template) => templateCard(template, useTemplate, known))),
          ),
        };
        const choices = [
          { id: "teams", label: `Teams (${teams.length})` },
          { id: "single", label: `Single agents (${listed.length})` },
        ];
        const buttons = choices.map((choice) => el("button", { type: "button", dataset: { pane: choice.id }, onclick: () => show(choice.id) }, choice.label));
        const show = (id) => {
          shownPane = id;
          for (const button of buttons) {
            button.setAttribute("aria-pressed", String(button.dataset.pane === id));
          }
          for (const [key, pane] of Object.entries(panes)) {
            pane.hidden = key !== id;
          }
        };
        show(shownPane);
        templates.replaceChildren(
          el(
            "div",
            { class: "agents-part-head" },
            el("h2", { class: "section-label" }, "Add ready-made agents"),
            el("div", { class: "segmented", role: "group", "aria-label": "Ready-made agents" }, buttons),
          ),
          panes.teams,
          panes.single,
        );
      }
    } catch (error) {
      if (!disposed) {
        templates.replaceChildren(errorPanel(error, () => attempt(loadTemplates)));
      }
    }
  }

  // The agent, then its schedule switched off when it was asked for (item 464): the agent's page lists it with its goal
  // to read and its switch. A schedule that could not be added leaves the agent made, and the toast says why.
  async function useTemplate(template, alsoSchedule) {
    const saved = await api.post(`/agent-templates/${template.id}/use`, null);
    rememberLook(saved.id, saved.look);
    if (!alsoSchedule) {
      toast("ok", `${saved.name} is ready.`);
    } else {
      try {
        await api.post("/schedules", { name: saved.name, goal: template.task, cron: template.cron, agent_id: saved.id, enabled: false });
        toast("ok", `${saved.name} is ready, with its schedule switched off: read its goal, then switch it on.`);
      } catch (error) {
        toast("error", `${saved.name} is ready, but its schedule was not added: ${error.message}`);
      }
    }
    ctx.navigate(`/agents/${saved.id}`);
  }

  // The team's agents the owner lacks (item 504), then the schedules of those it made when they were asked for,
  // switched off as a template's (item 464). A schedule that could not be added leaves its agent made, and the toast
  // says which and why. The list then shows the agents.
  async function useTeam(team, members, alsoSchedules) {
    const used = await api.post(`/agent-teams/${team.id}/use`, null);
    for (const record of [...used.made, ...used.kept]) {
      rememberLook(record.id, record.look);
    }
    const scheduled = alsoSchedules ? used.made.filter((record) => members.find((member) => member.name === record.name).cron !== "") : [];
    const missed = [];
    for (const record of scheduled) {
      const template = members.find((member) => member.name === record.name);
      try {
        await api.post("/schedules", { name: record.name, goal: template.task, cron: template.cron, agent_id: record.id, enabled: false });
      } catch (error) {
        // The toast ends the list with its own full stop, so a message's own is taken off.
        missed.push(`${record.name}: ${error.message.replace(/\.$/, "")}`);
      }
    }
    const made = AND_LIST.format(used.made.map((record) => record.name));
    const had = used.kept.length === 0 ? "" : ` Already yours and kept as they were: ${AND_LIST.format(used.kept.map((record) => record.name))}.`;
    if (used.made.length === 0) {
      toast("ok", `You have every agent of the ${team.name} already.`);
    } else if (missed.length > 0) {
      toast("error", `${team.name}: ${made} ${used.made.length === 1 ? "is" : "are"} ready, but not every schedule was added: ${missed.join("; ")}.${had}`);
    } else {
      const off = scheduled.length === 0 ? "" : " Their schedules are switched off: read their goals, then switch them on.";
      toast("ok", `${team.name}: ${made} ${used.made.length === 1 ? "is" : "are"} ready.${had}${off}`);
    }
    await reload();
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
      mineCount.textContent = String(summaries.length);
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
    pageHead("Agents", "Dot, and the agents you or Dot made, each with a role and its own chats.", el("div", { class: "cluster" }, teamLink, importButton, chooser, newButton)),
    editorSlot,
    yours,
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

/**
 * Where the composer of `record`'s page keeps its choices. It starts from the agent's own bounds, its approvals and its
 * reasoning level (on every model that offers it), and holds what is chosen there for that page alone: this browser's
 * choices for the home's box would otherwise stand in the agent's place, a mode chosen there for another chat loosening
 * the agent's, and a choice made here would change the home's. Fast mode and goal mode are the home's, which the agent
 * has no bounds for.
 */
function agentKept(record) {
  const levels = new Map();
  return {
    level: {
      read: (info) => {
        if (levels.has(info.ref)) {
          return levels.get(info.ref);
        }
        return record.effort !== null && info.efforts.includes(record.effort) ? record.effort : null;
      },
      write: (info, level) => {
        if (level === null) {
          levels.delete(info.ref);
        } else {
          levels.set(info.ref, level);
        }
      },
    },
    speed: KEPT_SPEED,
    approvals: heldChoice(record.approvals ?? null),
    goalMode: KEPT_GOAL_MODE,
    mode: "task",
    model: record.model ?? "",
    once: null,
  };
}

/** The composer of an agent's page: a message for the agent, sent with Enter, with the new chat's mode, model and
 * reasoning and Options pills; the model starts at the agent's own. */
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
  const choices = runChoices({
    frame,
    modes: MODES,
    kept: agentKept(record),
    optionsTitle: "Approvals",
    more: () => [],
    onMode: (id) => {
      choices.goalMode.setAvailable(id !== "chat");
      goal.focus();
    },
    onChange: () => {},
  });
  const { pills } = choices;
  frame.options.append(pills.mode, pills.model, pills.reasoning, pills.options);
  const stopStatus = onChange(choices.sync);
  let starting = false;
  const keys = messageKeys();

  async function submit() {
    // A message of files alone goes too (item 445): a pasted text or a picture sent without a word typed.
    const text = messageText(goal.value, attachments);
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
        // A kept reasoning level is checked against the model's own, so the models are known first.
        await choices.ready();
        const chosen = choices.value();
        const body = {
          goal: text,
          mode: chosen.mode,
          model: chosen.model,
          effort: chosen.effort,
          speed: chosen.speed,
          once: chosen.once,
          approvals: chosen.approvals,
          goal_mode: chosen.goal_mode,
          standing: false,
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: record.id,
          ...frame.picks.body(),
        };
        const created = await api.post("/runs", { ...body, key: keys.of("run", body) });
        keys.sent();
        choices.sent();
        frame.picks.sent();
        frame.clear();
        ctx.navigate(`/runs/${created.id}`);
      });
    } finally {
      starting = false;
      frame.sendButton.disabled = false;
    }
  }

  return {
    element: frame.element,
    focus: () => goal.focus(),
    dispose: () => {
      stopStatus();
      choices.dispose();
      frame.dispose();
    },
  };
}

/** The agent's greeting: its face, name and role, like Dot's on the home page. */
// The face on the agent's page, as big and as alive as Dot's on the home.
const FACE_PX = 96;
// Which sections of an agent's page are folded, kept per browser as the home keeps its own.
const FOLDS_KEY = "dot.agentFolds";

function who(record, face) {
  return el(
    "div",
    { class: "home-who agent-who" },
    face,
    el("div", { class: "home-name" }, record.name),
    record.role === "" ? null : el("p", { class: "home-greeting" }, record.role),
    record.made_by === null ? null : el("p", { class: "home-greeting agent-made" }, "Made by Dot in one of its runs"),
    record.paused_at === null
      ? null
      : el(
          "p",
          { class: "home-greeting agent-paused" },
          el("span", { class: "badge b-warn" }, "Paused"),
          " Its schedules start nothing by themselves; your messages here still go through.",
        ),
  );
}

/** A schedule the agent's runs run as (item 464): its switch, its name, its goal in full (a template's is added
 * switched off, to be read before it runs), when it runs, and Edit, on the Schedules page. */
function agentScheduleRow(view, onToggle) {
  const { record } = view;
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? "Switch off" : "Switch on"} ${record.name}`,
    title: record.enabled ? "Switch this schedule off" : "Switch this schedule on",
    onclick: () => onToggle(record),
  });
  const state = !record.enabled
    ? el("span", { class: "badge b-muted" }, "switched off")
    : el("span", null, view.next_local === null ? "No further runs" : `Next ${view.next_local}`);
  return el(
    "div",
    { class: "list-row", dataset: { schedule: record.id } },
    toggle,
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, record.name),
      el("div", { class: "agent-schedule-goal" }, record.goal),
      el("div", { class: "row-meta" }, el("span", { class: "mono", title: "When it runs" }, view.when), state),
    ),
    el("div", { class: "row-actions" }, el("a", { class: "btn ghost sm", href: "#/schedules", title: "Edit it on the Schedules page" }, "Edit")),
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

/** What the agent learned for itself (item 94): its lessons, then its skills, as rows; none when it learned nothing. */
function learnedRows(record, learned, actions) {
  return [
    ...learned.lessons.map((lesson) => lessonRow(lesson, learned.scores[lesson.id], actions.forget)),
    ...learned.skills.map((skill) => skillRow({ ...skill, agentName: record.name }, actions.skillsChanged)),
  ];
}

/**
 * A section of the agent's page as the home draws its Today strip: a title with its count that folds it, and its rows;
 * null for one without rows, so that an empty section takes no room. `folds` and `toggle(key)` keep what is folded.
 */
function homeSection(key, title, rows, folds, toggle) {
  if (rows.length === 0) {
    return null;
  }
  const folded = folds[key] === true;
  return el(
    "div",
    { class: "home-today-section", "data-section": key },
    el(
      "h2",
      { class: "section-label" },
      el(
        "button",
        { class: "home-today-title", type: "button", "aria-expanded": folded ? "false" : "true", onclick: () => toggle(key) },
        icon("chevron"),
        `${title} `,
        el("span", { class: "home-today-count" }, String(rows.length)),
      ),
    ),
    folded ? null : el("div", { class: "home-today-list card" }, ...rows),
  );
}

function mountPage(root, ctx, agentId) {
  let disposed = false;
  let ticket = 0;
  let composer = null;
  let form = null;
  let face = null;
  let shownDetail = null;
  let folds = readFolds(FOLDS_KEY);
  // The agent's page is laid out as the home is (item 124): its face, name and role over the composer in the middle,
  // and under them its chats, what it learned and its messages as the home's Today strip, each only when it has rows.
  const top = el("div", { class: "home-start agent-top" });
  const sections = el("section", { class: "home-today agent-sections", "aria-label": "The agent's work" });
  const page = el("div", { class: "home agent-home" }, loadingPanel());

  // The bar names the agent, as a heading of the page, and so does the tab (`Researcher · Agents · Dot`).
  function stageOf(record) {
    ctx.setTitle(`${record.name} · Agents`);
    setStageTitle(actorFace(record.id, "idle", record.look), el("h1", { class: "stage-h1 stage-name" }, record.name));
    setStageTools(
      el("button", { class: "btn ghost sm", type: "button", title: "Edit the agent", onclick: () => edit(record) }, icon("pencil"), el("span", { class: "label" }, "Edit")),
      el("button", { class: "btn ghost sm", type: "button", title: "Save the agent as a file, to import in another home", onclick: () => attempt(() => exportAgent(record)) }, icon("download"), el("span", { class: "label" }, "Export")),
      record.paused_at === null
        ? el(
            "button",
            { class: "btn ghost sm", type: "button", title: "Pause the agent: its schedules start nothing by themselves until you resume it", onclick: () => attempt(() => setPaused(record, true)) },
            icon("pause"),
            el("span", { class: "label" }, "Pause"),
          )
        : el(
            "button",
            { class: "btn ghost sm", type: "button", title: "Resume the agent: its schedules fire again", onclick: () => attempt(() => setPaused(record, false)) },
            icon("play"),
            el("span", { class: "label" }, "Resume"),
          ),
      el("button", { class: "btn ghost sm", type: "button", title: "Delete the agent", onclick: () => attempt(() => remove(record)) }, icon("trash"), el("span", { class: "label" }, "Delete")),
    );
  }

  /** Pause the agent or resume it (item 465), then show the page as the daemon has it. */
  async function setPaused(record, paused) {
    await api.post(`/agents/${record.id}/${paused ? "pause" : "resume"}`, null);
    toast("ok", paused ? `${record.name} paused: its schedules start nothing by themselves.` : `${record.name} resumed.`);
    await reload();
  }

  function closeForm() {
    delete page.dataset.editing;
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
    face?.dispose();
    face = liveFace(record.id, "idle", record.look, FACE_PX);
    top.replaceChildren(who(record, face.element), composer.element);
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
    // The editor opens at the top of the page, not in the middle where the greeting stood (agent-home, app.css).
    page.dataset.editing = "";
    form.focus();
  }

  async function remove(record) {
    // Its schedules are switched off with it (item 462), and the dialog says how many: the ones on now.
    const schedules = (await api.get("/schedules")).filter((view) => view.record.options.agent_id === record.id && view.record.enabled);
    const switchedOff =
      schedules.length === 0 ? "" : ` Its ${plural(schedules.length, "schedule is", "schedules are")} switched off.`;
    const confirmed = await confirmDialog({
      title: `Delete ${record.name}?`,
      message: `Its chats stay, as Dot's. The agent's face, role and instructions are gone.${switchedOff}`,
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

  /** Switch one of the agent's schedules on or off, then show the lists as the daemon has them. */
  async function switchSchedule(record) {
    await api.post(`/schedules/${record.id}/${record.enabled ? "disable" : "enable"}`, null);
    toast("ok", record.enabled ? `${record.name} is switched off.` : `${record.name} is switched on.`);
    await refresh();
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

  // The agent as the daemon has it now, with the schedules its runs run as (item 464); null when the page went or a
  // later load overtook this one. A failure takes the place of the page, with a way to try again.
  async function load() {
    const mine = ++ticket;
    try {
      const [detail, schedules] = await Promise.all([api.get(`/agents/${agentId}`), api.get("/schedules")]);
      const own = schedules.filter((view) => view.record.options.agent_id === agentId);
      return disposed || mine !== ticket ? null : { ...detail, schedules: own };
    } catch (error) {
      if (!disposed && mine === ticket) {
        page.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
      return null;
    }
  }

  // The lists under the top: chats, lessons and skills, messages. The top stays where it is, so the focus and what is
  // typed in it stay too.
  function toggleFold(key) {
    folds = { ...folds, [key]: folds[key] !== true };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    if (shownDetail !== null) {
      paintLists(shownDetail);
    }
    sections.querySelector(`[data-section="${key}"] .home-today-title`)?.focus();
  }

  function paintLists(detail) {
    shownDetail = detail;
    const learned = learnedRows(detail.record, detail.learned, {
      forget: (record) => attempt(() => forget(record)),
      skillsChanged: (list) => {
        if (list !== null) {
          attempt(refresh);
        }
      },
    });
    const parts = [
      homeSection(
        "schedules",
        "Schedules",
        detail.schedules.map((view) => agentScheduleRow(view, (record) => attempt(() => switchSchedule(record)))),
        folds,
        toggleFold,
      ),
      homeSection("chats", "Chats", detail.runs.map(runRow), folds, toggleFold),
      homeSection("learned", "What it learned", learned, folds, toggleFold),
      homeSection("messages", "Messages", detail.messages.map(messageRow), folds, toggleFold),
    ];
    sections.replaceChildren(...parts.filter((part) => part !== null));
    sections.hidden = sections.childElementCount === 0;
    // The tests and the page's own styles find what it learned by this mark.
    sections.querySelector('[data-section="learned"]')?.classList.add("agent-learned");
    if (top.parentNode !== page) {
      page.replaceChildren(top, sections);
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
    face?.dispose();
    closeForm();
  };
}

export function mount(root, ctx) {
  const agentId = ctx.params[0];
  return agentId === undefined ? mountList(root, ctx) : mountPage(root, ctx, agentId);
}
