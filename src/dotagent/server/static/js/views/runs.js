// The home page: Dot's presence (presence.js: the face in its state, a greeting, one line of now) and one composer in
// the middle of the window, the Today strip under them. The recent runs live in the sidebar; a run that waits for an
// answer shows above, as a card.

import { approvalCard, fromRecord, LEFT_IN_RUN } from "../approvals.js";
import { api } from "../api.js";
import { createAttachments, readyToSend } from "../attachments.js";
import { wireComposer } from "../composer.js";
import { el, modelName } from "../dom.js";
import { allModels, offersChoice, savedLevel } from "../efforts.js";
import { icon } from "../icons.js";
import { segmented } from "../panels.js";
import { providerLabel } from "../providers.js";
import { reasoningSelect } from "../reasoning.js";
import { approvalLabel, approvalsPicker, goalModePicker, standingPicker } from "../runmodes.js";
import { buildPresence } from "../presence.js";
import { onCompose } from "../shortcuts.js";
import { getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt } from "../ui.js";

const MODES = [
  { id: "task", label: "Task", hint: "Carry the goal out end to end on this Mac" },
  { id: "chat", label: "Chat", hint: "Converse; use tools whenever they help" },
  { id: "research", label: "Research", hint: "Read-only: gather information and report, change nothing" },
];
const MAX_GOAL_PX = 320;
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;

function defaultRefOf(snapshot) {
  return snapshot.status === null ? null : snapshot.status.models.main;
}

function defaultOptionText(ref) {
  return ref === null ? "Default model" : `Default (${modelName(ref)})`;
}

/** The models that can call tools, grouped by provider: runs need tools, so the others would only fail. */
function toolGroups(providers) {
  return providers
    .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools) }))
    .filter((group) => group.models.length > 0);
}

function pick(caption, control) {
  return el("label", { class: "composer-pick" }, el("span", { class: "composer-pick-label" }, caption), control);
}

