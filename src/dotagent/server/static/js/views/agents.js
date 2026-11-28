// Agents (item 86): the profiles the owner makes, each with a face in its own hue, a name, a role in one line,
// instructions that open the prompt of its runs, and bounds for them (a model, a reasoning level, approvals, tools).
// The list (#/agents) is in parts, one shown at a time (item 1136): Dot and the agents as a grid of compact cards, each
// with its newest chat, and under them the ready teams (item 504) and the templates (item 315) agents start from in one
// click; the owner's teams (item 610), each a card with its agents' faces; and the work orders. An
// agent's page (#/agents/<id>) greets with its face and role, takes a message for it, and lists its schedules and what it
// learned (item 1266: the counts of its chats, actions, orders and messages left the page); the editor makes or changes
// an agent. An agent goes out as a file from its page and comes back, in this home or another, from Import an
// agent under the list's "…". A team's own page is views/teams.js.

import { actorFace, dotFace, fallbackLook, liveFace, rememberLook, teamFaces } from "../actors.js";
import { agentHandoffs, agentObjectives, agentOrders, orderOpen } from "../botwork.js";
import { emptyBoxWords } from "../composershape.js";
import { sendToWork } from "../sendwork.js";
import { AGENTS_CHANGED_EVENT, api } from "../api.js";
import { messageText, readyToSend } from "../attachments.js";
import { AUTO_MODE, MESSAGE_MODES, runChoices } from "../choices.js";
import { composerFrame } from "../composer.js";
import { ago, clip, el, modelName, setText, switchRow } from "../dom.js";
import { allModels, chosenLevel, KEPT_SPEED, levelLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { touchFirst, trackHeight } from "../layout.js";
import { openMenu } from "../menu.js";
import { readFolds, readPref, writePref } from "../prefs.js";
import { lookPicker } from "../lookpicker.js";
import { modelSections } from "../modelpicker.js";
import { aboutToggle, chatLoading, errorPanel, field, loadingPanel, pageHead, segmented } from "../panels.js";
import { APPROVAL_OPTIONS, heldChoice, KEPT_GOAL_MODE } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { setStageGlass, setStageTitle, setStageTools } from "../stage.js";
import { scoreLine } from "./memory.js";
import { skillRow } from "./skills.js";
import { getSnapshot, loadModels, onChange, onLifecycle, onStreamBack } from "../state.js";
import { teamForm } from "../teamform.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";
import { workOrdersPart } from "../workorders.js";
import { WORKING_STATES } from "../runstates.js";
import { t, tCount, listAnd } from "../i18n.js";

const DOT_ID = "dot";
const GOAL_CHARS = 90;
const MAX_GOAL_PX = 320;
// How many of a team's agents a card shows the face of, the rest counted (item 610).
const MOST_TEAM_FACES = 4;
// The face an agent wears in its row: working while its newest run works, at rest otherwise.
const WORKING = WORKING_STATES;
// The largest agent file Import reads: its instructions take at most 50,000 characters, of up to four bytes each.
const MOST_AGENT_FILE_BYTES = 256 * 1024;
// How often the cards' "5m ago" are written again while the list stays open (item 633).
const AGE_REFRESH_MS = 60_000;
// Names in one line of words, as a toast says them: "A", "A and B", "A, B, and C".
const AND_LIST = { format: listAnd };

function faceOf(record, latest) {
  const state = latest !== null && WORKING.has(latest.status) ? "working" : "idle";
  return record.id === DOT_ID ? dotFace(state) : actorFace(record.id, state, record.look);
}

function hrefOf(record) {
  return record.id === DOT_ID ? "#/runs" : `#/agents/${record.id}`;
}

/**
 * An agent's compact card in the grid of Your agents (item 610): its face, its name and its role on one line, and under
 * them the title of its newest chat with when it began, or that it has none. Dot's names its newest chat of its own
 * (item 633) and opens the chats.
 */
function agentCard({ record, latest, latest_title: latestTitle }, picking = null) {
  // The chat's own title, which the owner may have named; a daemon from before it was sent says the run's goal.
  const chat = latest === null ? t("views.agents.noChatsYet") : clip(latestTitle ?? latest.goal, GOAL_CHARS);
  // While the owner picks agents for a team (item 867), a card of theirs is a box to tick, not a way to its page.
  const pickable = picking !== null && record.id !== DOT_ID;
  const card = el(
    "a",
    {
      class: pickable ? "card agent-card pickable" : "card agent-card",
      href: hrefOf(record),
      dataset: { agent: record.id },
      ...(pickable ? { role: "checkbox", "aria-checked": String(picking.has(record.id)) } : {}),
    },
    pickable ? el("span", { class: "agent-pick-mark", "aria-hidden": "true" }, icon("check")) : null,
    faceOf(record, latest),
    el(
      "div",
      { class: "agent-card-main" },
      el(
        "div",
        { class: "agent-card-name" },
        el("span", { class: "agent-card-title" }, record.name),
        record.made_by === null ? null : el("span", { class: "agent-made" }, t("views.agents.madeByDot")),
        // A paused agent (item 465) says so in the list, as on its page.
        record.paused_at === null ? null : el("span", { class: "badge b-warn", title: t("views.agents.itsSchedulesStartNothingBy") }, t("views.agents.paused")),
      ),
      el("div", { class: "agent-card-role", title: record.role }, record.role === "" ? t("views.agents.noRoleYet") : record.role),
    ),
    el(
      "div",
      { class: "agent-card-chat" },
      el("span", { class: "agent-card-chat-title muted", title: latest === null ? "" : chat }, chat),
      latest === null ? null : el("span", { class: "agent-when", dataset: { ts: String(latest.created_at) } }, ago(latest.created_at)),
    ),
  );
  if (pickable) {
    const toggle = (event) => {
      event.preventDefault();
      picking.toggle(record.id);
      card.setAttribute("aria-checked", String(picking.has(record.id)));
    };
    card.addEventListener("click", toggle);
    card.addEventListener("keydown", (event) => {
      if (event.key === " ") {
        toggle(event);
      }
    });
  }
  return card;
}

/**
 * A team's card in Your teams (item 610): its agents' faces over one another (four, the rest counted), its name and what
 * it is for, how many agents it has and its newest chat with when it was last busy. The whole card opens the team.
 */
function teamTile({ record, members, latest }) {
  for (const member of members) {
    rememberLook(member.id, member.look);
  }
  const working = latest !== null && WORKING.has(latest.latest_status);
  const names = members.map((member) => member.name);
  return el(
    "a",
    { class: "card owned-team", href: `#/teams/${record.id}`, dataset: { ownedTeam: record.id } },
    el(
      "div",
      { class: "owned-team-head" },
      teamFaces(record, working ? "working" : "idle", MOST_TEAM_FACES),
      el("div", { class: "owned-team-main" }, el("div", { class: "owned-team-name" }, record.name), el("div", { class: "owned-team-purpose" }, record.purpose === "" ? t("views.agents.noPurposeWrittenYet") : record.purpose)),
    ),
    el(
      "div",
      { class: "owned-team-count muted", title: names.join(", ") },
      members.length === 0 ? t("views.agents.noAgentsLeft") : tCount(members.length, "views.agents.countAgent", "views.agents.countAgents"),
    ),
    el(
      "div",
      { class: "agent-card-chat" },
      el("span", { class: "agent-card-chat-title muted", title: latest === null ? "" : latest.title }, latest === null ? t("views.agents.noChatsYet") : clip(latest.title, GOAL_CHARS)),
      latest === null ? null : el("span", { class: "agent-when", dataset: { ts: String(latest.updated_at) } }, ago(latest.updated_at)),
    ),
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
    el("summary", null, t("views.agents.actionsLength", { length: template.tools.length })),
    el(
      "div",
      { class: "template-tool-chips" },
      template.tools.map((name) =>
        el(
          "code",
          known !== null && !known.has(name)
            ? { class: "chip missing", title: t("views.agents.thisMacHasNoSuch") }
            : { class: "chip" },
          name,
        ),
      ),
    ),
  );
}

/** What a template's agent never does (item 875), a click away as its actions are: the edges of its job, which
    tell what is safe to leave it running for. */
function templateRefuses(template) {
  if (template.refuses.length === 0) {
    return null;
  }
  return el(
    "details",
    { class: "template-refuses" },
    el("summary", null, t("views.agents.neverDoesLength", { length: template.refuses.length })),
    el(
      "ul",
      { class: "template-refuse-list" },
      template.refuses.map((line) => el("li", null, line)),
    ),
  );
}

/**
 * The schedules its template suggests, for each of `pairs` (`{ agent, template }`) whose template has one, switched off
 * until the owner has read the goal (item 464), unless the agent has that schedule already (its agent and its goal):
 * an agent the owner had before, which Use team keeps, gets its schedule too, and a second Use adds none twice (item
 * 722). Returns the names of the agents given a schedule now, of those that had theirs, and why one was not added.
 */
async function addSuggestedSchedules(pairs) {
  const suggesting = pairs.filter(({ template }) => template.cron !== "");
  const added = [];
  const had = [];
  const missed = [];
  if (suggesting.length === 0) {
    return { added, had, missed };
  }
  let schedules;
  try {
    schedules = await api.get("/schedules");
  } catch (error) {
    // The agents are made all the same: the toast says that their schedules were not added, and why.
    const why = error.message.replace(/\.$/, "");
    return { added, had, missed: suggesting.map(({ agent }) => `${agent.name}: ${why}`) };
  }
  for (const { agent, template } of suggesting) {
    if (schedules.some((view) => view.record.options.agent_id === agent.id && view.record.goal === template.task)) {
      had.push(agent.name);
      continue;
    }
    try {
      await api.post("/schedules", { name: agent.name, goal: template.task, cron: template.cron, agent_id: agent.id, enabled: false });
      added.push(agent.name);
    } catch (error) {
      // The toast ends the list with its own full stop, so a message's own is taken off.
      missed.push(`${agent.name}: ${error.message.replace(/\.$/, "")}`);
    }
  }
  return { added, had, missed };
}

/** A template's card (item 315): its face, name and role, what it needs of the Mac, the schedule it suggests and the
    tools its agent may use. One that suggests a schedule offers to add it with the agent (item 464), switched off until
    the owner has read its goal; `onUse(template, alsoSchedule)` makes them. */
function templateCard(template, onUse, known) {
  // The app's own switch, which a finger finds on a phone (item 488), as every other on and off of the pages.
  const alsoWords = t("views.agents.alsoAddThisScheduleSwitched");
  const also = template.cron === "" ? null : el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": alsoWords });
  also?.addEventListener("click", () => also.setAttribute("aria-checked", String(also.getAttribute("aria-checked") !== "true")));
  const alsoRow = also === null ? null : switchRow(el("div", { class: "template-also" }, also, el("span", null, t("views.agents.alsoAddItsScheduleYou"))), also);
  const use = el(
    "button",
    { class: "btn sm template-use", type: "button", "aria-label": t("views.agents.useTheTemplateName", { name: template.name }) },
    icon("plus"),
    el("span", { class: "use-long" }, t("views.agents.useTemplate")),
    el("span", { class: "use-short" }, t("views.agents.use")),
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
      templateFact(t("views.agents.needs"), "plug", template.needs),
      templateFact(t("views.agents.schedule"), "clock", template.schedule === "" ? t("views.agents.whenYouAsk") : template.schedule),
    ),
    alsoRow,
    templateTools(template, known),
    templateRefuses(template),
    use,
  );
}

