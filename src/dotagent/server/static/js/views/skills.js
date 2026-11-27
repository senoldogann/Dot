// Settings › Skills (items 317 and 545), after Codex's page: the head the three pages of Connectors, Plugins and Skills share
// (customizehead.js) with the search, Personal and System, Save from a chat and Import from other apps, and the skills
// Dot reads, as one list, a row each (item 946), each with its icon in the ink on the quiet tile, name, what it is for
// and that Dot has it. Personal holds the owner's own skills (Dot's skills folder
// and the folders of the config), those imported from the Mac's other agents and those Dot and its agents learned from
// their own work, each with the kind of work, where it acted and how it fared; an agent's is offered to its runs alone
// until Share, and Drop removes one (item 94). System holds the skills that come with Dot and those of its plugins. A
// search over both, and a section shows its first cards with the others a click away. Save from a chat writes a
// finished chat's work as a skill with the review model (item 318). Under Personal, what the Mac's other
// agents set up (item 164): their skills, folded by agent, and their MCP servers by name and what they reach, never
// their arguments or keys. Dot uses one only once the owner adds it here, after a confirmation (item 165): a skill is
// copied into Dot's skills folder and offered to runs at once, a server is started from Dot's next start; Remove takes
// it back. Import from other apps adds several at once from a checklist (item 219). A learned skill no run read for
// [agent] skill_rest_days rests (item 585): it is shown as resting, last in its section, and is not offered to runs
// until Wake; Pin keeps one from resting; Versions lists the ones a rewrite, a merge or a restore kept, with the steps
// each changed, and Restore puts one back. What the nightly reflection proposes (a merge of two skills that say the
// same) waits above them as cards, with the steps it would write, for the owner's yes or no. A skill Dot could not offer
// to a task (a SKILL.md that does not parse, a name another skill has) is listed under "Left out" at the foot of the tab
// it belongs on, a closed fold with the file and the reason (`GET /api/skills/left-out`, item 1405).

import { api, query } from "../api.js";
import { customizeHead } from "../customizehead.js";
import { ago, clip, el, foldSearch, } from "../dom.js";
import { icon } from "../icons.js";
import { IMPORTED_SKILL_MARK, LEARNED_SKILL_MARK, skillMark } from "../marks.js";
import { emptyPanel, errorPanel, foldGroup, loadingPanel, segmented } from "../panels.js";
import { leftOutChip, leftOutIsSystem, leftOutName, leftOutSummary } from "../skillleftout.js";
import { proposalCard } from "../skillproposals.js";
import { WHOLE_RUN, providerOf } from "../providers.js";
import { showVersions } from "../skillversions.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { t, tCount } from "../i18n.js";

/** Drop a learned skill after a confirmation; resolves to the list after it, or null when the owner kept it. */
async function dropSkill(skill) {
  const confirmed = await confirmDialog({
    title: t("views.skills.dropName", { name: skill.name }),
    message: t("views.skills.theSkillIsRemovedA"),
    confirmLabel: t("views.skills.drop"),
    danger: true,
    focusCancel: true,
  });
  if (!confirmed) {
    return null;
  }
  const page = await api.del(`/skills/learned/${encodeURIComponent(skill.name)}`);
  toast("ok", t("views.skills.droppedName", { name: skill.name }));
  return page.items;
}

/** Offer an agent's skill to every run, or to the agent's alone again; resolves to the list after it. */
async function shareSkill(skill, shared) {
  const page = await api.put(`/skills/learned/${encodeURIComponent(skill.name)}`, { shared });
  toast("ok", shared ? t("views.skills.nameIsOfferedToEvery", { name: skill.name }) : t("views.skills.nameIsAgentnameSAlone", { name: skill.name, agentName: skill.agentName }));
  return page.items;
}

/** Pin a learned skill so that it never rests (a resting one wakes), or take the pin off; resolves to the list after it. */
async function pinSkill(skill, pinned) {
  const page = await api.put(`/skills/learned/${encodeURIComponent(skill.name)}/pin`, { pinned });
  toast("ok", pinned ? t("views.skills.nameIsPinnedItNever", { name: skill.name }) : t("views.skills.nameIsNotPinned", { name: skill.name }));
  return page.items;
}

/** Wake a learned skill that rests, for disuse, by hand or for a merge; resolves to the list after it. */
async function wakeSkill(skill) {
  const page = await api.post(`/skills/learned/${encodeURIComponent(skill.name)}/wake`, {});
  toast("ok", t("views.skills.nameIsAwakeTasksAre", { name: skill.name }));
  return page.items;
}

