// Skills Dot and its agents learned from their own work: one row each with the kind of work, where it acted, the runs
// it comes from and how it fared; a retired one (read by two runs that then failed) is listed apart. An agent's skill
// names the agent and is offered to its runs alone until Share; Drop removes one. Below them, what the Mac's other
// agents set up (item 164): their skills, folded by agent, and their MCP servers by name and what they reach, never
// their arguments or keys. Dot uses one only once the owner adds it here, after a confirmation (item 165): a skill is
// copied into Dot's skills folder and offered to runs at once, a server is started from Dot's next start; Remove
// takes it back. Each list keeps a size and scrolls inside past its first rows (item 218).

import { api } from "../api.js";
import { ago, el, plural } from "../dom.js";
import { emptyPanel, errorPanel, foldGroup, loadingPanel, paneHead } from "../panels.js";
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

/** What the owner is asked before a skill or an MCP server found elsewhere is added to Dot, or taken back. */
function foundDialog(kind, item, added) {
  if (kind === "skill") {
    return added
      ? {
          title: `Add ${item.name} to Dot?`,
          message: `Dot copies this skill of ${item.agent} into its own skills folder and offers it to every run. The copy stays as it is now: later changes in ${item.agent}'s folder are not copied.`,
          confirmLabel: "Add",
        }
      : {
          title: `Remove ${item.name} from Dot?`,
          message: `Dot deletes its copy and no longer offers the skill. The skill stays where ${item.agent} keeps it.`,
          confirmLabel: "Remove",
          focusCancel: true,
        };
  }
  return added
    ? {
        title: `Add the MCP server ${item.name} to Dot?`,
        message: `From its next start, Dot starts it as ${item.agent} does, with the command, arguments and environment written there, and offers its tools to runs as their mode allows.`,
        confirmLabel: "Add",
      }
    : {
        title: `Remove the MCP server ${item.name} from Dot?`,
        message: `Dot no longer starts it, from its next start. It stays set up in ${item.agent}.`,
        confirmLabel: "Remove",
        focusCancel: true,
      };
}

/** What the owner is asked before something added from an agent that no longer has it is taken back. */
function goneDialog(item) {
  return item.kind === "skill"
    ? {
        title: `Remove ${item.name} from Dot?`,
        message: `${item.agent} no longer has this skill. Dot deletes its copy and no longer offers it.`,
        confirmLabel: "Remove",
        focusCancel: true,
      }
    : {
        title: `Remove the MCP server ${item.name} from Dot?`,
        message: `${item.agent} no longer has this server, and Dot leaves it out when it starts. Dot forgets it.`,
        confirmLabel: "Remove",
        focusCancel: true,
      };
}

/**
 * Ask `dialog` of the owner and, on yes, make the change `body` says and tell `done`; resolves to what is found after
 * it, or null when the owner said no.
 */
async function askAndChange(dialog, body, done) {
  if (!(await confirmDialog(dialog))) {
    return null;
  }
  const after = await api.put("/skills/elsewhere", body);
  toast("ok", done);
  return after;
}

/**
 * Add a skill or an MCP server found elsewhere to Dot, or take it back, once the owner says yes; resolves to what is
 * found after it, or null when the owner said no.
 */
export function changeFound(kind, item, added) {
  const done = !added ? `Removed ${item.name}.` : kind === "skill" ? `Added ${item.name}; runs are offered it.` : `Added ${item.name}; Dot starts it the next time it starts.`;
  return askAndChange(foundDialog(kind, item, added), { kind, agent: item.agent, name: item.name, added }, done);
}

/** A line of why Dot cannot take a found item, under its name. */
function refusalLine(text) {
  return el("div", { class: "row-meta" }, el("span", { class: "hint" }, text));
}

/**
 * The state of a found item and what the owner can do with it: a chip of what Dot has, a line of why Dot cannot take
 * it (a server added once may have changed since), a button; each null when it has none.
 */
function foundState(kind, item, hasIt, onFound) {
  const change = (added) => attempt(async () => onFound(await changeFound(kind, item, added)));
  if (item.added) {
    const remove = el("button", { class: "btn ghost sm", type: "button" }, "Remove");
    remove.addEventListener("click", () => change(false));
    const refusal = item.refusal === null ? null : refusalLine(`Dot leaves it out when it starts: ${item.refusal}`);
    return { chip: el("span", { class: "chip" }, "Added"), refusal, action: remove };
  }
  if (item.used) {
    return { chip: el("span", { class: "chip" }, hasIt), refusal: null, action: null };
  }
  if (item.refusal !== null) {
    return { chip: null, refusal: refusalLine(`Can't add: ${item.refusal}`), action: null };
  }
  const add = el("button", { class: "btn sm", type: "button" }, "Add to Dot");
  add.addEventListener("click", () => change(true));
  return { chip: null, refusal: null, action: add };
}

/** A skill another agent of this Mac set up: its name and description, whether Dot has it, and Add or Remove. */
function foundSkillRow(skill, onFound) {
  const { chip, refusal, action } = foundState("skill", skill, "Dot reads it", onFound);
  return el(
    "div",
    { class: "list-row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, skill.name),
      el("div", { class: "row-text" }, skill.description),
      chip === null ? null : el("div", { class: "row-meta" }, chip),
      refusal,
    ),
    el("div", { class: "row-actions" }, action),
  );
}

/** An MCP server another agent set up: its name, the agent, the program it starts or the host it reaches, and Add. */
function foundServerRow(server, onFound) {
  const reach = server.transport === "stdio" ? `Starts ${server.reaches}` : `${server.transport.toUpperCase()} to ${server.reaches}`;
  const { chip, refusal, action } = foundState("server", server, "Dot has it", onFound);
  return el(
    "div",
    { class: "list-row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, server.name),
      el("div", { class: "row-meta" }, el("span", { class: "chip" }, server.agent), el("span", null, reach), chip),
      refusal,
    ),
    el("div", { class: "row-actions" }, action),
  );
}