/** A team's card (item 504): its name and what it does, the faces and names of its agents, those the owner has
    already marked Yours (`owned`, their names in lower case, item 512), and Use team, which makes the ones the owner
    lacks and puts the team under Your teams (item 610; `added` says it is there already, and Use then brings back an
    agent it lacks). A team one of whose agents suggests a schedule offers to add the schedules of the agents it makes,
    switched off as a template's (item 464); `onUse(team, members, alsoSchedules)` makes them. */
function teamCard(team, templates, onUse, owned, added) {
  const members = team.members.map((id) => templates.get(id));
  const alsoWords = t("views.agents.alsoAddTheirSchedulesSwitched");
  const also = members.some((template) => template.cron !== "")
    ? el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": alsoWords })
    : null;
  also?.addEventListener("click", () => also.setAttribute("aria-checked", String(also.getAttribute("aria-checked") !== "true")));
  const alsoRow = also === null ? null : switchRow(el("div", { class: "template-also" }, also, el("span", null, t("views.agents.alsoAddTheirSchedulesYou"))), also);
  const use = el(
    "button",
    { class: "btn sm template-use", type: "button", "aria-label": t("views.agents.useTheTeamName", { name: team.name }) },
    icon("plus"),
    el("span", { class: "use-long" }, t("views.agents.useTeam")),
    el("span", { class: "use-short" }, t("views.agents.use")),
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
      added ? el("span", { class: "badge b-muted team-added", title: t("views.agents.thisTeamIsUnderYour") }, t("views.agents.inYourTeams")) : null,
    ),
    el(
      "ul",
      { class: "team-members", "aria-label": t("views.agents.theAgentsOfTheName", { name: team.name }) },
      members.map((template) =>
        el(
          "li",
          null,
          actorFace(`template-${template.id}`, "idle", template.look),
          el("span", null, template.name),
          owned.has(template.name.trim().toLowerCase()) ? el("span", { class: "badge b-muted team-yours", title: t("views.agents.youHaveThisAgentUse") }, t("views.agents.yours")) : null,
        ),
      ),
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
  toast("ok", t("views.agents.nameIsSavedAsName2", { name: record.name, name2: name }));
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
  toast("ok", t("views.agents.nameIsImported", { name: saved.name }));
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

/** The checkboxes of the tools under one that ticks them all (item 512): an agent with every tool (`chosen` empty)
    opens with every box ticked, and every box ticked saves as every tool (`widens`), unless the boxes are the tools the
    agent names, unchanged, and All tools was not ticked: those stay named, so a save that changed nothing never lifts
    its bounds, and ticking All tools always does. */
function toolPicks(names, chosen) {
  const picked = new Set(chosen.length === 0 ? names : chosen);
  const boxes = names.map((name) => el("input", { type: "checkbox", value: name, checked: picked.has(name) }));
  const all = el("input", { type: "checkbox", "aria-label": t("views.agents.allActions") });
  const count = el("span", { class: "muted tool-picks-count" });
  const named = new Set(chosen);
  // Whether the owner ticked All tools since a box was last unticked: every tool, whatever the agent named.
  let allTicked = false;
  function widens() {
    const ticked = boxes.filter((box) => box.checked);
    return ticked.length === boxes.length && (named.size === 0 || allTicked || ticked.some((box) => !named.has(box.value)));
  }
  function paint() {
    const ticked = boxes.filter((box) => box.checked).length;
    all.checked = ticked === boxes.length;
    all.indeterminate = ticked > 0 && ticked < boxes.length;
    count.textContent = widens() ? t("views.agents.everyActionAndAnyAdded") : t("views.agents.tickedOfLength", { ticked, length: boxes.length });
  }
  all.addEventListener("change", () => {
    allTicked = all.checked;
    for (const box of boxes) {
      box.checked = all.checked;
    }
    paint();
  });
  for (const box of boxes) {
    box.addEventListener("change", () => {
      allTicked = allTicked && box.checked;
      paint();
    });
  }
  paint();
  const element = el(
    "div",
    { class: "stack tight" },
    el("label", { class: "tool-pick tool-pick-all" }, all, el("span", null, t("views.agents.allActions")), count),
    el(
      "div",
      { class: "tool-picks" },
      boxes.map((box) => el("label", { class: "tool-pick" }, box, el("span", null, box.value))),
    ),
  );
  return { element, widens, picked: () => boxes.filter((box) => box.checked).map((box) => box.value) };
}

/**
 * The form that makes an agent (`record` null) or changes one. `onSaved(record)` runs with the saved agent; `onCancel`
 * closes the form.
 */
export function agentForm({ record, onSaved, onCancel }) {
  const defaults = getSnapshot().status;
  const name = el("input", { class: "input", type: "text", maxlength: "60", autocomplete: "off", placeholder: t("views.agents.scout"), "aria-label": t("views.agents.name") });
  const role = el("input", { class: "input", type: "text", maxlength: "120", autocomplete: "off", placeholder: t("views.agents.findsAndChecksSources"), "aria-label": t("views.agents.role") });
  const instructions = el("textarea", {
    class: "textarea",
    rows: "5",
    placeholder: t("views.agents.howThisAgentWorksAnd"),
    "aria-label": t("views.agents.instructions"),
  });
  // What it never does, a line each (item 875): the edges of its job, which its runs hold to whoever asks.
  const refuses = el("textarea", {
    class: "textarea",
    rows: "3",
    maxlength: "2000",
    placeholder: t("views.agents.workInboundLeadsSendA"),
    "aria-label": t("views.agents.neverDoes"),
  });
  // The model, on the chat settings' list of models (the owner's report of 2026-11-28): the main model's row on top,
  // the favourites, a section per provider that folds (the chosen model's open), a tick on the one chosen and a search;
  // it writes the agent's own choice only, never which model a run takes.
  let modelValue = record === null ? "" : record.model;
  /** @type {{ provider: string, models: { ref: string, tools: boolean }[], why: null }[]} */
  let modelGroups = [];
  // "Main model", as the iPhone app's agent page says it, with the model it stands for once it is known.
  const mainWords = defaults === null ? t("views.agents.mainModel") : t("views.agents.mainModelname", { modelName: modelName(defaults.models.main) });
  const modelList = modelSections({
    onPick: (ref) => {
      modelValue = ref;
      paintModels();
      paintEffort();
    },
    // The agent's page makes no default: its main model's row has no Change.
    onMakeDefault: () => false,
  });
  const notOffered = el("p", { class: "hint", hidden: true });
  const model = el("div", { class: "agent-model-picker", role: "group", "aria-label": t("views.agents.model") });
  function paintModels() {
    const built = modelList.build({ groups: modelGroups, chosen: modelValue, fallback: null, blank: mainWords });
    const known = modelGroups.some((group) => group.models.some((candidate) => candidate.ref === modelValue));
    notOffered.hidden = modelValue === "" || known || modelGroups.length === 0;
    notOffered.textContent = t("views.agents.chosenNotOfferedNow", { chosen: modelValue });
    model.replaceChildren(built.search, notOffered, built.list);
  }
  paintModels();
  const effort = el("select", { class: "select", "aria-label": t("views.agents.reasoningLevel") }, el("option", { value: "" }, t("views.agents.default")));
  const effortField = field(t("views.agents.reasoning"), effort, "");
  effortField.hidden = true;
  const approvals = select(
    t("views.agents.approvals"),
    [{ id: "", label: t("views.agents.default") }, ...APPROVAL_OPTIONS.map((option) => ({ id: option.id, label: `${option.label}: ${option.hint}` }))],
    record === null || record.approvals === null ? "" : record.approvals,
  );
  // The look (item 102): the agent's own, or for a new one the fallback of a face not yet named.
  const look = lookPicker({ look: record === null || record.look === null ? fallbackLook(record === null ? "new" : record.id) : record.look, uid: record === null ? "new" : record.id });
  const lookField = field(t("views.agents.look"), look.element, t("views.agents.theFaceThisAgentWears"));
  lookField.classList.add("wide");
  const tools = el("div", null, el("span", { class: "muted" }, t("views.agents.loadingTheActions")));
  const toolsField = field(t("views.agents.actions"), tools, t("views.agents.allActionsEveryActionAnd"));
  toolsField.classList.add("wide");
  let models = [];

  function paintEffort() {
    const info = models.find((candidate) => candidate.ref === modelValue) ?? null;
    const keep = effort.value === "" ? (record === null ? "" : record.effort ?? "") : effort.value;
    if (info === null || info.efforts.length < 2) {
      effort.replaceChildren(el("option", { value: "" }, t("views.agents.default")));
      effort.value = "";
      effortField.hidden = true;
      return;
    }
    effort.replaceChildren(
      el("option", { value: "" }, info.default_effort === null ? t("views.agents.default") : t("views.agents.defaultLevellabel", { levelLabel: levelLabel(info.default_effort) })),
      ...info.efforts.map((level) => el("option", { value: level }, levelLabel(level))),
    );
    effort.value = info.efforts.includes(keep) ? keep : "";
    effortField.hidden = false;
  }

  const modelsListed = loadModels()
    .then((providers) => {
      models = allModels(providers).filter((candidate) => candidate.tools);
      modelGroups = providers
        .map((provider) => ({ provider: provider.provider, models: provider.models.filter((candidate) => candidate.tools), why: null }))
        .filter((group) => group.models.length > 0);
      paintModels();
      paintEffort();
    })
    .catch((error) => {
      console.warn("the models could not be listed", error);
    });
  // Saved only once the tools are listed: without them the form cannot say which tools the agent may use.
  let toolsListed = false;
  let picks = null;
  const toolsLoaded = api
    .get("/tools")
    .then((names) => {
      picks = toolPicks(names, record === null ? [] : record.tools);
      tools.replaceChildren(picks.element);
      toolsListed = true;
    })
    .catch((error) => {
      tools.replaceChildren(el("span", { class: "muted" }, t("views.agents.theActionsCouldNotBe", { message: error.message })));
    });
  if (record !== null) {
    name.value = record.name;
    role.value = record.role;
    instructions.value = record.instructions;
    refuses.value = record.refuses ?? "";
  }
  const save = el("button", { class: "btn primary", type: "button", disabled: true }, icon(record === null ? "plus" : "check"), record === null ? t("views.agents.createAgent") : t("views.agents.saveChanges"));
  Promise.allSettled([modelsListed, toolsLoaded]).then(() => {
    save.disabled = !toolsListed;
  });
  const cancel = el("button", { class: "btn ghost", type: "button", onclick: onCancel }, t("views.agents.cancel"));

  async function submit() {
    const nameText = name.value.trim();
    if (nameText === "") {
      name.focus();
      return;
    }
    const picked = picks.picked();
    if (picked.length === 0) {
      toast("error", t("views.agents.pickTheActionsItMay"));
      tools.querySelector(".tool-pick-all input")?.focus();
      return;
    }
    const body = {
      name: nameText,
      role: role.value.trim(),
      instructions: instructions.value.trim(),
      refuses: refuses.value.trim(),
      model: modelValue,
      effort: chosenLevel(models, modelValue, effort.value, record === null ? "" : record.model, record === null ? null : record.effort),
      approvals: approvals.value === "" ? null : approvals.value,
      // Every tool, said as null, when the boxes widen the agent (toolPicks): every box ticked, unless they are the tools
      // it names, unchanged, and All tools was not ticked. An empty list keeps the tools an agent has (the code review's
      // Y5).
      tools: picks.widens() ? null : picked,
      look: look.value(),
    };
    save.disabled = true;
    try {
      const saved = record === null ? await api.post("/agents", body) : await api.put(`/agents/${record.id}`, body);
      rememberLook(saved.id, saved.look);
      toast("ok", record === null ? t("views.agents.nameIsReady", { name: saved.name }) : t("views.agents.agentSaved"));
      onSaved(saved);
    } finally {
      save.disabled = false;
    }
  }

  save.addEventListener("click", () => attempt(submit));
  const element = el(
    "section",
    { class: "card agent-editor", "aria-label": record === null ? t("views.agents.newAgent") : t("views.agents.editAgent") },
    el("div", { class: "card-head" }, el("span", null, record === null ? t("views.agents.newAgent") : t("views.agents.editName", { name: record.name }))),
    el(
      "div",
      { class: "card-pad stack" },
      el(
        "div",
        { class: "form-grid" },
        field(t("views.agents.name"), name, ""),
        field(t("views.agents.role"), role, t("views.agents.oneLineAsItShows")),
        Object.assign(field(t("views.agents.instructions"), instructions, ""), { className: "field wide" }),
        Object.assign(field(t("views.agents.neverDoes"), refuses, t("views.agents.oneLineEachTheEdges")), { className: "field wide" }),
        lookField,
        Object.assign(field(t("views.agents.model"), model, ""), { className: "field wide" }),
        effortField,
        field(t("views.agents.approvals"), approvals, ""),
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

/** The language the owner reads, named in English for the agent ("Turkish"): the browser's own. */
function ownerLanguage() {
  const code = (navigator.language || "en").split("-")[0];
  return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
}

/**
 * Start the first meeting of the agent `record` just made, and open its chat (item 993). The agent is made whatever the
 * meeting comes to: one that could not start says why and opens the agent's page, so that the form is not pressed
 * again for a second agent of the same name (the pre-push review).
 */
async function meet(record, ctx) {
  try {
    const run = await api.post(`/agents/${encodeURIComponent(record.id)}/welcome`, { language: ownerLanguage() });
    ctx.navigate(`/runs/${run.id}`);
  } catch (error) {
    ctx.navigate(`/agents/${record.id}`);
    throw error;
  }
}

function mountList(root, ctx) {
  let disposed = false;
  let ticket = 0;
  let teamsTicket = 0;
  // The agents the owner has, for the editor of a team, as the list read them last.
  let owners = [];
  const holder = el("div", null, loadingPanel());
  const teamsHolder = el("div", null, loadingPanel());
  const editorSlot = el("div", null);
  // The page in parts (items 509 and 610): the teams the owner has, the agents the owner has, then the ready-made ones
  // to add, a team or one agent at a time, one of the two shown.
  // What each part is for stands behind an (i) beside its name (item 848).
  const teamsCount = el("span", { class: "agents-count" });
  const teamsAbout = aboutToggle(t("views.agents.openATeamToGive"), t("views.agents.aboutYourTeams"));
  const yourTeams = el(
    "section",
    { class: "agents-part", "aria-label": t("views.agents.yourTeams") },
    el("div", { class: "agents-part-head" }, el("h2", { class: "section-label" }, t("views.agents.yourTeams"), teamsCount), teamsAbout.button),
    teamsAbout.note,
    teamsHolder,
  );
  const mineCount = el("span", { class: "agents-count" });
  // Picking agents for a team on their cards (item 867): Select ticks them, the bar under the grid makes the team.
  let summariesShown = [];
  let picking = null;
  const pickedCount = el("span", { class: "agent-select-count" });
  const makeTeamButton = el("button", { class: "btn primary", type: "button", onclick: () => attempt(() => openTeamEditor([...(picking?.ids ?? [])])) }, icon("users"), t("views.agents.makeATeam"));
  const selectBar = el(
    "div",
    { class: "agent-select-bar", hidden: true, role: "region", "aria-label": t("views.agents.agentsPickedForATeam") },
    pickedCount,
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost", type: "button", onclick: () => setPicking(false) }, t("views.agents.cancel")),
    makeTeamButton,
  );
  // Hidden until the agents are read: a list that cannot be read offers nothing to pick.
  const selectButton = el("button", { class: "btn ghost sm", type: "button", hidden: true, title: t("views.agents.pickAgentsForATeam"), onclick: () => setPicking(true) }, t("views.agents.select"));

  function paintPicked() {
    const count = picking === null ? 0 : picking.ids.length;
    pickedCount.textContent = count === 0 ? t("views.agents.tickTheAgentsOfThe") : t("views.agents.partPicked", { part: tCount(count, "views.agents.countAgent", "views.agents.countAgents") });
    makeTeamButton.disabled = count === 0;
  }

  function drawGrid() {
    holder.replaceChildren(el("div", { class: picking === null ? "agent-grid" : "agent-grid picking" }, summariesShown.map((summary) => agentCard(summary, picking))));
  }

  /** Start picking agents on their cards, or stop. */
  function setPicking(on) {
    picking = on
      ? {
          ids: [],
          has(id) {
            return this.ids.includes(id);
          },
          toggle(id) {
            this.ids = this.has(id) ? this.ids.filter((each) => each !== id) : [...this.ids, id];
            paintPicked();
          },
        }
      : null;
    selectBar.hidden = !on;
    selectButton.hidden = on || owners.length < 2;
    paintPicked();
    drawGrid();
  }
  const mineAbout = aboutToggle(t("views.agents.openOneToGiveIt"), t("views.agents.aboutYourAgents"));
  const yours = el(
    "section",
    { class: "agents-part", "aria-label": t("views.agents.yourAgents") },
    el("div", { class: "agents-part-head" }, el("h2", { class: "section-label" }, t("views.agents.yourAgents"), mineCount), mineAbout.button, el("span", { class: "spacer" }), selectButton),
    mineAbout.note,
    holder,
    selectBar,
  );
  const templates = el("section", { class: "agents-part", "aria-label": t("views.agents.templates") }, loadingPanel());

  async function loadTemplates() {
    try {
      // The tools this home has, so that a card marks one it lacks. A home that cannot list them still shows the
      // templates, their tools unmarked, and says above them that it could not tell which are missing.
      const [listed, teams, named, summaries, mine] = await Promise.all([
        api.get("/agent-templates"),
        api.get("/agent-teams"),
        api.get("/tools").then(
          (names) => ({ names, problem: null }),
          (error) => ({ names: null, problem: error }),
        ),
        // Only the marks of a team's card need them: a list that failed leaves the cards unmarked, and says so above them.
        api.get("/agents").then(
          (records) => ({ records, problem: null }),
          (error) => ({ records: [], problem: error }),
        ),
        // The ready teams the owner has under Your teams (item 610), for the same cards; a list that failed is told in
        // Your teams, which reads it too, and leaves these cards unmarked.
        api.get("/teams").then(
          (records) => new Set(records.map(({ record }) => record.ready_team)),
          (error) => {
            console.warn("the teams could not be listed", error);
            return new Set();
          },
        ),
      ]);
      // The agents the owner has, by name as Use team reads them, for the marks of a team's card.
      const owned = new Set(summaries.records.map(({ record }) => record.name.trim().toLowerCase()));
      const known = named.names === null ? null : new Set(named.names);
      const listingProblem = named.problem;
      const byId = new Map(listed.map((template) => [template.id, template]));
      if (!disposed) {
        // `replaceChildren` draws a null as the word "null": the cards' note is a child only when there is one. What each
        // pane is for stands behind the (i) of the head, the shown pane's (item 848).
        const abouts = {
          teams: aboutToggle(
            t("views.agents.aFewAgentsThatWork"),
            t("views.agents.aboutTheReadyMadeTeams"),
          ),
          single: aboutToggle(t("views.agents.oneAgentOfYoursTo"), t("views.agents.aboutTheReadyMadeAgents")),
        };
        const panes = {
          teams: el(
            "div",
            { class: "stack tight", dataset: { pane: "teams" } },
            abouts.teams.note,
            ...(summaries.problem === null
              ? []
              : [
                  el(
                    "p",
                    { class: "hint", dataset: { teamsAgentsUnknown: "" } },
                    t("views.agents.yourAgentsCouldNotBe", { message: summaries.problem.message }),
                  ),
                ]),
            el("div", { class: "template-grid" }, teams.map((team) => teamCard(team, byId, useTeam, owned, mine.has(team.id)))),
          ),
          single: el(
            "div",
            { class: "stack tight", dataset: { pane: "single" } },
            abouts.single.note,
            ...(listingProblem === null
              ? []
              : [
                  el(
                    "p",
                    { class: "hint", dataset: { templatesToolsUnknown: "" } },
                    t("views.agents.theActionsCouldNotBe2", { message: listingProblem.message }),
                  ),
                ]),
            el("div", { class: "template-grid" }, listed.map((template) => templateCard(template, useTemplate, known))),
          ),
        };
        const choices = [
          { id: "teams", label: t("views.agents.teamsLength", { length: teams.length }) },
          { id: "single", label: t("views.agents.singleAgentsLength", { length: listed.length }) },
        ];
        const buttons = choices.map((choice) => el("button", { type: "button", dataset: { pane: choice.id }, onclick: () => show(choice.id) }, choice.label));
        const show = (id) => {
          shownPane = id;
          for (const button of buttons) {
            button.setAttribute("aria-pressed", String(button.dataset.pane === id));
          }
          for (const [key, pane] of Object.entries(panes)) {
            pane.hidden = key !== id;
            abouts[key].button.hidden = key !== id;
          }
        };
        show(shownPane);
        templates.replaceChildren(
          el(
            "div",
            { class: "agents-part-head" },
            el("h2", { class: "section-label" }, t("views.agents.addReadyMadeAgents")),
            abouts.teams.button,
            abouts.single.button,
            el("div", { class: "segmented", role: "group", "aria-label": t("views.agents.readyMadeAgents") }, buttons),
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

  /**
   * What the owner answers a template's questions (item 876), three at most, before its agent is made: they go into
   * its instructions, so that its first run gives a result instead of asking. Skip makes it with none; null when the
   * dialog was closed, and nothing is made.
   */
  async function setupAnswers(template) {
    const inputs = template.questions.map((question) => el("input", { class: "input", type: "text", maxlength: "500", autocomplete: "off", "aria-label": question }));
    let answers = null;
    const { closed } = formDialog({
      title: t("views.agents.setUpName", { name: template.name }),
      description: t("views.agents.whatItNeedsToKnow"),
      body: template.questions.map((question, index) => field(question, inputs[index], "")),
      submitLabel: t("views.agents.useTemplate"),
      alternative: {
        label: t("views.agents.skip"),
        onSubmit: async () => {
          answers = [];
          return true;
        },
      },
      onSubmit: async () => {
        answers = inputs.map((input) => input.value.trim());
        return true;
      },
    });
    return (await closed) ? answers : null;
  }

  // The agent, then its schedule switched off when it was asked for (item 464): the agent's page lists it with its goal
  // to read and its switch. A schedule that could not be added leaves the agent made, and the toast says why.
  async function useTemplate(template, alsoSchedule) {
    const answers = template.questions.length === 0 ? [] : await setupAnswers(template);
    if (answers === null) {
      return;
    }
    const saved = await api.post(`/agent-templates/${template.id}/use`, answers.some((answer) => answer !== "") ? { answers } : null);
    rememberLook(saved.id, saved.look);
    if (!alsoSchedule) {
      toast("ok", t("views.agents.nameIsReady", { name: saved.name }));
    } else {
      const { missed } = await addSuggestedSchedules([{ agent: saved, template }]);
      if (missed.length > 0) {
        toast("error", t("views.agents.nameIsReadyButIts", { name: saved.name, value: missed[0] }));
      } else {
        toast("ok", t("views.agents.nameIsReadyWithIts", { name: saved.name }));
      }
    }
    ctx.navigate(`/agents/${saved.id}`);
  }

  // The team's agents the owner lacks (item 504), then the schedules of those it made when they were asked for,
  // switched off as a template's (item 464). A schedule that could not be added leaves its agent made, and the toast
  // says which and why. The team is the owner's now (item 610): a record under Your teams, which a second Use finds,
  // bringing back an agent it lacks. The list then shows the team and the agents.
  async function useTeam(team, members, alsoSchedules) {
    const used = await api.post(`/agent-teams/${team.id}/use`, null);
    for (const record of [...used.made, ...used.kept]) {
      rememberLook(record.id, record.look);
    }
    // Every member gets the schedule its template suggests when asked for, the agents the owner had too (item 722:
    // they were left without, and the switch read as if it had not taken); one that has it already is left as it is.
    const memberOf = (record) => members.find((member) => member.name.toLowerCase() === record.name.trim().toLowerCase());
    const pairs = alsoSchedules
      ? [...used.made, ...used.kept].map((agent) => ({ agent, template: memberOf(agent) })).filter(({ template }) => template !== undefined)
      : [];
    const { added, had: scheduledAlready, missed } = await addSuggestedSchedules(pairs);
    const made = AND_LIST.format(used.made.map((record) => record.name));
    const had = used.kept.length === 0 ? "" : t("views.agents.alreadyYoursAndKeptAs", { format: AND_LIST.format(used.kept.map((record) => record.name)) });
    // A kept agent that named its tools was given message_agent, to hand its teammates their parts (item 512).
    const joined = used.joined.length === 0 ? "" : t(used.joined.length === 1 ? "views.agents.joinedOne" : "views.agents.joinedMany", { names: AND_LIST.format(used.joined) });
    // The team under Your teams: made now, or found with the agents it lacked put back (item 610).
    const placed = used.created
      ? t("views.agents.nameIsUnderYourTeams", { name: used.team.name })
      : used.added.length === 0
        ? ""
        : t(used.added.length === 1 ? "views.agents.backInOne" : "views.agents.backInMany", { names: AND_LIST.format(used.added), team: used.team.name });
    const off =
      added.length === 0
        ? ""
        : t(added.length === 1 ? "views.agents.schedulesAddedOne" : "views.agents.schedulesAddedMany", { count: added.length });
    const already = scheduledAlready.length === 0 ? "" : t("views.agents.formatHadValueAlready", { format: AND_LIST.format(scheduledAlready), value: scheduledAlready.length === 1 ? t("views.agents.itsSchedule") : t("views.agents.theirSchedules") });
    if (missed.length > 0) {
      const key = used.made.length === 0 ? "views.agents.teamNotAllAdded" : used.made.length === 1 ? "views.agents.teamMadeNotAllOne" : "views.agents.teamMadeNotAllMany";
      toast("error", t(key, { name: team.name, made, join: missed.join("; "), had, placed, joined, off, already }));
    } else if (used.made.length === 0) {
      toast("ok", t("views.agents.youHaveEveryAgentOf", { name: team.name, placed, joined, off, already }));
    } else {
      toast("ok", t(used.made.length === 1 ? "views.agents.teamReadyOne" : "views.agents.teamReadyMany", { name: team.name, made, had, placed, joined, off, already }));
    }
    // The lists show the team and the agents, and the cards mark them Yours. The page stays on the ready-made ones, where
    // the owner was choosing; the toast says the Teams tab holds the team (item 1136).
    await Promise.all([reload(), reloadTeams(), loadTemplates()]);
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
      owners = summaries.map(({ record }) => record).filter((record) => record.id !== DOT_ID);
      mineCount.textContent = String(summaries.length);
      summariesShown = summaries;
      selectButton.hidden = picking !== null || owners.length < 2;
      drawGrid();
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  // The teams the owner has (item 610), each a card; read on its own, so that a list of agents that fails leaves them.
  async function reloadTeams() {
    const mine = ++teamsTicket;
    try {
      const teams = await api.get("/teams");
      if (disposed || mine !== teamsTicket) {
        return;
      }
      teamsCount.textContent = String(teams.length);
      teamsHolder.replaceChildren(
        teams.length === 0
          ? el("div", { class: "card empty-teams" }, el("p", { class: "muted" }, t("views.agents.noTeamsYetStartFrom")))
          : el("div", { class: "team-grid" }, teams.map(teamTile)),
      );
    } catch (error) {
      if (!disposed && mine === teamsTicket) {
        teamsHolder.replaceChildren(errorPanel(error, () => attempt(reloadTeams)));
      }
    }
  }

  let editor = null;

  function closeEditor() {
    if (editor !== null) {
      editor.dispose?.();
      editor = null;
    }
    editorSlot.replaceChildren();
  }

  function openEditor() {
    closeEditor();
    editor = agentForm({
      record: null,
      // An agent just made meets you at once in its first chat (item 993): it greets you in its role and asks what you
      // want from it.
      onSaved: (saved) => attempt(() => meet(saved, ctx)),
      onCancel: closeEditor,
    });
    editorSlot.replaceChildren(editor.element);
    editor.focus();
  }

  // A team of the agents the owner has (item 610), put together here; Use team on a ready team makes one in a click.
  async function openTeamEditor(chosen = []) {
    closeEditor();
    if (picking !== null) {
      setPicking(false);
    }
    // The agents are read again, so that one made a moment ago is among them.
    await reload();
    editor = teamForm({
      team: null,
      agents: owners,
      chosen,
      onSaved: (saved) => ctx.navigate(`/teams/${saved.id}`),
      onCancel: closeEditor,
    });
    editorSlot.replaceChildren(editor.element);
    editor.element.scrollIntoView({ block: "start", behavior: "smooth" });
    editor.focus();
  }

  const newButton = el("button", { class: "btn primary", type: "button", "aria-label": t("views.agents.newAgent"), onclick: openEditor }, icon("plus"), el("span", { class: "label" }, t("views.agents.newAgent")));
  const chooser = el("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": t("views.agents.agentFile") });
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
  // The page's other actions sit behind one button beside New agent (item 1136), as an agent's page keeps its own
  // behind More (item 993): one primary action in sight, the rest a click away.
  const moreButton = el("button", { class: "btn ghost icon-only", type: "button", title: t("views.agents.more"), "aria-label": t("views.agents.moreActions"), "aria-haspopup": "menu" }, icon("ellipsis"));
  moreButton.addEventListener("click", () => {
    const box = moreButton.getBoundingClientRect();
    openMenu({
      label: t("views.agents.moreActions"),
      at: { x: box.right - 220, y: box.bottom + 4 },
      back: moreButton,
      button: moreButton,
      items: [
        { label: t("views.agents.newTeam"), icon: "users", title: t("views.agents.putAgentsOfYoursTogether"), run: () => attempt(() => openTeamEditor()) },
        { label: t("views.agents.importAnAgent"), icon: "upload", title: t("views.agents.makeAnAgentFromA"), run: () => chooser.click() },
        "separator",
        { label: t("views.agents.handoffs"), icon: "branch", title: t("views.agents.todaySMessagesBetweenAgents"), run: () => ctx.navigate("/team") },
      ],
    });
  });
  // What Dot handed out to the agents and the objectives it keeps (items 907, 908), read only.
  const work = workOrdersPart();
  // The page in parts (item 1136): the agents the owner has and the ready-made ones first, then the teams, the work
  // orders, one part at a time; the part shown is kept in this browser.
  const parts = [
    { id: "agents", label: t("views.agents.agents"), sections: [yours, templates] },
    { id: "teams", label: t("views.agents.teams"), sections: [yourTeams] },
    { id: "orders", label: t("views.agents.orders"), sections: [work.element] },
  ];
  const kept = readPref(PART_KEY);
  const tabs = segmented(
    parts.map(({ id, label }) => ({ id, label, hint: null })),
    parts.some(({ id }) => id === kept) ? kept : "agents",
    (id) => showPart(id),
  );
  tabs.element.classList.add("agents-tabs");
  tabs.element.setAttribute("aria-label", t("views.agents.partsOfTheAgentsPage"));
  function showPart(id) {
    writePref(PART_KEY, id);
    tabs.set(id);
    for (const part of parts) {
      for (const section of part.sections) {
        section.hidden = part.id !== id;
      }
    }
  }
  root.append(
    pageHead(
      t("views.agents.agents"),
      t("views.agents.dotYourTeamsAndThe"),
      el("div", { class: "agents-head" }, tabs.element, el("span", { class: "spacer" }), chooser, moreButton, newButton),
    ),
    editorSlot,
    yours,
    templates,
    yourTeams,
    work.element,
  );
  showPart(tabs.value());
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(reload);
      attempt(reloadTeams);
      attempt(work.reload);
    }
  });
  const stopBack = onStreamBack(() => {
    attempt(reload);
    attempt(reloadTeams);
    attempt(work.reload);
  });
  // The list is read again when a run starts or ends; in between, the cards' ages keep up with the clock (item 633).
  const ages = setInterval(() => {
    for (const node of root.querySelectorAll(".agent-when[data-ts]")) {
      setText(node, ago(Number(node.dataset.ts)));
    }
  }, AGE_REFRESH_MS);
  attempt(reload);
  attempt(reloadTeams);
  attempt(loadTemplates);
  attempt(work.reload);
  return () => {
    disposed = true;
    clearInterval(ages);
    stopLifecycle();
    stopBack();
    closeEditor();
    work.dispose();
  };
}

// The part of the Agents page shown (item 1136), kept in this browser: "agents", "teams" or "orders".
const PART_KEY = "dot.agents.part";

// ------------------------------------------------------------------------------------------------- the page

/**
 * Where the composer of a page keeps its choices, for `record`, the agent whose page it is, or, for a team's page (item
 * 610), the bounds of Dot's own runs (`{ model: "", effort: null, approvals: null }`). It starts from the bounds, the
 * approvals and the reasoning level (on every model that offers it), and holds what is chosen there for that page alone:
 * this browser's choices for the home's box would otherwise stand in the agent's place, a mode chosen there for another
 * chat loosening the agent's, and a choice made here would change the home's. Fast mode and goal mode are the home's,
 * which the agent has no bounds for.
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
    // An agent's message starts on Auto, as the home's and a chat's do (items 495 and 513): its words settle the mode.
    mode: AUTO_MODE.id,
    model: record.model ?? "",
    once: null,
  };
}

/** The composer of an agent's page: a message for the agent, sent with Enter, with the mode, model and reasoning and
 * Options pills; the model starts at the agent's own, the mode at Auto, and /auto, /task, /chat and /research name one
 * as on the home (item 513). The message goes on in the agent's conversation (item 1233, `follow`), and its chat
 * opens: the run it started, or the run at work it reached. */
function agentComposer(record, ctx, follow) {
  const opening = {
    ...ctx,
    steered: (runId) => ctx.navigate(`/runs/${runId}`),
    queued: (runId) => ctx.navigate(`/runs/${runId}`),
  };
  return startComposer({ name: record.name, bounds: record, owner: { agent_id: record.id }, ctx: opening, follow });
}

/**
 * The composer of a page that starts a chat for `name`: an agent's page, or a team's (item 610). `bounds` are where the
 * choices start (`agentKept`), `owner` what the message says of whose chat it is, `{ agent_id }` or `{ team_id }`, and
 * `ctx` the page's context, which opens the run the message started. Returns `element`, `focus()` and `dispose()`.
 */
/**
 * What the box that starts an agent's run is given of its page: where a started run is opened, and what is told of a
 * message steered to the run at work, or one queued for it.
 * @typedef {{ navigate: (path: string) => void, steered?: (runId: string, text: string) => void,
 *   queued?: (runId: string, text: string) => void, [key: string]: any }} ComposerContext
 */

/**
 * The box that starts an agent's run. `follow`, when given, says where the next message goes (item 1233):
 * the run at work it is sent to (`working`), or the chat it goes on in (`threadId`), else a new chat.
 * @param {{ name: string, bounds: object, owner: object, ctx: ComposerContext, follow?: () => { threadId: string | null, working: string | null } }} options
 */
export function startComposer({ name, bounds, owner, ctx, follow }) {
  const frame = composerFrame({
    label: t("views.agents.messageName", { name }),
    boxLabel: t("views.agents.message"),
    placeholder: emptyBoxWords(name, true),
    maxPx: MAX_GOAL_PX,
    commands: MESSAGE_MODES.map((option) => ({
      label: `/${option.id}`,
      hint: `${option.label}: ${option.hint}`,
      insert: "",
      run: () => choices.setMode(option.id),
    })),
    send: submit,
    sendLabel: t("views.agents.start"),
    draftKey: null,
    listener: null,
  });
  const { textarea: goal, attachments } = frame;
  const choices = runChoices({
    frame,
    modes: MESSAGE_MODES,
    kept: agentKept(bounds),
    optionsTitle: t("views.agents.approvals"),
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
  // The message whose sending failed and where it went (item 1245), so that sent again it goes there with its key.
  let unsent = null;
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
    // A message sent again after an answer that was lost goes where its first sending went, with its key (item 189).
    const where = unsent !== null && unsent.text === text ? unsent.where : (follow?.() ?? { threadId: null, working: null });
    unsent = { text, where };
    try {
      await attempt(async () => {
        if (await sendToWork({ where, text, choices, frame, owner, keys, ctx })) {
          unsent = null;
          return;
        }
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
          thread_id: where.threadId,
          attachments: attachments.ids(),
          ...owner,
          ...frame.picks.body(),
        };
        const created = await api.post("/runs", { ...body, key: keys.of("run", body) });
        keys.sent();
        choices.sent();
        frame.picks.sent();
        frame.clear();
        unsent = null;
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
    record.made_by === null ? null : el("p", { class: "home-greeting agent-made" }, t("views.agents.madeByDotInOne")),
    record.paused_at === null
      ? null
      : el(
          "p",
          { class: "home-greeting agent-paused" },
          el("span", { class: "badge b-warn" }, t("views.agents.paused")),
          t("views.agents.itsSchedulesStartNothingBy2"),
        ),
  );
}

/** A schedule the agent's runs run as (item 464): its switch, its name, its goal in full (a template's is added
 * switched off, to be read before it runs), when it runs, and Edit, in Settings › Scheduled tasks. */
function agentScheduleRow(view, onToggle) {
  const { record } = view;
  // A one-time schedule that fired reads as one that ran, not as one switched off, and has nothing to switch (item 723).
  const done = view.ran_once === true;
  const toggle = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(record.enabled),
    "aria-label": `${record.enabled ? t("views.agents.switchOff") : t("views.agents.switchOn")} ${record.name}`,
    title: done ? t("views.agents.itRanOnceAndHas") : record.enabled ? t("views.agents.switchThisScheduleOff") : t("views.agents.switchThisScheduleOn"),
    disabled: done,
    onclick: () => onToggle(record),
  });
  const state = done
    ? el("span", { class: "badge b-muted", title: t("views.agents.itRanOnceItEnded", { last_status: record.last_status }) }, t("views.agents.ranOnce"))
    : !record.enabled
      ? el("span", { class: "badge b-muted" }, t("views.agents.switchedOff"))
      : el("span", null, view.next_local === null ? t("views.agents.willNotRunAgain") : t("views.agents.nextNextLocal", { next_local: view.next_local }));
  return el(
    "div",
    { class: "list-row", dataset: { schedule: record.id } },
    toggle,
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, record.name),
      el("div", { class: "agent-schedule-goal" }, record.goal),
      el("div", { class: "row-meta" }, el("span", { class: "mono", title: t("views.agents.whenItRuns") }, view.when), state),
    ),
    el("div", { class: "row-actions" }, el("a", { class: "btn ghost sm", href: `#/settings/schedules/${record.id}`, title: t("views.agents.editItInSettingsScheduled") }, t("views.agents.edit"))),
  );
}

/**
 * The schedule an agent's template suggests and the agent lacks (item 722): an agent made before the switch was asked
 * for, or kept by Use team, had no way to get it. `onAdd()` adds it, switched off.
 */
function suggestedScheduleRow(template, onAdd) {
  return el(
    "div",
    { class: "list-row", dataset: { suggestedSchedule: template.id } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, t("views.agents.suggestedByItsTemplate")),
      el("div", { class: "agent-schedule-goal" }, template.task),
      el("div", { class: "row-meta" }, el("span", { title: t("views.agents.whenItWouldRun") }, template.schedule), el("span", { class: "badge b-muted" }, t("views.agents.notAdded"))),
    ),
    el("div", { class: "row-actions" }, el("button", { class: "btn sm", type: "button", title: t("views.agents.addItSwitchedOffYou"), onclick: onAdd }, t("views.agents.addIt"))),
  );
}

/** A lesson the agent learned: the note, how the agent's own runs fared after it, and Forget. */
function lessonRow(record, score, onForget) {
  const forget = el("button", { class: "btn ghost sm", type: "button" }, t("views.agents.forget"));
  forget.addEventListener("click", () => onForget(record));
  return el(
    "div",
    { class: "list-row", "data-lesson": record.id },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-text" }, record.content),
      el("div", { class: "row-meta" }, el("span", null, t("views.agents.savedAgo", { ago: ago(record.created_at) })), scoreLine(score)),
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
export function homeSection(key, title, rows, folds, toggle) {
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

/** The agent's work (item 2035), three rows as the phone's bot profile has them: its open work orders, its active
 * objectives and today's hand-offs, each counted, each a link to its own page. */
function workCounts(agentId, orders, objectives, team) {
  const { received, sent } = agentHandoffs(agentId, team.messages);
  return {
    error: null,
    orders: agentOrders(agentId, orders).filter(orderOpen).length,
    objectives: agentObjectives(agentId, orders, objectives).filter((objective) => objective.status === "active").length,
    handoffs: received.length + sent.length,
  };
}

/** The Work section; `counts.error` (the lists could not be read) is said in it, with `retry`. */
function workSection(agentId, counts, retry) {
  if (counts.error !== null) {
    return el("div", { class: "home-today-section", "data-section": "work" }, el("h2", { class: "section-label" }, t("views.agentwork.work")), errorPanel(counts.error, retry));
  }
  const row = (kind, mark, title, count, words) =>
    el(
      "a",
      { class: "list-row agent-work-row", href: `#/agents/${agentId}/${kind}`, dataset: { work: kind } },
      icon(mark),
      el("span", { class: "row-title" }, title),
      el("span", { class: "row-meta" }, count > 0 ? words : t("views.agentwork.none")),
      icon("chevron"),
    );
  return el(
    "div",
    { class: "home-today-section", "data-section": "work" },
    el("h2", { class: "section-label" }, t("views.agentwork.work")),
    el(
      "div",
      { class: "home-today-list card" },
      row("orders", "list", t("workorders.workOrders"), counts.orders, t("views.agentwork.openCount", { count: counts.orders })),
      row("objectives", "target", t("views.agentwork.objectives"), counts.objectives, t("views.agentwork.activeCount", { count: counts.objectives })),
      row("handoffs", "branch", t("views.team.handoffs"), counts.handoffs, t("views.agentwork.todayCount", { count: counts.handoffs })),
    ),
  );
}

function mountPage(root, ctx, agentId) {
  // The agent's bar is glass from the page's first frame, while the agent is read (item 1123).
  setStageGlass();
  let disposed = false;
  let ticket = 0;
  let composer = null;
  let form = null;
  let face = null;
  let shownDetail = null;
  let folds = readFolds(FOLDS_KEY);
  // The agent's conversation (item 1233): its home chat as the daemon last named it, which the box goes on in, unless
  // New chat in the "…" chose to start the next message afresh.
  let homeChat = null;
  let freshChat = false;
  // The way back into that conversation, under the box while there is one to go on in.
  const conversationLabel = el("span");
  const conversation = el("a", { class: "btn ghost sm agent-conversation", hidden: true }, icon("message"), conversationLabel);
  function paintConversation(record) {
    conversation.hidden = homeChat === null || freshChat;
    if (homeChat !== null) {
      conversation.setAttribute("href", `#/runs/${homeChat.latest_run_id}`);
      conversation.title = clip(homeChat.title, GOAL_CHARS);
    }
    setText(conversationLabel, t("views.agents.openYourChatWithName", { name: record.name }));
  }
  function follow() {
    if (freshChat || homeChat === null) {
      return { threadId: null, working: null };
    }
    // Waiting for the owner (blocked) is at work too: the message reaches that run, as in its chat (item 898).
    return { threadId: homeChat.id, working: WORKING.has(homeChat.latest_status) ? homeChat.latest_run_id : null };
  }
  // The agent's page is laid out as the home is (item 124): its face, name and role over the composer in the middle,
  // and under them its chats, what it learned and its messages as the home's Today strip, each only when it has rows.
  const top = el("div", { class: "home-start agent-top" });
  const sections = el("section", { class: "home-today agent-sections", "aria-label": t("views.agents.theAgentSWork") });
  // While the agent is read its page stands as a chat that opens: Dot's three dots in the middle, not a card's spinner
  // at the top that read as something else loading.
  const page = el("div", { class: "home agent-home" }, chatLoading());
  // Its face, name and box stand in the middle of the room under the bar, as the home's do (item 124): the row above
  // them takes half of what their height leaves (app.css), so the page keeps that height as it changes.
  const stopCentring = trackHeight(top, page, "--home-start-h");

  // The bar names the agent, as a heading of the page, and so does the tab (`Researcher · Agents · Dot`).
  function stageOf(record) {
    ctx.setTitle(t("views.agents.nameAgents", { name: record.name }));
    setStageTitle(actorFace(record.id, "idle", record.look), el("h1", { class: "stage-h1 stage-name" }, record.name));
    // On a phone the bar is a chat's (item 1123): the name in its middle on glass, the tools in one pill of glass.
    setStageGlass();
    setStageTools(
      el(
        "div",
        { class: "stage-pill" },
        // What the agent's page can do of it stands in one "…" (item 993, the owner's ask for a plain page): four
        // buttons of a row were a toolbar on a phone.
        agentMenuButton(record),
      ),
    );
  }

  function agentMenuButton(record) {
    const button = el("button", { class: "icon-btn", type: "button", title: t("views.agents.moreForName", { name: record.name }), "aria-label": t("views.agents.moreForName", { name: record.name }), "aria-haspopup": "menu" }, icon("ellipsis"));
    button.addEventListener("click", () => {
      const box = button.getBoundingClientRect();
      openMenu({
        label: t("views.agents.moreForName", { name: record.name }),
        at: { x: box.right - 200, y: box.bottom + 4 },
        back: button,
        button,
        items: [
          {
            label: t("views.agents.newChat"),
            icon: "plus",
            title: t("views.agents.theNextMessageStartsA", { name: record.name }),
            run: () => {
              freshChat = true;
              paintConversation(record);
              toast("ok", t("views.agents.yourNextMessageToName", { name: record.name }));
              composer?.focus();
            },
          },
          { label: t("views.agents.edit"), icon: "pencil", run: () => edit(record) },
          { label: t("views.agents.export"), icon: "download", title: t("views.agents.saveTheAgentAsA"), run: () => attempt(() => exportAgent(record)) },
          record.paused_at === null
            ? { label: t("views.agents.pause"), icon: "pause", title: t("views.agents.itsSchedulesStartNothingBy"), run: () => attempt(() => setPaused(record, true)) }
            : { label: t("views.agents.resume"), icon: "play", title: t("views.agents.itsSchedulesStartAgain"), run: () => attempt(() => setPaused(record, false)) },
          "separator",
          { label: t("views.agents.delete"), icon: "trash", danger: true, run: () => attempt(() => remove(record)) },
        ],
      });
    });
    return button;
  }

  /** Pause the agent or resume it (item 465), then show the page as the daemon has it. */
  async function setPaused(record, paused) {
    await api.post(`/agents/${record.id}/${paused ? "pause" : "resume"}`, null);
    toast("ok", paused ? t("views.agents.namePausedItsSchedulesStart", { name: record.name }) : t("views.agents.nameResumed", { name: record.name }));
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
    composer = agentComposer(record, ctx, follow);
    face?.dispose();
    face = liveFace(record.id, "idle", record.look, FACE_PX);
    paintConversation(record);
    top.replaceChildren(who(record, face.element), composer.element, conversation);
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
      schedules.length === 0 ? "" : t("views.agents.itsPartSwitchedOff", { part: tCount(schedules.length, "views.agents.countScheduleIs", "views.agents.countSchedulesAre") });
    const confirmed = await confirmDialog({
      title: t("views.agents.deleteName", { name: record.name }),
      message: t("views.agents.itsChatsStayAsDot", { switchedOff }),
      confirmLabel: t("views.agents.delete"),
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await api.del(`/agents/${record.id}`);
      toast("ok", t("views.agents.nameDeleted", { name: record.name }));
      ctx.navigate("/settings/agents");
    }
  }

  /** Add the schedule the agent's template suggests, switched off until its goal is read (item 722). */
  async function addSuggested(record, template) {
    const { added, missed } = await addSuggestedSchedules([{ agent: record, template }]);
    if (missed.length > 0) {
      toast("error", t("views.agents.itsScheduleWasNotAdded", { value: missed[0] }));
    } else if (added.length > 0) {
      toast("ok", t("views.agents.itsScheduleIsAddedSwitched"));
    }
    await refresh();
  }

  /** Switch one of the agent's schedules on or off, then show the lists as the daemon has them. */
  async function switchSchedule(record) {
    await api.post(`/schedules/${record.id}/${record.enabled ? "disable" : "enable"}`, null);
    toast("ok", record.enabled ? t("views.agents.nameIsSwitchedOff", { name: record.name }) : t("views.agents.nameIsSwitchedOn", { name: record.name }));
    await refresh();
  }

  async function forget(record) {
    const confirmed = await confirmDialog({
      title: t("views.agents.forgetThisLesson"),
      message: t("views.agents.theNoteIsRemovedFrom"),
      confirmLabel: t("views.agents.forget"),
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await api.del(`/memories/${record.id}`);
      toast("ok", t("views.agents.lessonForgotten"));
      await refresh();
    }
  }

  // The agent as the daemon has it now, with the schedules its runs run as (item 464); null when the page went or a
  // later load overtook this one. A failure takes the place of the page, with a way to try again.
  async function load() {
    const mine = ++ticket;
    try {
      const [detail, schedules, templates, work] = await Promise.all([
        api.get(`/agents/${agentId}`),
        api.get("/schedules"),
        // The template of its name, for the schedule it suggests (item 722); a list that cannot be read offers none.
        api.get("/agent-templates").catch((error) => {
          console.warn("the templates could not be listed", error);
          return [];
        }),
        // What the agent is handed and passes on (item 2035), counted on its page as the phone's profile counts it; a
        // list that cannot be read says so in the Work section alone, and the page stands.
        Promise.all([api.get("/work-orders"), api.get("/objectives"), api.get("/team")]).then(
          (lists) => ({ lists, error: null }),
          (error) => ({ lists: null, error }),
        ),
      ]);
      const own = schedules.filter((view) => view.record.options.agent_id === agentId);
      const name = detail.record.name.trim().toLowerCase();
      const template = templates.find((each) => each.name.toLowerCase() === name && each.cron !== "") ?? null;
      const suggested = template !== null && !own.some((view) => view.record.goal === template.task) ? template : null;
      const counts = work.lists === null ? { error: work.error } : workCounts(agentId, ...work.lists);
      return disposed || mine !== ticket ? null : { ...detail, schedules: own, suggested, work: counts };
    } catch (error) {
      if (!disposed && mine === ticket) {
        page.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
      return null;
    }
  }

  // The lists under the top: lessons and skills, schedules. The top stays where it is, so the focus and what is typed
  // in it stay too.
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
      workSection(detail.record.id, detail.work, () => attempt(reload)),
      homeSection(
        "schedules",
        t("views.agents.schedules"),
        [
          ...detail.schedules.map((view) => agentScheduleRow(view, (record) => attempt(() => switchSchedule(record)))),
          ...(detail.suggested === null ? [] : [suggestedScheduleRow(detail.suggested, () => attempt(() => addSuggested(detail.record, detail.suggested)))]),
        ],
        folds,
        toggleFold,
      ),
      homeSection("learned", t("views.agents.whatItLearned"), learned, folds, toggleFold),
    ];
    sections.replaceChildren(...parts.filter((part) => part !== null));
    sections.hidden = sections.childElementCount === 0;
    // The tests and the page's own styles find what it learned by this mark.
    sections.querySelector('[data-section="learned"]')?.classList.add("agent-learned");
    if (top.parentNode !== page) {
      page.replaceChildren(top, sections);
    }
  }

  // Whether the top is to be built again by the next read that is drawn: only the newest read is (`load`), and a
  // refresh that started after the page's opening read, as its agent's run ended, left the page without its box (item
  // 1036).
  let greetDue = false;

  /** Draw a read: the top built again when it is due, and the lists. */
  function paint(detail) {
    // The conversation as the daemon names it now (item 1233): a chat deleted or begun since is followed.
    homeChat = detail.home ?? null;
    paintConversation(detail.record);
    if (greetDue) {
      greetDue = false;
      greet(detail.record);
      // On a touch screen the caret waits for a tap, as on the home (item 293).
      if (!touchFirst()) {
        composer.focus();
      }
    }
    paintLists(detail);
  }

  // The whole page: the top built again (the composer, after a save) and the lists.
  async function reload() {
    greetDue = true;
    const detail = await load();
    if (detail !== null) {
      paint(detail);
    }
  }

  // Only the lists, when a run starts or ends or a lesson goes: a message being typed or an open form stays (item 116).
  async function refresh() {
    const detail = await load();
    if (detail !== null) {
      paint(detail);
    }
  }

  root.append(el("div", { class: "agent-page" }, el("a", { class: "back", href: "#/settings/agents" }, icon("arrowLeft"), t("views.agents.allAgents")), page));
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(refresh);
    }
  });
  const stopBack = onStreamBack(() => attempt(refresh));
  // Chats deleted elsewhere on the page may take the conversation with them (item 1233): the box then starts one.
  const onAgentsChanged = () => attempt(refresh);
  window.addEventListener(AGENTS_CHANGED_EVENT, onAgentsChanged);
  attempt(reload);
  return () => {
    disposed = true;
    stopCentring();
    stopLifecycle();
    stopBack();
    window.removeEventListener(AGENTS_CHANGED_EVENT, onAgentsChanged);
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
