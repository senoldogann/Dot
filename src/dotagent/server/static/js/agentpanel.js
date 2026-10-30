// The Agent tab of a chat's side panel (item 846): who the chat is with and how it is set, as a messaging app's panel
// shows the contact, beside the conversation. For one of your agents: its face, name and role, its model, approvals and
// actions, its instructions, what it learned, and Edit, which opens the agent's editor in the panel (views/agents.js);
// for Dot: its model, its name and look, its rules and its memory, each a link to the settings that hold it. The agent's
// own page stays a link away.

import { actorFace } from "./actors.js";
import { api, query } from "./api.js";
import { clip, el, modelName, plural, setText } from "./dom.js";
import { dotName } from "./face.js";
import { icon } from "./icons.js";
import { APPROVAL_OPTIONS } from "./runmodes.js";
import { getSnapshot } from "./state.js";
import { attempt } from "./ui.js";

// How much of an agent's instructions the panel shows; the editor holds them whole.
const INSTRUCTION_CHARS = 280;

/** A row of the facts: `label` and `value` (a node or a text). */
function fact(label, value) {
  return el("div", null, el("dt", null, label), el("dd", null, value));
}

/** The model a run of `chosen` ("" for the configured one) takes, in words. */
function modelWords(chosen) {
  const status = getSnapshot().status;
  if (chosen !== "") {
    return modelName(chosen);
  }
  return status === null ? "Default" : `Default (${modelName(status.models.main)})`;
}

function approvalWords(approvals) {
  return approvals === null ? "Default" : (APPROVAL_OPTIONS.find((option) => option.id === approvals)?.label ?? approvals);
}

/** The head of the tab: the agent's face, its name and its role. */
function who(id, name, role, look) {
  return el(
    "div",
    { class: "agent-panel-who" },
    actorFace(id, "idle", look),
    el("div", { class: "agent-panel-names" }, el("div", { class: "agent-panel-name" }, name), role === "" ? null : el("div", { class: "agent-panel-role muted" }, role)),
  );
}

/** A link to the settings tab `tab`, named `label`. */
function settingsLink(tab, label) {
  return el("a", { href: `#/settings/${tab}` }, label);
}

/**
 * The tab's content for the chat's agent: `agent` is its record, null for Dot. Returns `element` and `dispose()`; the
 * tab reads what it shows when it is made and again after an edit.
 */
export function agentPanel({ agent }) {
  const element = el("div", { class: "agent-panel" });
  let record = agent;
  let form = null;
  let disposed = false;

  function closeForm() {
    form?.dispose();
    form = null;
  }

  /** Dot: its settings, each where it is set. */
  function paintDot(notes) {
    element.replaceChildren(
      who("dot", dotName(), "Your assistant", null),
      el(
        "dl",
        { class: "kv kv-rows agent-panel-facts" },
        fact("Model", settingsLink("models", modelWords(""))),
        fact("Name and look", settingsLink("general", "General")),
        fact("Rules", settingsLink("rules", "Rules and memory")),
        fact("Memory", settingsLink("memory", notes === null ? "Notes" : plural(notes, "note", "notes"))),
      ),
    );
  }

  /** One of your agents: its settings, what it learned, Edit and its page. */
  function paintAgent(learned) {
    const instructions = record.instructions.trim();
    const learnedWords = learned === null ? "" : [learned.lessons.length > 0 ? plural(learned.lessons.length, "lesson", "lessons") : null, learned.skills.length > 0 ? plural(learned.skills.length, "skill", "skills") : null].filter((part) => part !== null).join(", ");
    const edit = el("button", { class: "btn sm", type: "button", onclick: () => attempt(openForm) }, icon("pencil"), "Edit");
    element.replaceChildren(
      who(record.id, record.name, record.role, record.look),
      el(
        "dl",
        { class: "kv kv-rows agent-panel-facts" },
        fact("Model", modelWords(record.model)),
        fact("Approvals", approvalWords(record.approvals)),
        fact("Actions", record.tools.length === 0 ? "All" : plural(record.tools.length, "action", "actions")),
        record.paused_at === null ? null : fact("Schedules", "Paused"),
        learnedWords === "" ? null : fact("Learned", learnedWords),
      ),
      el(
        "section",
        { class: "agent-panel-instructions", "aria-label": "Instructions" },
        el("h4", { class: "agent-panel-label" }, "Instructions"),
        el("p", { class: instructions === "" ? "muted" : null }, instructions === "" ? "None: it works as Dot does." : clip(instructions, INSTRUCTION_CHARS)),
      ),
      el("div", { class: "cluster agent-panel-actions" }, edit, el("a", { class: "btn ghost sm", href: `#/agents/${record.id}` }, "Open its page")),
    );
  }

  /** The agent's editor in the tab's place; saved or cancelled, the tab shows the agent again. */
  async function openForm() {
    const { agentForm } = await import("./views/agents.js");
    if (disposed) {
      return;
    }
    closeForm();
    form = agentForm({
      record,
      onSaved: (saved) => {
        record = saved;
        closeForm();
        attempt(load);
      },
      onCancel: () => {
        closeForm();
        attempt(load);
      },
    });
    element.replaceChildren(form.element);
    form.focus();
  }

  /** Read what the tab shows; what cannot be read is left out. */
  async function load() {
    if (record === null) {
      paintDot(null);
      const notes = await api.get(`/memories${query({ limit: 1, offset: 0 })}`);
      if (!disposed && form === null) {
        paintDot(notes.total);
      }
      return;
    }
    paintAgent(null);
    const detail = await api.get(`/agents/${record.id}`);
    if (!disposed && form === null) {
      record = detail.record;
      paintAgent(detail.learned);
    }
  }

  attempt(load);
  return {
    element,
    dispose() {
      disposed = true;
      closeForm();
      setText(element, "");
    },
  };
}