/** Why a skill rests, in words: no run read it for `restDays` days, the owner put it to rest, or a merge put it aside. */
/** What a skill that was put away reads as, and the check for it. */
const RETIRED = t("views.skills.retired");

function restWords(skill, restDays) {
  if (skill.rest_reason === "merged") {
    return t("views.skills.mergedIntoMergedInto", { merged_into: skill.merged_into });
  }
  return skill.rest_reason === "owner" ? t("views.skills.putToRestByYou") : t("views.skills.noTaskReadItFor", { part: tCount(restDays, "views.skills.countDay", "views.skills.countDays") });
}

/**
 * One learned skill as a row: the name and the description, then the kind, the place, the agent whose it is, the runs
 * and the record. `skill.agentName` names the agent when the skill is one's; `onChanged` gets the list after an action.
 */
export function skillRow(skill, onChanged) {
  const drop = el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.drop"));
  drop.addEventListener("click", () => attempt(async () => onChanged(await dropSkill(skill))));
  const share =
    skill.agent_id === null
      ? null
      : el("button", { class: "btn ghost sm", type: "button" }, skill.shared ? t("views.skills.keepToTheAgent") : t("views.skills.share"));
  if (share !== null) {
    share.addEventListener("click", () => attempt(async () => onChanged(await shareSkill(skill, !skill.shared))));
  }
  const record =
    skill.uses + skill.failures === 0
      ? t("views.skills.notReadByATask")
      : t("views.skills.partDoneAfterReadingIt", { part: tCount(skill.uses, "views.skills.countTask", "views.skills.countTasks"), failures: skill.failures });
  const whose =
    skill.agent_id === null
      ? null
      : el("span", { class: "chip" }, skill.shared ? t("views.skills.valueShared", { value: skill.agentName ?? t("views.skills.anAgent") }) : (skill.agentName ?? t("views.skills.anAgent")));
  // A skill that rests is not offered to runs (item 585): the row says so and Wake offers it again.
  const wake = skill.resting ? el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.wake")) : null;
  if (wake !== null) {
    wake.addEventListener("click", () => attempt(async () => onChanged(await wakeSkill(skill))));
  }
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
        skill.resting ? el("span", { class: "chip" }, t("views.skills.resting")) : null,
        skill.pinned ? el("span", { class: "chip" }, t("views.skills.pinned")) : null,
        el("span", null, skill.host),
        el("span", null, t("views.skills.learnedFromPartLastAgo", { part: tCount(skill.runs.length, "views.skills.countTask", "views.skills.countTasks"), ago: ago(skill.updated_at) })),
        el("span", null, record),
      ),
    ),
    el("div", { class: "row-actions" }, wake, share, drop),
  );
}

/** The skills with the name of the agent each belongs to, from the agents list. */
function withAgentNames(items, agents) {
  const names = new Map(agents.map((row) => [row.record.id, row.record.name]));
  return items.map((skill) => ({ ...skill, agentName: skill.agent_id === null ? null : names.get(skill.agent_id) ?? null }));
}

/** What the owner is asked before a skill or an MCP server found elsewhere is added to Dot, or taken back. */
function foundDialog(kind, item, added) {
  if (kind === "skill") {
    return added
      ? {
          title: t("views.skills.addNameToDot", { name: item.name }),
          message: t("views.skills.dotCopiesThisSkillOf", { agent: item.agent, agent2: item.agent }),
          confirmLabel: t("views.skills.add"),
        }
      : {
          title: t("views.skills.removeNameFromDot", { name: item.name }),
          message: t("views.skills.dotDeletesItsCopyAnd", { agent: item.agent }),
          confirmLabel: t("views.skills.remove"),
          focusCancel: true,
        };
  }
  return added
    ? {
        title: t("views.skills.addTheConnectedAppName", { name: item.name }),
        message: t("views.skills.fromItsNextStartDot", { agent: item.agent }),
        confirmLabel: t("views.skills.add"),
      }
    : {
        title: t("views.skills.removeTheConnectedAppName", { name: item.name }),
        message: t("views.skills.dotNoLongerStartsIt", { agent: item.agent }),
        confirmLabel: t("views.skills.remove"),
        focusCancel: true,
      };
}

