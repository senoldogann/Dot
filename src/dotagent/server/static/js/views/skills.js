// Customize › Skills (item 317), after Codex's page: the skills Dot reads, as cards two a row (one on a phone), each
// with its mark, name, what it is for and that Dot has it. Personal holds the owner's own skills (Dot's skills folder
// and the folders of the config), those imported from the Mac's other agents and those Dot and its agents learned from
// their own work, each with the kind of work, where it acted and how it fared; an agent's is offered to its runs alone
// until Share, and Drop removes one (item 94). System holds the skills that come with Dot and those of its plugins. A
// search over both, and a section shows its first cards with the others a click away. Save from a chat writes a
// finished chat's work as a skill with the review model (item 318). Under Personal, what the Mac's other
// agents set up (item 164): their skills, folded by agent, and their MCP servers by name and what they reach, never
// their arguments or keys. Dot uses one only once the owner adds it here, after a confirmation (item 165): a skill is
// copied into Dot's skills folder and offered to runs at once, a server is started from Dot's next start; Remove takes
// it back. Import from other apps adds several at once from a checklist (item 219).

import { api, query } from "../api.js";
import { ago, clip, el, foldSearch, plural } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, foldGroup, loadingPanel, segmented } from "../panels.js";
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

/** Whether Dot can add a found item: it does not have it, and nothing stands in the way. */
function addable(item) {
  return !item.added && !item.used && item.refusal === null;
}

/** What a found MCP server starts or reaches, in a few words. */
function reachOf(server) {
  return server.transport === "stdio" ? `Starts ${server.reaches}` : `${server.transport.toUpperCase()} to ${server.reaches}`;
}

/**
 * The owner's choice of what to import (item 219): a checklist of the skills, by agent, and the MCP servers that Dot can
 * add, the skills chosen at first and the servers not, as a server starts a program of this Mac. Resolves to the items
 * chosen (`{ kind, agent, name }`), or null when the owner closed it.
 */
function importDialog(found) {
  return new Promise((resolve) => {
    const skills = found.skills.filter(addable);
    const servers = found.servers.filter(addable);
    const ticks = [];
    const dialog = el("dialog", { class: "modal import-dialog", "aria-labelledby": "import-title" });
    const go = el("button", { class: "btn primary", type: "button", onclick: () => dialog.close("ok") });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, "Cancel");
    const all = el("input", { type: "checkbox" });

    function paint() {
      const chosen = ticks.filter((tick) => tick.box.checked).length;
      go.textContent = chosen === 0 ? "Import" : `Import ${chosen}`;
      go.disabled = chosen === 0;
      all.checked = chosen === ticks.length;
      all.indeterminate = chosen > 0 && chosen < ticks.length;
    }

    function tick(kind, item, chosen, detail) {
      const box = el("input", { type: "checkbox", checked: chosen, onchange: paint });
      ticks.push({ box, item: { kind, agent: item.agent, name: item.name } });
      return el(
        "label",
        { class: "import-row" },
        box,
        el("span", { class: "import-row-main" }, el("span", { class: "import-row-name" }, item.name), el("span", { class: "import-row-detail" }, detail)),
      );
    }

    const agents = new Map();
    for (const skill of skills) {
      agents.set(skill.agent, [...(agents.get(skill.agent) ?? []), skill]);
    }
    const groups = [...agents].map(([agent, list]) =>
      el(
        "div",
        { class: "import-group" },
        el("div", { class: "import-group-head" }, agent, el("span", { class: "hint" }, plural(list.length, "skill", "skills"))),
        list.map((skill) => tick("skill", skill, true, clip(skill.description, 140))),
      ),
    );
    if (servers.length > 0) {
      groups.push(
        el(
          "div",
          { class: "import-group" },
          el("div", { class: "import-group-head" }, "MCP servers", el("span", { class: "hint" }, "start with Dot's next start")),
          servers.map((server) => tick("server", server, false, `${server.agent} · ${reachOf(server)}`)),
        ),
      );
    }
    all.addEventListener("change", () => {
      for (const { box } of ticks) {
        box.checked = all.checked;
      }
      paint();
    });
    const others = found.skills.length + found.servers.length - skills.length - servers.length;
    dialog.append(
      el(
        "div",
        { class: "modal-body" },
        el("h3", { id: "import-title" }, "Import from other apps"),
        el(
          "p",
          { class: "muted" },
          "What Claude Code, Codex and the Mac's other agents set up. A skill is copied into Dot and offered to runs at once; an MCP server starts with Dot's next start, as its app starts it.",
        ),
        el("label", { class: "import-all" }, all, el("span", null, "Choose all")),
        el("div", { class: "import-list" }, groups),
        others === 0 ? null : el("p", { class: "hint" }, `${plural(others, "other item is", "other items are")} in Dot already or cannot be added; Found on this Mac says which.`),
      ),
      el("div", { class: "modal-actions" }, cancel, go),
    );
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(dialog.returnValue === "ok" ? ticks.filter((one) => one.box.checked).map((one) => one.item) : null);
    });
    paint();
    document.body.append(dialog);
    dialog.showModal();
    go.focus();
  });
}