/** Something the owner added that its agent no longer has: its name, the agent, what Dot does with it, and Remove. */
function goneRow(item, onFound) {
  const remove = el("button", { class: "btn ghost sm", type: "button" }, "Remove");
  const body = { kind: item.kind, agent: item.agent, name: item.name, added: false };
  remove.addEventListener("click", () => attempt(async () => onFound(await askAndChange(goneDialog(item), body, `Removed ${item.name}.`))));
  const what = item.kind === "skill" ? "Skill: Dot keeps its copy" : "MCP server: Dot leaves it out when it starts";
  return el(
    "div",
    { class: "list-row" },
    el("div", { class: "row-main" }, el("div", { class: "row-title" }, item.name), el("div", { class: "row-meta" }, el("span", { class: "chip" }, item.agent), el("span", null, what))),
    el("div", { class: "row-actions" }, remove),
  );
}

/** A folding group of the found section: `title`, how many it holds, and its rows. */
function foundGroup(id, title, count, extra, rows) {
  return foldGroup({
    id,
    head: el("span", { class: "row grow" }, el("strong", null, title), el("span", { class: "hint" }, count), extra),
    body: el("div", null, rows),
    openByDefault: false,
  });
}

function foundHead() {
  return el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, "Found on this Mac"));
}

/**
 * What the Mac's other agents set up (item 164): the skills of each agent, then the MCP servers, then what the owner
 * added that its agent no longer has, then what was left out. `onFound` gets what is found after the owner adds or
 * removes one (item 165).
 */
export function foundSection(found, onFound) {
  const agents = new Map();
  for (const skill of found.skills) {
    agents.set(skill.agent, [...(agents.get(skill.agent) ?? []), skill]);
  }
  const groups = [...agents].map(([agent, skills]) => {
    const read = skills.filter((skill) => skill.used).length;
    const extra = read > 0 ? el("span", { class: "chip" }, `Dot reads ${read}`) : null;
    // An element id holds no space ("Claude Code plugin x" becomes claude-code-plugin-x), or aria-controls breaks.
    const slug = agent.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return foundGroup(`found-skills-${slug}`, agent, plural(skills.length, "skill", "skills"), extra, skills.map((skill) => foundSkillRow(skill, onFound)));
  });
  if (found.servers.length > 0) {
    groups.push(foundGroup("found-servers", "MCP servers", plural(found.servers.length, "server", "servers"), null, found.servers.map((server) => foundServerRow(server, onFound))));
  }
  if (found.gone.length > 0) {
    groups.push(foundGroup("found-gone", "Added, no longer set up", plural(found.gone.length, "item", "items"), null, found.gone.map((item) => goneRow(item, onFound))));
  }
  if (found.problems.length > 0) {
    const rows = found.problems.map((problem) =>
      el("div", { class: "list-row" }, el("div", { class: "row-main" }, el("div", { class: "row-text" }, problem.path), el("div", { class: "row-meta" }, problem.reason))),
    );
    groups.push(foundGroup("found-problems", "Left out", plural(found.problems.length, "file", "files"), null, rows));
  }
  const head = foundHead();
  if (groups.length === 0) {
    return [head, emptyPanel("Nothing found", "No other agent of this Mac (Claude Code, Claude Desktop, Cursor, VS Code, Codex) has skills or MCP servers set up.")];
  }
  const hint = el(
    "div",
    { class: "hint" },
    "Skills and MCP servers that other agents of this Mac set up. Dot uses one only once you add it: a skill is copied and offered to runs at once, a server starts with Dot's next start.",
  );
  return [head, hint, el("div", { class: "card list-box" }, groups)];
}

export function mount(root) {
  let disposed = false;
  let items = null;
  let agents = [];
  let found = null;
  let foundError = null;
  const holder = el("div", null, loadingPanel());

  function foundPart() {
    if (foundError !== null) {
      return [foundHead(), errorPanel(foundError)];
    }
    return found === null ? [foundHead(), loadingPanel()] : foundSection(found, foundChanged);
  }

  function paint() {
    if (items === null) {
      return;
    }
    if (items.length === 0) {
      holder.replaceChildren(
        emptyPanel("No learned skills yet", "After a task that went well, Dot writes how it did it as a skill for the next one of its kind."),
        ...foundPart(),
      );
      return;
    }
    const named = withAgentNames(items, agents);
    const active = named.filter((skill) => !skill.retired);
    const retired = named.filter((skill) => skill.retired);
    const parts = [];
    if (active.length > 0) {
      parts.push(el("div", { class: "card list-box" }, active.map((skill) => skillRow(skill, changed))));
    }
    if (retired.length > 0) {
      parts.push(
        el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, "Retired")),
        el("div", { class: "card list-box" }, retired.map((skill) => skillRow(skill, changed))),
      );
    }
    holder.replaceChildren(...parts, ...foundPart());
  }

  function foundChanged(after) {
    if (after !== null && !disposed) {
      found = after;
      paint();
    }
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

  // Read apart from the learned skills, so that a failure here leaves them on the page.
  async function loadFound() {
    try {
      found = await api.get("/skills/elsewhere");
    } catch (error) {
      foundError = error;
    }
    if (!disposed) {
      paint();
    }
  }

  root.replaceChildren(
    paneHead("What Dot and its agents learned from their own work, offered to later runs as advice, and what the Mac's other agents set up.", []),
    holder,
  );
  load();
  loadFound();
  return () => {
    disposed = true;
  };
}