/** What the owner is asked before something added from an agent that no longer has it is taken back. */
function goneDialog(item) {
  return item.kind === "skill"
    ? {
        title: t("views.skills.removeNameFromDot", { name: item.name }),
        message: t("views.skills.agentNoLongerHasThis", { agent: item.agent }),
        confirmLabel: t("views.skills.remove"),
        focusCancel: true,
      }
    : {
        title: t("views.skills.removeTheConnectedAppName", { name: item.name }),
        message: t("views.skills.agentNoLongerHasThis2", { agent: item.agent }),
        confirmLabel: t("views.skills.remove"),
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
function changeFound(kind, item, added) {
  const done = !added ? t("views.skills.removedName", { name: item.name }) : kind === "skill" ? t("views.skills.addedNameTasksAreOffered", { name: item.name }) : t("views.skills.addedNameDotStartsIt", { name: item.name });
  return askAndChange(foundDialog(kind, item, added), { kind, agent: item.agent, name: item.name, added }, done);
}

/** Whether Dot can add a found item: it does not have it, and nothing stands in the way. */
function addable(item) {
  return !item.added && !item.used && item.refusal === null;
}

/** What a found MCP server starts or reaches, in a few words. */
function reachOf(server) {
  return server.transport === "stdio" ? t("views.skills.startsReaches", { reaches: server.reaches }) : t("views.skills.connectsToReaches", { reaches: server.reaches });
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
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, t("views.skills.cancel"));
    const all = el("input", { type: "checkbox" });

    function paint() {
      const chosen = ticks.filter((tick) => tick.box.checked).length;
      go.textContent = chosen === 0 ? t("views.skills.import") : t("views.skills.importChosen", { chosen });
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
        el("div", { class: "import-group-head" }, agent, el("span", { class: "hint" }, tCount(list.length, "views.skills.countSkill", "views.skills.countSkills"))),
        list.map((skill) => tick("skill", skill, true, clip(skill.description, 140))),
      ),
    );
    if (servers.length > 0) {
      groups.push(
        el(
          "div",
          { class: "import-group" },
          el("div", { class: "import-group-head" }, t("views.skills.connectedApps"), el("span", { class: "hint" }, t("views.skills.startWithDotSNext"))),
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
        el("h3", { id: "import-title" }, t("views.skills.importFromOtherApps")),
        el(
          "p",
          { class: "muted" },
          t("views.skills.whatClaudeCodeCodexAnd"),
        ),
        el("label", { class: "import-all" }, all, el("span", null, t("views.skills.chooseAll"))),
        el("div", { class: "import-list" }, groups),
        others === 0 ? null : el("p", { class: "hint" }, t("views.skills.partInDotAlreadyOr", { part: tCount(others, "views.skills.countOtherItemIs", "views.skills.countOtherItemsAre") })),
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
async function importFound(found) {
  const items = await importDialog(found);
  if (items === null || items.length === 0) {
    return null;
  }
  const done = await api.post("/skills/elsewhere/import", { items });
  if (done.added.length > 0) {
    toast("ok", t("views.skills.importedPart", { part: tCount(done.added.length, "views.skills.countItem", "views.skills.countItems") }));
  }
  for (const refused of done.refused) {
    toast("error", t("views.skills.nameWasNotImportedReason", { name: refused.item.name, reason: refused.reason }));
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
    const remove = el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.remove"));
    remove.addEventListener("click", () => change(false));
    const refusal = item.refusal === null ? null : refusalLine(t("views.skills.dotLeavesItOutWhen", { refusal: item.refusal }));
    return { chip: el("span", { class: "chip" }, t("views.skills.added")), refusal, action: remove };
  }
  if (item.used) {
    return { chip: el("span", { class: "chip" }, hasIt), refusal: null, action: null };
  }
  if (item.refusal !== null) {
    return { chip: null, refusal: refusalLine(t("views.skills.canTAddRefusal", { refusal: item.refusal })), action: null };
  }
  const add = el("button", { class: "btn sm", type: "button" }, t("views.skills.addToDot"));
  add.addEventListener("click", () => change(true));
  return { chip: null, refusal: null, action: add };
}

/** A skill another agent of this Mac set up: its name and description, whether Dot has it, and Add or Remove. */
function foundSkillRow(skill, onFound) {
  const { chip, refusal, action } = foundState("skill", skill, t("views.skills.dotReadsIt"), onFound);
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
  const { chip, refusal, action } = foundState("server", server, t("views.skills.dotHasIt"), onFound);
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
  const remove = el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.remove"));
  const body = { kind: item.kind, agent: item.agent, name: item.name, added: false };
  remove.addEventListener("click", () => attempt(async () => onFound(await askAndChange(goneDialog(item), body, t("views.skills.removedName", { name: item.name })))));
  const what = item.kind === "skill" ? t("views.skills.skillDotKeepsItsCopy") : t("views.skills.connectedAppDotLeavesIt");
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
  return el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, t("views.skills.foundOnThisMac")));
}

/**
 * What the Mac's other agents set up (item 164): the skills of each agent, then the MCP servers, then what the owner
 * added that its agent no longer has, then what was left out. `onFound` gets what is found after the owner adds or
 * removes one (item 165).
 */
function foundSection(found, onFound) {
  const agents = new Map();
  for (const skill of found.skills) {
    agents.set(skill.agent, [...(agents.get(skill.agent) ?? []), skill]);
  }
  const groups = [...agents].map(([agent, skills]) => {
    const read = skills.filter((skill) => skill.used).length;
    const extra = read > 0 ? el("span", { class: "chip" }, t("views.skills.dotReadsRead", { read })) : null;
    // An element id holds no space ("Claude Code plugin x" becomes claude-code-plugin-x), or aria-controls breaks.
    const slug = agent.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return foundGroup(`found-skills-${slug}`, agent, tCount(skills.length, "views.skills.countSkill", "views.skills.countSkills"), extra, skills.map((skill) => foundSkillRow(skill, onFound)));
  });
  if (found.servers.length > 0) {
    groups.push(foundGroup("found-servers", t("views.skills.connectedApps"), tCount(found.servers.length, "views.skills.countConnectedApp", "views.skills.countConnectedApps"), null, found.servers.map((server) => foundServerRow(server, onFound))));
  }
  if (found.gone.length > 0) {
    groups.push(foundGroup("found-gone", t("views.skills.addedNoLongerSetUp"), tCount(found.gone.length, "views.skills.countItem", "views.skills.countItems"), null, found.gone.map((item) => goneRow(item, onFound))));
  }
  if (found.problems.length > 0) {
    const rows = found.problems.map((problem) =>
      el("div", { class: "list-row" }, el("div", { class: "row-main" }, el("div", { class: "row-text" }, problem.path), el("div", { class: "row-meta" }, problem.reason))),
    );
    groups.push(foundGroup("found-problems", t("views.skills.leftOut"), tCount(found.problems.length, "views.skills.countFile", "views.skills.countFiles"), null, rows));
  }
  const head = foundHead();
  if (groups.length === 0) {
    return [head, emptyPanel("search", t("views.skills.nothingFound"), t("views.skills.noOtherAgentOfThis"))];
  }
  const hint = el(
    "div",
    { class: "hint" },
    t("views.skills.skillsAndConnectedAppsThat"),
  );
  return [head, hint, el("div", { class: "card list-box" }, groups)];
}

/** A skill Dot could not offer, as a row (item 1405): its name, why, and the file it is in. */
function leftOutRow(entry) {
  return el(
    "div",
    { class: "list-row", dataset: { leftOut: leftOutName(entry.path) } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, leftOutName(entry.path)),
      el("div", { class: "row-text" }, entry.reason),
      el("div", { class: "row-meta" }, el("span", { class: "chip" }, leftOutChip(entry)), el("span", null, entry.path)),
    ),
  );
}

/**
 * The skills Dot could not offer to a task, as a part of the page (item 1405): a head, one plain sentence of what is
 * wrong and a closed fold with a row each, the file and the catalog's reason in it; nothing when `entries` is empty. Each
 * tab's fold is its own (`tab`, "personal" or "system"): opening one does not open the other.
 */
function leftOutSection(entries, tab) {
  if (entries.length === 0) {
    return [];
  }
  const head = el("div", { class: "row section-head" }, el("div", { class: "section-label grow" }, t("views.skills.leftOut")));
  const hint = el("div", { class: "hint", dataset: { leftOutHint: "" } }, leftOutSummary(entries));
  const group = foundGroup(`left-out-${tab}`, t("views.skills.skillsDotCouldNotUse"), tCount(entries.length, "views.skills.countSkill", "views.skills.countSkills"), null, entries.map(leftOutRow));
  return [head, hint, el("div", { class: "card list-box" }, group)];
}

// A section shows this many cards, and the rest after See more.
const SHOWN_CARDS = 6;
// A skill's source as the catalogue names it (skills/catalog.py), and what its card says of it.
const BUILTIN_SOURCE = "built-in";
const PLUGIN_SOURCE = "plugin ";
const OWN_SOURCES = new Set(["home", "config"]);

/** A card: the skill's mark, its name with that Dot has it, what it is for, its facts and its actions. `haveMark` is the
 * icon beside the state and `haveClass` a class that sets its tone (a skill that rests is quiet, item 585). */
function skillCard({ name, description, mark, facts, actions, have, haveMark = "check", haveClass = "" }) {
  return el(
    "div",
    { class: "skill-card", dataset: { skill: name } },
    skillTile(mark),
    el(
      "div",
      { class: "skill-main" },
      el(
        "div",
        { class: "skill-head" },
        el("div", { class: "skill-name" }, name),
        el("span", { class: `skill-have${have === RETIRED ? " retired" : ""}${haveClass}` }, have === RETIRED ? null : icon(haveMark), have),
      ),
      el("div", { class: "skill-does" }, description),
      facts,
      actions.length === 0 ? null : el("div", { class: "skill-actions" }, actions),
    ),
  );
}

/** The mark `mark` (an icon's name) of a skill on its quiet tile, in the tile's ink (item 946). */
function skillTile(mark) {
  return el("span", { class: "skill-mark", "aria-hidden": "true" }, icon(mark));
}

/** A card's facts: its chips, then one quiet line of the rest. */
function factsOf(chips, words) {
  return [
    chips.length === 0 ? null : el("div", { class: "row-meta skill-chips" }, chips),
    words.length === 0 ? null : el("div", { class: "skill-facts" }, words.join(" · ")),
  ];
}

/** The state a learned skill's card says: retired, resting (quiet), pinned or learned, with the icon beside it. */
function learnedState(skill) {
  if (skill.retired) {
    return { have: RETIRED, haveMark: "check", haveClass: "" };
  }
  if (skill.resting) {
    return { have: t("views.skills.resting"), haveMark: "clock", haveClass: " resting" };
  }
  return skill.pinned ? { have: t("views.skills.pinned"), haveMark: "pin", haveClass: "" } : { have: t("views.skills.learned"), haveMark: "check", haveClass: "" };
}

/** A learned skill as a card: the kind of work, the place, the agent whose it is, the runs, the record and when a run last
 * read it, with Versions once it was rewritten, Wake while it rests, Pin, Share for an agent's and Drop. `onChanged` gets
 * the list after an action; `restDays` is the days after which a skill no run read rests. */
function learnedCard(skill, onChanged, restDays) {
  const drop = el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.drop"));
  drop.addEventListener("click", () => attempt(async () => onChanged(await dropSkill(skill))));
  const share = skill.agent_id === null ? null : el("button", { class: "btn ghost sm", type: "button" }, skill.shared ? t("views.skills.keepToTheAgent") : t("views.skills.share"));
  if (share !== null) {
    share.addEventListener("click", () => attempt(async () => onChanged(await shareSkill(skill, !skill.shared))));
  }
  const versions =
    skill.version > 1
      ? el("button", { class: "btn ghost sm", type: "button", title: t("views.skills.theVersionsThisSkillHad") }, icon("clock"), t("views.skills.versionsVersion", { version: skill.version }))
      : null;
  if (versions !== null) {
    versions.addEventListener("click", () => attempt(() => showVersions(skill, onChanged)));
  }
  const wake =
    skill.resting && !skill.retired
      ? el("button", { class: "btn ghost sm", type: "button", title: t("views.skills.offerThisSkillToTasks") }, t("views.skills.wake"))
      : null;
  if (wake !== null) {
    wake.addEventListener("click", () => attempt(async () => onChanged(await wakeSkill(skill))));
  }
  const pin = skill.retired
    ? null
    : el(
        "button",
        {
          class: "btn ghost sm",
          type: "button",
          "aria-pressed": String(skill.pinned),
          title: skill.pinned ? t("views.skills.takeThePinOffThis") : t("views.skills.pinThisSkillItNever"),
        },
        icon("pin"),
        skill.pinned ? t("views.skills.unpin") : t("views.skills.pin"),
      );
  if (pin !== null) {
    pin.addEventListener("click", () => attempt(async () => onChanged(await pinSkill(skill, !skill.pinned))));
  }
  const record = skill.uses + skill.failures === 0 ? t("views.skills.notReadByATask") : t("views.skills.partDoneAfterReadingIt", { part: tCount(skill.uses, "views.skills.countTask", "views.skills.countTasks"), failures: skill.failures });
  const chips = [
    el("span", { class: "chip" }, skill.kind),
    skill.agent_id === null ? null : el("span", { class: "chip" }, skill.shared ? t("views.skills.valueShared", { value: skill.agentName ?? t("views.skills.anAgent") }) : (skill.agentName ?? t("views.skills.anAgent"))),
  ].filter((chip) => chip !== null);
  const facts = factsOf(chips, [
    skill.host,
    t("views.skills.learnedFromPartLastAgo2", { part: tCount(skill.runs.length, "views.skills.countTask", "views.skills.countTasks"), ago: ago(skill.updated_at) }),
    record,
    skill.last_read_at === null ? null : t("views.skills.lastReadAgo", { ago: ago(skill.last_read_at) }),
    skill.version > 1 ? t("views.skills.versionVersion", { version: skill.version }) : null,
    skill.resting && !skill.retired ? restWords(skill, restDays) : null,
  ].filter((fact) => fact !== null));
  return skillCard({
    name: skill.name,
    description: skill.description,
    mark: LEARNED_SKILL_MARK,
    facts,
    actions: [versions, wake, pin, share, drop].filter((one) => one !== null),
    ...learnedState(skill),
  });
}

// A chat's run that can give a skill at the owner's word, by what the page knows of it: it ended done, it is no
// delegated run, no eval's, and not OpenCode's agent's nor Claude Code's, whose tools a skill of Dot's cannot name
// (agent/playbooks.py `unsavable`, which also wants an acting step: the server says so of a chat without one).
const EVAL_SOURCE = "eval";
// The runs the dialog looks among, newest first: the most the API gives in one read.
const CHAT_RUNS = 200;

function savable(run) {
  return run.status === "done" && run.parent_run_id === null && run.source !== EVAL_SOURCE && !WHOLE_RUN.has(providerOf(run.model));
}

/**
 * The owner's choice of a finished chat whose work becomes a skill, and its saving: the dialog opens at once and lists
 * the chats once `loading` gives them (item 1005: it opened only after the daemon answered, which a loaded machine took
 * seconds for, so a tap seemed to do nothing); `save(run)` writes the chosen one while the dialog stays open, its rows
 * off and the chosen one saying so; a refusal or a failure is said in the dialog, which stays open for another choice.
 * Resolves to what `save` gave, or null when the owner closed the dialog.
 */
function chatDialog(loading, save) {
  return new Promise((resolve) => {
    let saved = null;
    const dialog = el("dialog", { class: "modal chat-skill-dialog", "aria-labelledby": "chat-skill-title" });
    const problem = el("p", { class: "hint chat-skill-problem", role: "alert", hidden: true });
    const cancel = el("button", { class: "btn", type: "button", onclick: () => dialog.close("cancel") }, t("views.skills.cancel"));
    const list = el("div", { class: "chat-skill-list" }, el("p", { class: "hint" }, t("views.skills.readingYourChats")));
    let rows = [];
    const rowsOf = (runs) => runs.map((run) => {
      const when = el("span", { class: "hint" }, ago(run.finished_at ?? run.created_at));
      const row = el("button", { class: "chat-skill-row", type: "button" }, el("span", { class: "chat-skill-goal" }, clip(run.goal, 120)), when);
      row.addEventListener("click", async () => {
        for (const one of rows) {
          one.disabled = true;
        }
        cancel.disabled = true;
        problem.hidden = true;
        when.textContent = t("views.skills.saving");
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
    loading.then(
      (runs) => {
        rows = rowsOf(runs);
        list.replaceChildren(...(rows.length === 0 ? [el("p", { class: "hint" }, t("views.skills.noChatHasEndedDone"))] : rows));
      },
      (error) => {
        list.replaceChildren();
        problem.textContent = t("views.skills.yourChatsCouldNotBe", { message: error.message });
        problem.hidden = false;
      },
    );
    dialog.append(
      el(
        "div",
        { class: "modal-body" },
        el("h3", { id: "chat-skill-title" }, t("views.skills.saveASkillFromA")),
        el("p", { class: "muted" }, t("views.skills.dotSReviewerWritesHow")),
        list,
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
  // The days after which a learned skill no run read rests ([agent] skill_rest_days), as the list says.
  let restDays = 60;
  // The changes to learned skills that wait for a yes, read apart from the skills so that a failure leaves them on the page.
  let proposals = [];
  // The skills Dot could not offer to a task (item 1405), read apart too: a failure leaves the skills as they were.
  let leftOut = [];
  let agents = [];
  let found = null;
  let foundError = null;
  let shown = "personal";
  let words = "";
  // The sections opened past their first cards.
  const opened = new Set();
  const holder = el("div", { class: "stack" }, loadingPanel());
  const importButton = el("button", { class: "btn sm", type: "button", disabled: true }, icon("download"), t("views.skills.importFromOtherApps"));
  importButton.addEventListener("click", () => attempt(async () => foundChanged(await importFound(found))));
  const saveButton = el("button", { class: "btn sm", type: "button" }, icon("plus"), t("views.skills.saveFromAChat"));
  saveButton.addEventListener("click", () => attempt(saveFromChat));
  const search = el("input", { class: "input customize-search", type: "search", placeholder: t("views.skills.searchSkills"), "aria-label": t("views.skills.searchSkills"), autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const tabs = segmented(
    [
      { id: "personal", label: t("views.skills.personal"), hint: t("views.skills.yourSkillsThoseYouImported") },
      { id: "system", label: t("views.skills.system"), hint: t("views.skills.theSkillsThatComeWith") },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  tabs.element.classList.add("plugin-tabs");
  tabs.element.setAttribute("aria-label", t("views.skills.skillsToShow"));
  // Out of what paints again, so that the search keeps the focus while the cards are drawn anew.
  const head = customizeHead({
    line: t("views.skills.whatDotKnowsHowTo"),
    search,
    controls: [tabs.element, saveButton, importButton],
  });

  // The button can be pressed once what other apps set up is read and something of it can be added.
  function paintImport() {
    const count = found === null ? 0 : found.skills.filter(addable).length + found.servers.filter(addable).length;
    importButton.disabled = count === 0;
    importButton.title =
      found === null
        ? foundError === null
          ? t("views.skills.lookingForWhatOtherApps")
          : t("views.skills.whatOtherAppsSetUp")
        : count === 0
          ? t("views.skills.nothingFoundThatDotCan")
          : t("views.skills.partFoundThatDotCan", { part: tCount(count, "views.skills.countItem", "views.skills.countItems") });
  }

  function foundPart() {
    if (foundError !== null) {
      // Read again on Try again (item 559), as the tabs of Plugins and Connectors offer.
      const retry = () => {
        foundError = null;
        paint();
        loadFound();
      };
      return [foundHead(), errorPanel(foundError, retry)];
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
            t("views.skills.seeValueMore", { value: cards.length - SHOWN_CARDS }),
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
    return askAndChange(goneDialog(skill), { kind: "skill", agent: skill.agent, name: skill.name, added: false }, t("views.skills.removedName", { name: skill.name }));
  }

  function personal() {
    const copies = imported();
    const own = palette.skills.filter((skill) => OWN_SOURCES.has(skill.source) && !copies.has(skill.name) && matches(skill.name, skill.description));
    const ownCards = own.map((skill) =>
      skillCard({ name: skill.name, description: skill.description, mark: skillMark(skill), facts: factsOf([], [skill.source === "home" ? t("views.skills.inDotSSkillsFolder") : t("views.skills.inAFolderInDot")]), actions: [], have: t("views.skills.installed") }),
    );
    const importedCards = [...copies.values()]
      .filter((skill) => matches(skill.name, skill.description))
      .map((skill) => {
        const remove = el("button", { class: "btn ghost sm", type: "button" }, t("views.skills.remove"));
        remove.addEventListener("click", () => attempt(async () => foundChanged(await removeImported(skill))));
        const from = skill.gone ? t("views.skills.importedFromAgentWhichNo", { agent: skill.agent }) : t("views.skills.importedFromAgent", { agent: skill.agent });
        return skillCard({ name: skill.name, description: skill.description, mark: IMPORTED_SKILL_MARK, facts: factsOf([], [from]), actions: [remove], have: t("views.skills.installed") });
      });
    // Awake first, then those that rest, then the retired ones; each in the order the list gave.
    const rank = (skill) => (skill.retired ? 2 : skill.resting ? 1 : 0);
    const named = withAgentNames(items, agents)
      .filter((skill) => matches(skill.name, skill.description))
      .sort((one, other) => rank(one) - rank(other));
    const learnedCards = named.map((skill) => learnedCard(skill, changed, restDays));
    const waiting = proposals
      .filter((view) => matches(view.proposal.names.join(" "), view.proposal.reason))
      .map((view) =>
        proposalCard(view, {
          onAccepted: (list) => {
            changed(list);
            attempt(loadProposals);
          },
          onDismissed: (after) => {
            proposals = after.items;
            paint();
          },
        }),
      );
    const parts = [
      section("proposals", t("views.skills.waitingForYourYes"), waiting),
      section("own", t("views.skills.yours"), ownCards),
      section("imported", t("views.skills.imported"), importedCards),
      section("learned", t("views.skills.learned"), learnedCards),
    ];
    if (parts.every((part) => part === null)) {
      parts.push(
        words === ""
          ? emptyPanel("sparkles", t("views.skills.noSkillsOfYoursYet"), t("views.skills.afterATaskThatWent"))
          : emptyPanel("search", t("views.skills.noSkillMatchesTheSearch"), ""),
      );
    }
    return [...parts, ...leftOutPart("personal"), ...foundPart()];
  }

  /** The skills Dot could not offer that belong on this tab (item 1405), the search applied: a plugin's and Dot's own on
   * System, the others on Personal. */
  function leftOutPart(tab) {
    const here = leftOut.filter((entry) => leftOutIsSystem(entry.source) === (tab === "system") && matches(leftOutName(entry.path), `${entry.reason} ${entry.path}`));
    return leftOutSection(here, tab);
  }

  function system() {
    const builtin = palette.skills.filter((skill) => skill.source === BUILTIN_SOURCE && matches(skill.name, skill.description));
    const fromPlugins = palette.skills.filter((skill) => skill.source.startsWith(PLUGIN_SOURCE) && matches(skill.name, skill.description));
    const parts = [
      section(
        "builtin",
        t("views.skills.comesWithDot"),
        builtin.map((skill) => skillCard({ name: skill.name, description: skill.description, mark: skillMark(skill), facts: factsOf([], [t("views.skills.comesWithDot")]), actions: [], have: t("views.skills.installed") })),
      ),
      section(
        "plugins",
        t("views.skills.fromPlugins"),
        fromPlugins.map((skill) =>
          skillCard({ name: skill.name, description: skill.description, mark: skillMark(skill), facts: factsOf([el("span", { class: "chip" }, skill.source.slice(PLUGIN_SOURCE.length))], []), actions: [], have: t("views.skills.installed") }),
        ),
      ),
    ];
    if (parts.every((part) => part === null)) {
      parts.push(
        words === ""
          ? emptyPanel("sparkles", t("views.skills.neitherDotNorAPlugin"), t("views.skills.dotSBuiltInSkills"))
          : emptyPanel("search", t("views.skills.noSkillMatchesTheSearch"), ""),
      );
    }
    return [...parts, ...leftOutPart("system")];
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
    personalButton.textContent = t("views.skills.personalPersonalcount", { personalCount });
    systemButton.textContent = t("views.skills.systemSystemcount", { systemCount });
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
    const loading = api.get(`/runs${query({ limit: CHAT_RUNS })}`).then((runs) => runs.filter(savable));
    const saved = await chatDialog(loading, (run) => api.post(`/runs/${encodeURIComponent(run.id)}/skill`, {}));
    if (saved === null) {
      return;
    }
    toast("ok", saved.updated ? t("views.skills.updatedTheSkillName", { name: saved.name }) : t("views.skills.savedTheSkillName", { name: saved.name }));
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
      restDays = page.rest_days;
      agents = rows;
      palette = offered;
      paint();
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(
          errorPanel(error, () => {
            holder.replaceChildren(loadingPanel());
            attempt(load);
          }),
        );
      }
    }
  }

  // Read apart from the skills, so that a failure here leaves them on the page: what the page shows without them is the
  // skills as they were.
  async function loadProposals() {
    try {
      proposals = (await api.get("/skills/proposals")).items;
    } catch (error) {
      console.error("the proposals to change skills could not be read", error);
    }
    if (!disposed) {
      paint();
    }
  }

  // Read apart from the skills, so that a failure here leaves them on the page.
  async function loadLeftOut() {
    try {
      leftOut = (await api.get("/skills/left-out")).items;
    } catch (error) {
      console.error("the skills Dot left out could not be read", error);
    }
    if (!disposed) {
      paint();
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

  root.append(head.element, holder);
  load();
  loadProposals();
  loadLeftOut();
  loadFound();
  return () => {
    disposed = true;
  };
}