/**
 * Import what the owner chooses of `found` (item 219): one request adds them all, and a toast says what was added and
 * what could not be; resolves to what is found after it, or null when the owner chose nothing.
 */
export async function importFound(found) {
  const items = await importDialog(found);
  if (items === null || items.length === 0) {
    return null;
  }
  const done = await api.post("/skills/elsewhere/import", { items });
  if (done.added.length > 0) {
    toast("ok", `Imported ${plural(done.added.length, "item", "items")}.`);
  }
  for (const refused of done.refused) {
    toast("error", `${refused.item.name} was not imported: ${refused.reason}`);
  }
  return done.found;
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
  const reach = reachOf(server);
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
    return [head, emptyPanel("search", "Nothing found", "No other agent of this Mac (Claude Code, Claude Desktop, Cursor, VS Code, Codex) has skills or MCP servers set up.")];
  }
  const hint = el(
    "div",
    { class: "hint" },
    "Skills and MCP servers that other agents of this Mac set up. Dot uses one only once you add it: a skill is copied and offered to runs at once, a server starts with Dot's next start.",
  );
  return [head, hint, el("div", { class: "card list-box" }, groups)];
}

// A section shows this many cards, and the rest after See more.
const SHOWN_CARDS = 6;
// A skill's source as the catalogue names it (skills/catalog.py), and what its card says of it.
const BUILTIN_SOURCE = "built-in";
const PLUGIN_SOURCE = "plugin ";
const OWN_SOURCES = new Set(["home", "config"]);

/** A card: the skill's mark, its name with that Dot has it, what it is for, its facts and its actions. */
function skillCard({ name, description, mark, facts, actions, have }) {
  return el(
    "div",
    { class: "skill-card", dataset: { skill: name } },
    el("span", { class: "skill-mark", "aria-hidden": "true" }, icon(mark)),
    el(
      "div",
      { class: "skill-main" },
      el(
        "div",
        { class: "skill-head" },
        el("div", { class: "skill-name" }, name),
        el("span", { class: `skill-have${have === "Retired" ? " retired" : ""}` }, have === "Retired" ? null : icon("check"), have),
      ),
      el("div", { class: "skill-does" }, description),
      facts,
      actions.length === 0 ? null : el("div", { class: "skill-actions" }, actions),
    ),
  );
}

/** A card's facts: its chips, then one quiet line of the rest. */
function factsOf(chips, words) {
  return [
    chips.length === 0 ? null : el("div", { class: "row-meta skill-chips" }, chips),
    words.length === 0 ? null : el("div", { class: "skill-facts" }, words.join(" · ")),
  ];
}

/** A learned skill as a card: the kind of work, the place, the agent whose it is, the runs and the record, with Share
 * for an agent's and Drop. `onChanged` gets the list after an action. */
function learnedCard(skill, onChanged) {
  const drop = el("button", { class: "btn ghost sm", type: "button" }, "Drop");
  drop.addEventListener("click", () => attempt(async () => onChanged(await dropSkill(skill))));
  const share = skill.agent_id === null ? null : el("button", { class: "btn ghost sm", type: "button" }, skill.shared ? "Keep to the agent" : "Share");
  if (share !== null) {
    share.addEventListener("click", () => attempt(async () => onChanged(await shareSkill(skill, !skill.shared))));
  }
  const record = skill.uses + skill.failures === 0 ? "Not read by a run yet" : `${plural(skill.uses, "run", "runs")} done after reading it, ${skill.failures} failed`;
  const chips = [
    el("span", { class: "chip" }, skill.kind),
    skill.agent_id === null ? null : el("span", { class: "chip" }, skill.shared ? `${skill.agentName ?? "An agent"}, shared` : (skill.agentName ?? "An agent")),
  ].filter((chip) => chip !== null);
  const facts = factsOf(chips, [skill.host, `Learned from ${plural(skill.runs.length, "run", "runs")}, last ${ago(skill.updated_at)}`, record]);
  return skillCard({ name: skill.name, description: skill.description, mark: "brain", facts, actions: [share, drop].filter((one) => one !== null), have: skill.retired ? "Retired" : "Learned" });
}

