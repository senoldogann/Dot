// The home page: Dot's presence (presence.js: the face in its state, a greeting, one line of now) and one composer in
// the middle of the window, the Today strip under them. The recent runs live in the sidebar; a run that waits for an
// answer shows above, as a card.

import { actorFace, DOT_ID } from "../actors.js";
import { approvalCard, fromRecord, LEFT_IN_RUN } from "../approvals.js";
import { api } from "../api.js";
import { readyToSend } from "../attachments.js";
import { composerFrame, composerPill } from "../composer.js";
import { el, modelName } from "../dom.js";
import { allModels, forgetLevel, levelLabel, offersChoice, savedLevel, saveLevel } from "../efforts.js";
import { icon } from "../icons.js";
import { anchoredPopover } from "../popover.js";
import { buildPresence } from "../presence.js";
import { providerLabel, providerMark, providerOf } from "../providers.js";
import { approvalLabel, approvalsPicker, goalModePicker, standingPicker } from "../runmodes.js";
import { onCompose } from "../shortcuts.js";
import { getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt } from "../ui.js";

const MODES = [
  { id: "task", label: "Task", hint: "Carry the goal out end to end on this Mac", icon: "target" },
  { id: "chat", label: "Chat", hint: "Converse; use tools whenever they help", icon: "message" },
  { id: "research", label: "Research", hint: "Read-only: gather information and report, change nothing", icon: "search" },
];
// The Options pill's icon by approval mode: closed for Ask, a guard for Auto (a review model answers), open for YOLO.
const APPROVAL_ICONS = { ask: "lock", auto: "shield", yolo: "lockOpen" };
const MAX_GOAL_PX = 320;
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;

function defaultRefOf(snapshot) {
  return snapshot.status === null ? null : snapshot.status.models.main;
}

/** The models that can call tools, grouped by provider: runs need tools, so the others would only fail. */
function toolGroups(providers) {
  return providers
    .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools) }))
    .filter((group) => group.models.length > 0);
}

/** The words of the daemon's default model, for the row that follows it. */
function defaultRowText(ref) {
  return ref === null ? "Default model" : `Default (${modelName(ref)})`;
}

/** The option row of a popover's list: `lead` (a mark or an icon, or null), the label with its `hint` under it (or ""),
 * and a tick when it is the one chosen. */
function optionRow({ lead, label, hint, selected, onPick }) {
  return el(
    "button",
    { class: "pop-option", type: "button", role: "option", "aria-selected": String(selected), onclick: onPick },
    lead,
    el("span", { class: "pop-option-text" }, el("span", null, label), hint === "" ? null : el("span", { class: "pop-option-hint" }, hint)),
    icon("check"),
  );
}