function buildComposer(ctx) {
  const goal = el("textarea", { class: "textarea", rows: "3", placeholder: "Message Dot", "aria-label": "Goal" });
  // The approval mode stays in sight on the Options button, since it decides whether the run asks before it acts.
  const approvalTag = el("span", { class: "chip", hidden: true });
  const approvals = approvalsPicker((shown) => {
    approvalTag.hidden = shown === null;
    approvalTag.className = `chip approvals-${shown}`;
    approvalTag.textContent = shown === null ? "" : approvalLabel(shown);
  });
  const goalMode = goalModePicker();
  // A campaign checks its outcome itself, so goal mode does not apply to one; the switch only shows for a task.
  const standing = standingPicker((on) => goalMode.setAvailable(!on));
  // A chat has no goal to check, so the goal switch only belongs to the other two kinds of run; only a task keeps going.
  function modePicked(id) {
    standing.setAvailable(id === "task" && agent.value === "");
    goalMode.setAvailable(id !== "chat" && !standing.value());
    goal.focus();
  }
  const mode = segmented(MODES, "task", modePicked);
  // The agent the run is for (item 86): Dot, or one the owner made; the menu only shows once an agent exists. A
  // campaign is Dot's own work, so Keep going hides while an agent is picked.
  const agentOption = el("option", { value: "" }, "Dot");
  const agent = el("select", { class: "select sm", "aria-label": "Agent" }, agentOption);
  const agentPick = pick("Agent", agent);
  agentPick.hidden = true;
  let agents = [];
  function agentPicked() {
    const chosen = agents.find((candidate) => candidate.id === agent.value) ?? null;
    goal.placeholder = chosen === null ? "Message Dot" : `Message ${chosen.name}`;
    standing.setAvailable(chosen === null && mode.value() === "task");
  }
  agent.addEventListener("change", agentPicked);
  api
    .get("/agents")
    .then((summaries) => {
      agents = summaries.map((summary) => summary.record).filter((record) => record.id !== "dot");
      agent.append(...agents.map((record) => el("option", { value: record.id }, record.name)));
      agentPick.hidden = agents.length === 0;
    })
    .catch((error) => {
      console.warn("the agents could not be listed", error);
    });
  const defaultOption = el("option", { value: "" });
  const model = el("select", { class: "select sm", "aria-label": "Model" }, defaultOption);
  const reasoning = el("span", { class: "composer-reasoning", hidden: true });
  const attachments = createAttachments({ dropTarget: goal, input: goal });
  // Approvals and goal mode are kept per browser and keep going is for the odd goal, so the three wait behind one button.
  const more = el("div", { class: "composer-more", hidden: true }, approvals.element, goalMode.element, standing.element, approvals.warning);
  const moreButton = el(
    "button",
    { class: "btn ghost sm", type: "button", "aria-expanded": "false", title: "Approvals, goal mode and keep going" },
    icon("sliders"),
    el("span", null, "Options"),
    approvalTag,
  );
  moreButton.addEventListener("click", () => {
    more.hidden = !more.hidden;
    moreButton.setAttribute("aria-expanded", String(!more.hidden));
  });
  const start = el(
    "button",
    { class: "btn primary send", type: "button", title: "Start (Enter)", "aria-label": "Start" },
    icon("arrowUp"),
    el("span", { class: "label" }, "Start"),
  );
  let providers = [];
  let shownDefault = NOTHING_SHOWN;

  // The model a run would use now: the one picked, else the daemon's default.
  function selectedInfo() {
    const ref = model.value === "" ? defaultRefOf(getSnapshot()) : model.value;
    return allModels(providers).find((candidate) => candidate.ref === ref) ?? null;
  }

  // The reasoning menu only exists for a model that offers more than one level.
  function paintReasoning() {
    const info = selectedInfo();
    if (info === null || !offersChoice(info)) {
      reasoning.hidden = true;
      reasoning.replaceChildren();
      return;
    }
    reasoning.replaceChildren(pick("Reasoning", reasoningSelect(info, "Reasoning level")));
    reasoning.hidden = false;
  }

  // The closed menu shows the model name only; its tooltip carries the whole reference.
  function paintModel() {
    const info = selectedInfo();
    model.title = info === null ? "" : info.ref;
    paintReasoning();
  }

  // Called on every status update; it only does work when the daemon's default model changed.
  function syncDefault() {
    const ref = defaultRefOf(getSnapshot());
    if (ref === shownDefault) {
      return;
    }
    shownDefault = ref;
    defaultOption.textContent = defaultOptionText(ref);
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
    start.disabled = true;
    try {
      await attempt(async () => {
        const created = await api.post("/runs", {
          goal: text,
          mode: mode.value(),
          model: model.value === "" ? null : model.value,
          effort: info !== null && offersChoice(info) ? savedLevel(info) : null,
          approvals: approvals.value(),
          goal_mode: mode.value() === "chat" || standing.value() ? null : goalMode.value(),
          standing: standing.value(),
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: agent.value === "" ? null : agent.value,
        });
        attachments.clear();
        ctx.navigate(`/runs/${created.id}`);
      });
    } finally {
      starting = false;
      start.disabled = false;
    }
  }

  start.addEventListener("click", submit);
  // Enter starts the run like it sends a message in a chat; Shift+Enter keeps a new line; "/" opens the slash menu,
  // whose commands here pick the kind of run.
  const composer = wireComposer(goal, {
    maxPx: MAX_GOAL_PX,
    send: submit,
    commands: MODES.map((option) => ({
      label: `/${option.id}`,
      hint: `${option.label}: ${option.hint}`,
      insert: "",
      run: () => {
        mode.set(option.id);
        modePicked(option.id);
      },
    })),
  });
  model.addEventListener("change", paintModel);
  loadModels().then(
    (loaded) => {
      providers = loaded;
      for (const group of toolGroups(loaded)) {
        model.append(
          el(
            "optgroup",
            { label: providerLabel(group.provider) },
            group.models.map((info) => el("option", { value: info.ref, title: info.ref }, modelName(info.ref))),
          ),
        );
      }
      paintModel();
    },
    (error) => {
      console.warn("the model list is unavailable", error);
      model.title = `The model list could not be loaded: ${error.message}`;
    },
  );
  syncDefault();

  // The status carries the daemon's defaults; the pickers show them until the person chose something else.
  function syncStatus() {
    syncDefault();
    approvals.sync();
    goalMode.sync();
  }

  const element = el(
    "section",
    { class: "card composer", "aria-label": "New chat" },
    composer.menu,
    goal,
    attachments.list,
    attachments.picker,
    el(
      "div",
      { class: "composer-bar" },
      el("div", { class: "composer-options" }, attachments.button, mode.element, agentPick, pick("Model", model), reasoning, moreButton),
      start,
    ),
    more,
    standing.note,
  );
  const stopStatus = onChange(syncStatus);
  return {
    element,
    focus: () => goal.focus(),
    dispose: () => {
      stopStatus();
      composer.dispose();
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
  const composer = buildComposer(ctx);
  const approvals = buildApprovals();
  const presence = buildPresence();
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