// A chat's run that can give a skill at the owner's word, by what the page knows of it: it ended done, it is no
// delegated run, no eval's, and not OpenCode's agent's, whose tools a skill of Dot's cannot name (agent/playbooks.py
// `unsavable`, which also wants an acting step: the server says so of a chat without one).
const EVAL_SOURCE = "eval";
const OPENCODE_MODEL = "opencode:";
// The runs the dialog looks among, newest first: the most the API gives in one read.
const CHAT_RUNS = 200;

function savable(run) {
  return run.status === "done" && run.parent_run_id === null && run.source !== EVAL_SOURCE && !run.model.startsWith(OPENCODE_MODEL);
}

/**
 * The owner's choice of a finished chat whose work becomes a skill, and its saving: `save(run)` writes it while the
 * dialog stays open, its rows off and the chosen one saying so; a refusal or a failure is said in the dialog, which
 * stays open for another choice. Resolves to what `save` gave, or null when the owner closed the dialog.
 */
function chatDialog(runs, save) {
  return new Promise((resolve) => {
    let saved = null;
    const dialog = el("dialog", { class: "modal chat-skill-dialog", "aria-labelledby": "chat-skill-title" });
    const problem = el("p", { class: "hint chat-skill-problem", role: "alert", hidden: true });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, "Cancel");
    const rows = runs.map((run) => {
      const when = el("span", { class: "hint" }, ago(run.finished_at ?? run.created_at));
      const row = el("button", { class: "chat-skill-row", type: "button" }, el("span", { class: "chat-skill-goal" }, clip(run.goal, 120)), when);
      row.addEventListener("click", async () => {
        for (const one of rows) {
          one.disabled = true;
        }
        cancel.disabled = true;
        problem.hidden = true;
        when.textContent = "Saving…";
        try {
          saved = await save(run);
          dialog.close("ok");
        } catch (error) {
          problem.textContent = `${clip(run.goal, 60)}: ${error.message}`;
          problem.hidden = false;
          when.textContent = ago(run.finished_at ?? run.created_at);
          for (const one of rows) {
            one.disabled = false;
          }
          cancel.disabled = false;
        }
      });
      return row;
    });
    dialog.append(
      el(
        "div",
        { class: "modal-body" },
        el("h3", { id: "chat-skill-title" }, "Save a skill from a chat"),
        el("p", { class: "muted" }, "Dot's review model writes how the chat's work was done as a skill, offered to the runs of its kind after it."),
        rows.length === 0 ? el("p", { class: "hint" }, "No chat has ended done yet.") : el("div", { class: "chat-skill-list" }, rows),
        problem,
      ),
      el("div", { class: "modal-actions" }, cancel),
    );
    // While a skill is being written the dialog stays: Escape waits for the answer too.
    dialog.addEventListener("cancel", (event) => {
      if (cancel.disabled) {
        event.preventDefault();
      }
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(saved);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

export function mount(root) {
  let disposed = false;
  let palette = null;
  let items = null;
  let agents = [];
  let found = null;
  let foundError = null;
  let shown = "personal";
  let words = "";
  // The sections opened past their first cards.
  const opened = new Set();
  const holder = el("div", { class: "stack" }, loadingPanel());
  const importButton = el("button", { class: "btn sm", type: "button", disabled: true }, icon("download"), "Import from other apps");
  importButton.addEventListener("click", () => attempt(async () => foundChanged(await importFound(found))));
  const saveButton = el("button", { class: "btn sm", type: "button" }, icon("plus"), "Save from a chat");
  saveButton.addEventListener("click", () => attempt(saveFromChat));
  const search = el("input", { class: "input plugin-search", type: "search", placeholder: "Search skills", "aria-label": "Search skills", autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const tabs = segmented(
    [
      { id: "personal", label: "Personal", hint: "Your skills, those you imported and those Dot learned" },
      { id: "system", label: "System", hint: "The skills that come with Dot and with its plugins" },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  tabs.element.classList.add("plugin-tabs");
  tabs.element.setAttribute("aria-label", "Skills to show");

  // The button can be pressed once what other apps set up is read and something of it can be added.
  function paintImport() {
    const count = found === null ? 0 : found.skills.filter(addable).length + found.servers.filter(addable).length;
    importButton.disabled = count === 0;
    importButton.title =
      found === null
        ? foundError === null
          ? "Looking for what other apps set up…"
          : "What other apps set up could not be read"
        : count === 0
          ? "Nothing found that Dot can add"
          : `${plural(count, "item", "items")} found that Dot can add`;
  }

  function foundPart() {
    if (foundError !== null) {
      return [foundHead(), errorPanel(foundError)];
    }
    return found === null ? [foundHead(), loadingPanel()] : foundSection(found, foundChanged);
  }

  function matches(name, description) {
    return words === "" || foldSearch(`${name} ${description}`).includes(words);
  }

  /** A section of cards: its title and count, its first cards and See more for the others, or nothing when the search
   * leaves none. */
  function section(id, title, cards) {
    if (cards.length === 0) {
      return null;
    }
    const open = opened.has(id) || words !== "";
    const more =
      open || cards.length <= SHOWN_CARDS
        ? null
        : el(
            "button",
            {
              class: "btn ghost sm skill-more",
              type: "button",
              onclick: () => {
                opened.add(id);
                paint();
              },
            },
            `See ${cards.length - SHOWN_CARDS} more`,
          );
    return el(
      "section",
      { class: "plugin-section", dataset: { section: id } },
      el("h2", { class: "section-label plugin-section-title" }, title, el("span", { class: "hint" }, String(cards.length))),
      el("div", { class: "skill-grid" }, open ? cards : cards.slice(0, SHOWN_CARDS)),
      more,
    );
  }

  /** The skills Dot copied from the Mac's other agents, by name: the agent each came from, its description (Dot's
   * copy's, for one its agent no longer has) and whether its agent still has it. */
  function imported() {
    if (found === null) {
      return new Map();
    }
    const described = new Map(palette.skills.map((skill) => [skill.name, skill.description]));
    const kept = found.skills.filter((skill) => skill.added).map((skill) => [skill.name, { ...skill, gone: false }]);
    const gone = found.gone
      .filter((item) => item.kind === "skill")
      .map((item) => [item.name, { ...item, description: described.get(item.name) ?? "", gone: true }]);
    return new Map([...kept, ...gone]);
  }

  /** Take an imported skill back, after a yes: through what is found, or as one its agent no longer has. */
  async function removeImported(skill) {
    if (!skill.gone) {
      return changeFound("skill", skill, false);
    }
    return askAndChange(goneDialog(skill), { kind: "skill", agent: skill.agent, name: skill.name, added: false }, `Removed ${skill.name}.`);
  }

  function personal() {
    const copies = imported();
    const own = palette.skills.filter((skill) => OWN_SOURCES.has(skill.source) && !copies.has(skill.name) && matches(skill.name, skill.description));
    const ownCards = own.map((skill) =>
      skillCard({ name: skill.name, description: skill.description, mark: "sparkle", facts: factsOf([], [skill.source === "home" ? "In Dot's skills folder" : "In a folder of the config"]), actions: [], have: "Installed" }),
    );
    const importedCards = [...copies.values()]
      .filter((skill) => matches(skill.name, skill.description))
      .map((skill) => {
        const remove = el("button", { class: "btn ghost sm", type: "button" }, "Remove");
        remove.addEventListener("click", () => attempt(async () => foundChanged(await removeImported(skill))));
        const from = skill.gone ? `Imported from ${skill.agent}, which no longer has it` : `Imported from ${skill.agent}`;
        return skillCard({ name: skill.name, description: skill.description, mark: "download", facts: factsOf([], [from]), actions: [remove], have: "Installed" });
      });
    const named = withAgentNames(items, agents).filter((skill) => matches(skill.name, skill.description));
    const learnedCards = named.map((skill) => learnedCard(skill, changed));
    const parts = [section("own", "Yours", ownCards), section("imported", "Imported", importedCards), section("learned", "Learned", learnedCards)];
    if (parts.every((part) => part === null)) {
      parts.push(
        words === ""
          ? emptyPanel("sparkles", "No skills of yours yet", "After a task that went well, Dot writes how it did it as a skill for the next one of its kind; Save from a chat writes one at your word.")
          : emptyPanel("search", "No skill matches the search", ""),
      );
    }
    return [...parts, ...foundPart()];
  }

  function system() {
    const builtin = palette.skills.filter((skill) => skill.source === BUILTIN_SOURCE && matches(skill.name, skill.description));
    const fromPlugins = palette.skills.filter((skill) => skill.source.startsWith(PLUGIN_SOURCE) && matches(skill.name, skill.description));
    const parts = [
      section(
        "builtin",
        "Comes with Dot",
        builtin.map((skill) => skillCard({ name: skill.name, description: skill.description, mark: "sparkle", facts: factsOf([], ["Comes with Dot"]), actions: [], have: "Installed" })),
      ),
      section(
        "plugins",
        "From plugins",
        fromPlugins.map((skill) =>
          skillCard({ name: skill.name, description: skill.description, mark: "plug", facts: factsOf([el("span", { class: "chip" }, skill.source.slice(PLUGIN_SOURCE.length))], []), actions: [], have: "Installed" }),
        ),
      ),
    ];
    if (parts.every((part) => part === null)) {
      parts.push(
        words === ""
          ? emptyPanel("sparkles", "Neither Dot nor a plugin brings a skill", "[skills] builtin is off and no plugin has one.")
          : emptyPanel("search", "No skill matches the search", ""),
      );
    }
    return parts;
  }

  function paint() {
    paintImport();
    if (disposed || palette === null || items === null) {
      return;
    }
    const [personalButton, systemButton] = tabs.element.children;
    const copies = imported();
    const personalCount = palette.skills.filter((skill) => OWN_SOURCES.has(skill.source) && !copies.has(skill.name)).length + copies.size + items.length;
    const systemCount = palette.skills.filter((skill) => skill.source === BUILTIN_SOURCE || skill.source.startsWith(PLUGIN_SOURCE)).length;
    personalButton.textContent = `Personal ${personalCount}`;
    systemButton.textContent = `System ${systemCount}`;
    const parts = shown === "personal" ? personal() : system();
    holder.replaceChildren(...parts.filter((part) => part !== null));
  }

  // After a change the page paints at once, then reads the skills Dot offers again: a skill imported or taken back
  // changes them. One taken back leaves them at once, so that it does not show as one of yours meanwhile.
  function foundChanged(after) {
    if (after === null || disposed) {
      return;
    }
    if (palette !== null) {
      const before = [...imported().keys()];
      const still = new Set([...after.skills.filter((skill) => skill.added), ...after.gone.filter((item) => item.kind === "skill")].map((skill) => skill.name));
      const removed = new Set(before.filter((name) => !still.has(name)));
      palette = { ...palette, skills: palette.skills.filter((skill) => !removed.has(skill.name)) };
    }
    found = after;
    paint();
    attempt(loadPalette);
  }

  function changed(list) {
    if (list !== null && !disposed) {
      items = list;
      paint();
      attempt(loadPalette);
    }
  }

  async function saveFromChat() {
    const runs = await api.get(`/runs${query({ limit: CHAT_RUNS })}`);
    const saved = await chatDialog(runs.filter(savable), (run) => api.post(`/runs/${encodeURIComponent(run.id)}/skill`, {}));
    if (saved === null) {
      return;
    }
    toast("ok", saved.updated ? `Updated the skill ${saved.name}.` : `Saved the skill ${saved.name}.`);
    const page = await api.get("/skills/learned");
    changed(page.items);
  }

  async function loadPalette() {
    const offered = await api.get("/palette");
    if (!disposed) {
      palette = offered;
      paint();
    }
  }

  async function load() {
    try {
      const [page, rows, offered] = await Promise.all([api.get("/skills/learned"), api.get("/agents"), api.get("/palette")]);
      if (disposed) {
        return;
      }
      items = page.items;
      agents = rows;
      palette = offered;
      paint();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error));
      }
    }
  }

  // Read apart from the skills, so that a failure here leaves them on the page.
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

  // Out of what paints again, so that the search keeps the focus while the cards are drawn anew.
  root.append(el("div", { class: "plugin-bar" }, search, tabs.element, saveButton, importButton), holder);
  load();
  loadFound();
  return () => {
    disposed = true;
  };
}