/** `listener` is the home's face (presence.js `listen`): it hears the box and the send. */
function buildComposer(ctx, listener) {
  const frame = composerFrame({
    label: "New chat",
    boxLabel: "Goal",
    placeholder: "Message Dot",
    maxPx: MAX_GOAL_PX,
    commands: MODES.map((option) => ({
      label: `/${option.id}`,
      hint: `${option.label}: ${option.hint}`,
      insert: "",
      run: () => {
        mode.set(option.id);
        modePicked(option.id);
      },
    })),
    send: submit,
    sendLabel: "Start",
    draftKey: "home",
    listener,
  });
  const { textarea: goal, attachments } = frame;
  // The approval mode stays in sight as the Options pill's value and icon, since it decides whether the run asks
  // before it acts.
  const optionsPill = composerPill({ id: "options", caption: "Options", value: "", icon: null });
  optionsPill.element.title = "Approvals, goal mode and keep going";
  const approvals = approvalsPicker((shown) => {
    optionsPill.setValue(shown === null ? "" : approvalLabel(shown), "");
    optionsPill.setIcon(icon(shown === null ? "lock" : APPROVAL_ICONS[shown]));
    optionsPill.setTone(shown === "yolo" ? "warn" : null);
  });
  const goalMode = goalModePicker();
  // A campaign checks its outcome itself, so goal mode does not apply to one; the switch only shows for a task.
  const standing = standingPicker((on) => goalMode.setAvailable(!on));
  // A chat has no goal to check, so the goal switch only belongs to the other two kinds of run; only a task keeps going.
  function modePicked(id) {
    standing.setAvailable(id === "task" && agentId === "");
    goalMode.setAvailable(id !== "chat" && !standing.value());
    goal.focus();
  }
  // The mode (item 108): a pill with the mode's icon, its menu the three modes with what each does.
  const modePill = composerPill({ id: "mode", caption: "Mode", value: "", icon: null });
  let modeId = "task";
  const mode = {
    value: () => modeId,
    set: (id) => {
      modeId = id;
      paintMode();
    },
  };
  function pickMode(id) {
    mode.set(id);
    modeMenu.close();
    modePicked(id);
  }
  const modeMenu = anchoredPopover({
    anchor: modePill.element,
    frame: frame.element,
    label: "Mode",
    build: () => [
      el(
        "div",
        { role: "listbox", "aria-label": "Mode" },
        ...MODES.map((option) =>
          optionRow({ lead: icon(option.icon), label: option.label, hint: option.hint, selected: option.id === modeId, onPick: () => pickMode(option.id) }),
        ),
      ),
    ],
  });
  function paintMode() {
    const option = MODES.find((candidate) => candidate.id === modeId);
    modePill.setValue(option.label, "");
    modePill.setIcon(icon(option.icon));
    modePill.element.title = option.hint;
    modeMenu.repaint();
  }
  paintMode();
  // Approvals and goal mode are kept per browser and keep going is for the odd goal, so the three wait in one popover.
  const optionsMenu = anchoredPopover({
    anchor: optionsPill.element,
    frame: frame.element,
    label: "Options",
    build: () => [approvals.element, goalMode.element, standing.element, approvals.warning],
  });
  // The agent the run is for (item 86): Dot, or one the owner made, with its still face and name on the pill and the
  // agents with their roles in its menu (item 115); the pill only shows once an agent exists. A campaign is Dot's own
  // work, so Keep going hides while an agent is picked.
  const agentPill = composerPill({ id: "agent", caption: "Agent", value: "Dot", icon: actorFace(DOT_ID, "idle", null) });
  agentPill.element.title = "The agent the run is for";
  agentPill.element.hidden = true;
  let agents = [];
  let agentId = "";
  function chosenAgent() {
    return agents.find((candidate) => candidate.id === agentId) ?? null;
  }
  function paintAgent() {
    const chosen = chosenAgent();
    goal.placeholder = chosen === null ? "Message Dot" : `Message ${chosen.name}`;
    agentPill.setValue(chosen === null ? "Dot" : chosen.name, "");
    agentPill.setIcon(chosen === null ? actorFace(DOT_ID, "idle", null) : actorFace(chosen.id, "idle", chosen.look));
    standing.setAvailable(chosen === null && mode.value() === "task");
    agentMenu.repaint();
  }
  function pickAgent(id) {
    agentId = id;
    agentMenu.close();
    paintAgent();
    goal.focus();
  }
  const agentMenu = anchoredPopover({
    anchor: agentPill.element,
    frame: frame.element,
    label: "Agent",
    build: () => [
      el(
        "div",
        { role: "listbox", "aria-label": "Agent" },
        optionRow({ lead: actorFace(DOT_ID, "idle", null), label: "Dot", hint: "", selected: agentId === "", onPick: () => pickAgent("") }),
        ...agents.map((record) =>
          optionRow({ lead: actorFace(record.id, "idle", record.look), label: record.name, hint: record.role, selected: agentId === record.id, onPick: () => pickAgent(record.id) }),
        ),
      ),
    ],
  });
  api
    .get("/agents")
    .then((summaries) => {
      agents = summaries.map((summary) => summary.record).filter((record) => record.id !== "dot");
      agentPill.element.hidden = agents.length === 0;
      agentMenu.repaint();
    })
    .catch((error) => {
      console.warn("the agents could not be listed", error);
    });
  // The model: the daemon's default until one is picked, worn with its provider's mark (item 108).
  const modelPill = composerPill({ id: "model", caption: "Model", value: "", icon: null });
  // The reasoning level of a model that offers a choice, kept in the browser per model; hidden for the others.
  const reasoningPill = composerPill({ id: "reasoning", caption: "Reasoning", value: "", icon: icon("brain") });
  reasoningPill.element.title = "Reasoning level";
  reasoningPill.element.hidden = true;
  let chosenRef = "";
  let providers = [];
  let shownDefault = NOTHING_SHOWN;

  // The model a run would use now: the one picked, else the daemon's default.
  function selectedInfo() {
    const ref = chosenRef === "" ? defaultRefOf(getSnapshot()) : chosenRef;
    return allModels(providers).find((candidate) => candidate.ref === ref) ?? null;
  }

  // The Model pill shows the mark and the name, its tooltip the whole reference; the Reasoning pill the level chosen,
  // or the one the daemon would use.
  function paintModel() {
    const info = selectedInfo();
    const ref = chosenRef === "" ? defaultRefOf(getSnapshot()) : chosenRef;
    modelPill.setValue(ref === null ? "Default" : modelName(ref), "");
    modelPill.setIcon(providerMark(ref === null ? "" : providerOf(ref)));
    modelPill.element.title = info === null ? "" : info.ref;
    const choice = info !== null && offersChoice(info);
    reasoningPill.element.hidden = !choice;
    if (choice) {
      const level = savedLevel(info) ?? info.default_effort;
      reasoningPill.setValue(level === null ? "Default" : levelLabel(level), "");
    } else {
      reasoningMenu.close();
    }
    modelMenu.repaint();
    reasoningMenu.repaint();
  }

  function pickModel(ref) {
    chosenRef = ref;
    modelMenu.close();
    paintModel();
    modelPill.element.focus();
  }

  function pickLevel(info, level) {
    if (level === null) {
      forgetLevel(info);
    } else {
      saveLevel(info, level);
    }
    reasoningMenu.close();
    paintModel();
    reasoningPill.element.focus();
  }

  function modelRows() {
    const fallback = defaultRefOf(getSnapshot());
    const list = el(
      "div",
      { role: "listbox", "aria-label": "Model" },
      optionRow({ lead: providerMark(fallback === null ? "" : providerOf(fallback)), label: defaultRowText(fallback), hint: "", selected: chosenRef === "", onPick: () => pickModel("") }),
    );
    for (const group of toolGroups(providers)) {
      list.append(el("div", { class: "pop-group" }, providerLabel(group.provider)));
      for (const info of group.models) {
        const row = optionRow({ lead: providerMark(group.provider), label: modelName(info.ref), hint: "", selected: chosenRef === info.ref, onPick: () => pickModel(info.ref) });
        row.title = info.ref;
        list.append(row);
      }
    }
    return [list];
  }

  function reasoningRows() {
    const info = selectedInfo();
    if (info === null || !offersChoice(info)) {
      return [];
    }
    const saved = savedLevel(info);
    const fallback = info.default_effort === null ? "Default" : `Default (${levelLabel(info.default_effort)})`;
    return [
      el(
        "div",
        { role: "listbox", "aria-label": "Reasoning" },
        optionRow({ lead: null, label: fallback, hint: "The daemon's own choice", selected: saved === null, onPick: () => pickLevel(info, null) }),
        ...info.efforts.map((level) => optionRow({ lead: null, label: levelLabel(level), hint: "", selected: saved === level, onPick: () => pickLevel(info, level) })),
      ),
    ];
  }

  const modelMenu = anchoredPopover({ anchor: modelPill.element, frame: frame.element, label: "Model", build: modelRows });
  const reasoningMenu = anchoredPopover({ anchor: reasoningPill.element, frame: frame.element, label: "Reasoning", build: reasoningRows });

  // Called on every status update; it only does work when the daemon's default model changed.
  function syncDefault() {
    const ref = defaultRefOf(getSnapshot());
    if (ref === shownDefault) {
      return;
    }
    shownDefault = ref;
    paintModel();
  }

  // True from the moment a run is being started until the answer is in: Enter does not look at the disabled button, so
  // a second press must be refused here, or one goal would start two runs.
  let starting = false;

  async function submit() {
    if (starting) {
      return;
    }
    const text = goal.value.trim();
    if (text === "") {
      goal.focus();
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    const info = selectedInfo();
    starting = true;
    frame.sendButton.disabled = true;
    listener.sending();
    try {
      await attempt(async () => {
        const created = await api.post("/runs", {
          goal: text,
          mode: mode.value(),
          model: chosenRef === "" ? null : chosenRef,
          effort: info !== null && offersChoice(info) ? savedLevel(info) : null,
          approvals: approvals.value(),
          goal_mode: mode.value() === "chat" || standing.value() ? null : goalMode.value(),
          standing: standing.value(),
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: agentId === "" ? null : agentId,
        });
        frame.clear();
        ctx.navigate(`/runs/${created.id}`);
      });
    } finally {
      starting = false;
      frame.sendButton.disabled = false;
    }
  }

  loadModels().then(
    (loaded) => {
      providers = loaded;
      paintModel();
    },
    (error) => {
      console.warn("the model list is unavailable", error);
      modelPill.element.title = `The model list could not be loaded: ${error.message}`;
    },
  );
  syncDefault();

  // The status carries the daemon's defaults; the pickers show them until the person chose something else.
  function syncStatus() {
    syncDefault();
    approvals.sync();
    goalMode.sync();
  }

  frame.options.append(modePill.element, agentPill.element, modelPill.element, reasoningPill.element, optionsPill.element);
  const stopStatus = onChange(syncStatus);
  return {
    // Keep going's line sits under the frame, not in it.
    element: el("div", { class: "composer-stack" }, frame.element, standing.note),
    focus: () => goal.focus(),
    dispose: () => {
      stopStatus();
      optionsMenu.dispose();
      modeMenu.dispose();
      agentMenu.dispose();
      modelMenu.dispose();
      reasoningMenu.dispose();
      frame.dispose();
    },
  };
}

function buildApprovals() {
  const list = el("div", { class: "stack" });
  const section = el("section", { class: "home-approvals", hidden: true }, el("div", { class: "section-label" }, "Waiting for you"), list);
  const cards = new Map();

  function sync(snapshot) {
    const wanted = new Set(snapshot.approvals.map((record) => record.id));
    for (const [id, card] of cards) {
      if (!wanted.has(id)) {
        card.node.remove();
        cards.delete(id);
      }
    }
    for (const record of snapshot.approvals) {
      if (!cards.has(record.id)) {
        const card = approvalCard({
          approval: fromRecord(record),
          heading: el("a", { class: "small", href: `#/runs/${record.run_id}` }, "Open run"),
          onSettled: () => {
            refresh().catch((error) => console.warn("status refresh failed", error));
          },
          leftNote: LEFT_IN_RUN,
        });
        cards.set(record.id, card);
        list.append(card.node);
      }
    }
    section.hidden = cards.size === 0;
  }

  return { element: section, sync };
}

export function mount(root, ctx) {
  const presence = buildPresence();
  const composer = buildComposer(ctx, presence.listen);
  const approvals = buildApprovals();
  root.append(
    el(
      "div",
      { class: "home" },
      el("h1", { class: "visually-hidden" }, "New chat"),
      approvals.element,
      el("div", { class: "home-start" }, presence.element, composer.element),
      presence.strip,
    ),
  );
  approvals.sync(getSnapshot());
  const stopApprovals = onChange(approvals.sync);
  const stopCompose = onCompose(composer.focus);
  composer.focus();
  return () => {
    stopApprovals();
    stopCompose();
    composer.dispose();
    presence.dispose();
  };
}
