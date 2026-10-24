// The editor of a team (item 610): its name, what it is for, and which of the owner's agents are in it. A team is a record
// of agents the owner has; changing it never makes or removes an agent.

import { actorFace } from "./actors.js";
import { api } from "./api.js";
import { el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { field } from "./panels.js";
import { attempt, toast } from "./ui.js";

const NAME_CHARS = 60;
const PURPOSE_CHARS = 500;

/** The agents in the order the form lists them: those the team has, in the team's order, then the others as they come. */
function listed(team, agents) {
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  const members = team === null ? [] : team.agents.filter((id) => byId.has(id)).map((id) => byId.get(id));
  const inTeam = new Set(members.map((agent) => agent.id));
  return [...members, ...agents.filter((agent) => !inTeam.has(agent.id))];
}

/**
 * The form that makes a team (`team` null) or changes one, over `agents` (the records of the agents the owner has). The
 * agents the team has are listed first, in its order, and the order of the ticked ones is what is saved.
 * `onSaved(record)` runs with the saved team; `onCancel` closes the form. Returns `element`, `focus()`.
 */
export function teamForm({ team, agents, onSaved, onCancel }) {
  const name = el("input", { class: "input", type: "text", maxlength: String(NAME_CHARS), autocomplete: "off", placeholder: "Content team", "aria-label": "Name" });
  const purpose = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: String(PURPOSE_CHARS),
    placeholder: "What the team is for. Dot reads this at the start of every chat with the team, and hands each agent the part its role fits.",
    "aria-label": "Purpose",
  });
  if (team !== null) {
    name.value = team.name;
    purpose.value = team.purpose;
  }
  const inTeam = new Set(team === null ? [] : team.agents);
  const boxes = listed(team, agents).map((agent) => ({
    agent,
    box: el("input", { type: "checkbox", value: agent.id, checked: inTeam.has(agent.id) }),
  }));
  const count = el("span", { class: "muted team-picks-count" });
  const save = el("button", { class: "btn primary", type: "button" }, icon(team === null ? "plus" : "check"), team === null ? "Create team" : "Save changes");
  const cancel = el("button", { class: "btn ghost", type: "button", onclick: onCancel }, "Cancel");

  function picked() {
    return boxes.filter(({ box }) => box.checked).map(({ agent }) => agent.id);
  }
  function paint() {
    const chosen = picked().length;
    count.textContent = agents.length === 0 ? "" : `${chosen} of ${agents.length}`;
    save.disabled = chosen === 0;
  }
  for (const { box } of boxes) {
    box.addEventListener("change", paint);
  }
  paint();

  async function submit() {
    const nameText = name.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    const body = { name: nameText, purpose: purpose.value.trim(), agents: picked() };
    save.disabled = true;
    try {
      const saved = team === null ? await api.post("/teams", body) : await api.put(`/teams/${team.id}`, body);
      toast("ok", team === null ? `${saved.name} is ready: ${plural(saved.agents.length, "agent", "agents")}.` : "Team saved.");
      onSaved(saved);
    } finally {
      paint();
    }
  }
  save.addEventListener("click", () => attempt(submit));

  const agentList =
    agents.length === 0
      ? el("p", { class: "hint" }, "You have no agents yet. Make one with New agent, or start from a ready team, then put them together here.")
      : el(
          "div",
          { class: "team-picks", role: "group", "aria-label": "The agents of the team" },
          boxes.map(({ agent, box }) =>
            el(
              "label",
              { class: "team-pick", dataset: { agent: agent.id } },
              box,
              actorFace(agent.id, "idle", agent.look),
              el("span", { class: "team-pick-name" }, agent.name),
              agent.role === "" ? null : el("span", { class: "team-pick-role" }, agent.role),
            ),
          ),
        );
  const agentsField = el(
    "div",
    { class: "field wide" },
    el("span", { class: "field-label" }, "Agents ", count),
    agentList,
    el("span", { class: "hint" }, "Dot leads the team: it hands each of these agents the part its role fits and puts their answers together. They stay your agents, with their own chats, if the team goes."),
  );
  const element = el(
    "section",
    { class: "card agent-editor team-editor", "aria-label": team === null ? "New team" : "Edit team" },
    el("div", { class: "card-head" }, el("span", null, team === null ? "New team" : `Edit ${team.name}`)),
    el(
      "div",
      { class: "card-pad stack" },
      el("div", { class: "form-grid" }, field("Name", name, ""), Object.assign(field("Purpose", purpose, ""), { className: "field wide" }), agentsField),
      el("div", { class: "cluster end" }, cancel, save),
    ),
  );
  return { element, focus: () => name.focus() };
}
