// Skills Dot and its agents learned from their own work: one row each with the kind of work, where it acted, the runs
// it comes from and how it fared; a retired one (read by two runs that then failed) is listed apart. An agent's skill
// names the agent and is offered to its runs alone until Share; Drop removes one.

import { api } from "../api.js";
import { ago, el, plural } from "../dom.js";
import { emptyPanel, errorPanel, loadingPanel, paneHead } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

/** Drop a learned skill after a confirmation; resolves to the list after it, or null when the owner kept it. */
export async function dropSkill(skill) {
  const confirmed = await confirmDialog({
    title: `Drop ${skill.name}?`,
    message: "The skill is removed; a later run of its kind may learn it again.",
    confirmLabel: "Drop",
    danger: true,
    focusCancel: true,
  });
  if (!confirmed) {
    return null;
  }
  const page = await api.del(`/skills/learned/${encodeURIComponent(skill.name)}`);
  toast("ok", `Dropped ${skill.name}.`);
  return page.items;
}

/** Offer an agent's skill to every run, or to the agent's alone again; resolves to the list after it. */
export async function shareSkill(skill, shared) {
  const page = await api.put(`/skills/learned/${encodeURIComponent(skill.name)}`, { shared });
  toast("ok", shared ? `${skill.name} is offered to every run.` : `${skill.name} is ${skill.agentName}'s alone again.`);
  return page.items;
}

/**
 * One learned skill as a row: the name and the description, then the kind, the place, the agent whose it is, the runs
 * and the record. `skill.agentName` names the agent when the skill is one's; `onChanged` gets the list after an action.
 */
export function skillRow(skill, onChanged) {
  const drop = el("button", { class: "btn ghost sm", type: "button" }, "Drop");
  drop.addEventListener("click", () => attempt(async () => onChanged(await dropSkill(skill))));
  const share =
    skill.agent_id === null
      ? null
      : el("button", { class: "btn ghost sm", type: "button" }, skill.shared ? "Keep to the agent" : "Share");
  if (share !== null) {
    share.addEventListener("click", () => attempt(async () => onChanged(await shareSkill(skill, !skill.shared))));
  }
  const record =
    skill.uses + skill.failures === 0
      ? "Not read by a run yet"
      : `${plural(skill.uses, "run", "runs")} done after reading it, ${skill.failures} failed`;
  const whose =
    skill.agent_id === null
      ? null
      : el("span", { class: "chip" }, skill.shared ? `${skill.agentName ?? "An agent"}, shared` : (skill.agentName ?? "An agent"));
  return el(
    "div",
    { class: "list-row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, skill.name),
      el("div", { class: "row-text" }, skill.description),
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: "chip" }, skill.kind),
        whose,
        el("span", null, skill.host),
        el("span", null, `Learned from ${plural(skill.runs.length, "run", "runs")}, last ${ago(skill.updated_at)}`),
        el("span", null, record),
      ),
    ),
    el("div", { class: "row-actions" }, share, drop),
  );
}

/** The skills with the name of the agent each belongs to, from the agents list. */
export function withAgentNames(items, agents) {
  const names = new Map(agents.map((row) => [row.record.id, row.record.name]));
  return items.map((skill) => ({ ...skill, agentName: skill.agent_id === null ? null : names.get(skill.agent_id) ?? null }));
}

export function mount(root) {
  let disposed = false;
  let items = null;
  let agents = [];
  const holder = el("div", null, loadingPanel());

  function paint() {
    if (items === null) {
      return;
    }
    if (items.length === 0) {
      holder.replaceChildren(
        emptyPanel("No learned skills yet", "After a task that went well, Dot writes how it did it as a skill for the next one of its kind."),
      );
      return;
    }
    const named = withAgentNames(items, agents);
    const active = named.filter((skill) => !skill.retired);
    const retired = named.filter((skill) => skill.retired);
    const parts = [];
    if (active.length > 0) {
      parts.push(el("div", { class: "card" }, active.map((skill) => skillRow(skill, changed))));
    }
    if (retired.length > 0) {
      parts.push(
        el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, "Retired")),
        el("div", { class: "card" }, retired.map((skill) => skillRow(skill, changed))),
      );
    }
    holder.replaceChildren(...parts);
  }

  function changed(list) {
    if (list !== null && !disposed) {
      items = list;
      paint();
    }
  }

  async function load() {
    try {
      const [page, rows] = await Promise.all([api.get("/skills/learned"), api.get("/agents")]);
      if (disposed) {
        return;
      }
      items = page.items;
      agents = rows;
      paint();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error));
      }
    }
  }

  root.replaceChildren(
    paneHead("What Dot and its agents learned from their own work, offered to later runs as advice.", []),
    holder,
  );
  load();
  return () => {
    disposed = true;
  };
}
