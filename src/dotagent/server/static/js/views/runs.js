// The home page: Dot's presence (presence.js: the face in its state, a greeting, one line of now) and one composer in
// the middle of the window, the Today strip under them. The recent runs live in the sidebar; a run that waits for an
// answer shows above, as a card.

import { actorFace, DOT_ID } from "../actors.js";
import { approvalCard, fromRecord, LEFT_IN_RUN } from "../approvals.js";
import { api } from "../api.js";
import { askForWords, readyToSend } from "../attachments.js";
import { MODES, optionRow, runChoices } from "../choices.js";
import { composerFrame, composerPill, NEW_CHAT_DRAFT } from "../composer.js";
import { el, modelName } from "../dom.js";
import { forgetLevel, KEPT_SPEED, savedLevel, saveLevel } from "../efforts.js";
import { icon } from "../icons.js";
import { touchFirst, trackHeight } from "../layout.js";
import { anchoredPopover } from "../popover.js";
import { buildPresence } from "../presence.js";
import { providerLabel, providerOf } from "../providers.js";
import { KEPT_APPROVALS, KEPT_GOAL_MODE, standingPicker } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { onCompose } from "../shortcuts.js";
import { getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt, toast } from "../ui.js";

const MAX_GOAL_PX = 320;

// The home's choices are this browser's: the mode starts at Task, the model at the daemon's default, and a reasoning
// level, an approval mode and goal mode are kept per browser until changed again.
const HOME_KEPT = {
  mode: "task",
  model: "",
  level: {
    read: savedLevel,
    write: (info, level) => {
      if (level === null) {
        forgetLevel(info);
      } else {
        saveLevel(info, level);
      }
    },
  },
  speed: KEPT_SPEED,
  approvals: KEPT_APPROVALS,
  goalMode: KEPT_GOAL_MODE,
};

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
      run: () => choices.setMode(option.id),
    })),
    send: submit,
    sendLabel: "Start",
    draftKey: NEW_CHAT_DRAFT,
    listener,
  });
  const { textarea: goal, attachments } = frame;
  // The mode, the model with its reasoning and the Options (choices.js).
  const choices = runChoices({
    frame,
    kept: HOME_KEPT,
    optionsTitle: "Approvals",
    more: () => [],
    onMode: modePicked,
    // HOME_KEPT keeps what it keeps as it is chosen.
    onChange: () => {},
  });
  // A campaign checks its outcome itself, so goal mode does not apply to one; the switch only shows for a task. It is a
  // row of the "+" menu after Goal mode's (item 353).
  const standing = standingPicker((on) => choices.goalMode.setAvailable(!on));
  frame.addMenuItems(() => [standing.element]);
  // Only a task of Dot's keeps going, and goal mode waits while it does.
  function modePicked(id) {
    standing.setAvailable(id === "task" && agentId === "");
    choices.goalMode.setAvailable(id !== "chat" && !standing.value());
    goal.focus();
  }
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
    standing.setAvailable(chosen === null && choices.value().mode === "task");
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

  // True from the moment a run is being started until the answer is in: Enter does not look at the disabled button, so
  // a second press must be refused here, or one goal would start two runs.
  let starting = false;
  const keys = messageKeys();

  async function submit() {
    if (starting) {
      return;
    }
    const text = goal.value.trim();
    if (text === "") {
      askForWords(attachments);
      goal.focus();
      return;
    }
    if (!readyToSend(attachments)) {
      return;
    }
    starting = true;
    frame.sendButton.disabled = true;
    listener.sending();
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
          goal_mode: standing.value() ? null : chosen.goal_mode,
          standing: standing.value(),
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: agentId === "" ? null : agentId,
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

  const { pills } = choices;
  frame.options.append(pills.mode, agentPill.element, pills.model, pills.reasoning, pills.options);
  const stopStatus = onChange(choices.sync);
  return {
    // Keep going's line sits under the frame, not in it.
    element: el("div", { class: "composer-stack" }, frame.element, standing.note),
    focus: () => goal.focus(),
    dispose: () => {
      stopStatus();
      choices.dispose();
      agentMenu.dispose();
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
          onSettled: (decided) => {
            // The card shows the decision at once; the refresh then takes it away.
            if (decided !== null) {
              card.settle(decided.status, decided.note);
            }
            refresh().catch((error) => toast("error", `The approvals could not be refreshed: ${error.message}`));
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

/** Why the default model `main` cannot answer, as the providers' lists say (`loadModels`), or null when its provider
 * lists it. */
function mainProblem(providers, main) {
  const provider = providers.find((candidate) => candidate.provider === providerOf(main));
  const label = providerLabel(provider.provider);
  if (provider.problem === "no_key") {
    return `No ${label} key is set.`;
  }
  if (provider.problem === "key_refused") {
    return `${label} refused the key.`;
  }
  if (provider.error !== null) {
    return provider.error;
  }
  return provider.models.some((model) => model.ref === main) ? null : `${label} does not offer it.`;
}

/**
 * The card that leads to a model while the default one cannot answer (item 333): its provider listed no models, or not
 * this one. Its ways are Settings › Models, Settings › Providers and `dotx setup`. It reads the list the composer reads,
 * and goes once the default model is one its provider lists; `data-state` says what it found (`ready` or `needed`).
 */
function buildSetup() {
  const what = el("p", null);
  const why = el("p", { class: "hint" });
  const element = el(
    "section",
    { class: "card home-setup", hidden: true, "aria-labelledby": "home-setup-title" },
    el("div", { class: "card-head" }, icon("alert"), el("span", { id: "home-setup-title", class: "grow" }, "Set up a model")),
    el(
      "div",
      { class: "card-pad home-setup-body" },
      what,
      why,
      el(
        "div",
        { class: "row home-setup-ways" },
        el("a", { class: "btn primary", href: "#/settings/models" }, "Choose a model"),
        el("a", { class: "btn", href: "#/settings/keys" }, "Add a key"),
      ),
      el("p", { class: "hint" }, "Or run ", el("code", null, "dotx setup"), " in Terminal: it checks this Mac, stores a key and chooses the model."),
    ),
  );
  let providers = null;

  function sync(snapshot) {
    if (providers === null || snapshot.status === null) {
      return;
    }
    const main = snapshot.status.models.main;
    const problem = mainProblem(providers, main);
    element.dataset.state = problem === null ? "ready" : "needed";
    element.hidden = problem === null;
    if (problem !== null) {
      what.textContent = `New chats start on ${modelName(main)} from ${providerLabel(providerOf(main))}, which cannot answer now.`;
      why.textContent = problem;
    }
  }

  loadModels()
    .then((found) => {
      providers = found;
      sync(getSnapshot());
    })
    .catch((error) => {
      console.warn("the model list is unavailable", error);
    });
  return { element, sync };
}

export function mount(root, ctx) {
  const presence = buildPresence();
  const composer = buildComposer(ctx, presence.listen);
  const approvals = buildApprovals();
  const setup = buildSetup();
  // The setup card stands over the greeting, in the block the page keeps in the middle.
  const start = el("div", { class: "home-start" }, setup.element, presence.element, composer.element);
  const home = el("div", { class: "home" }, el("h1", { class: "visually-hidden" }, "New chat"), approvals.element, start, presence.strip);
  root.append(home);
  // The greeting and the composer stand in the middle of the space under the top bar (item 124): the row above them
  // takes half of what their height leaves of it (app.css), so the page keeps that height as it changes.
  const stopCentring = trackHeight(start, home, "--home-start-h");
  approvals.sync(getSnapshot());
  const stopApprovals = onChange(approvals.sync);
  const stopSetup = onChange(setup.sync);
  // On a touch screen the caret waits for a tap: a box that takes the focus opens the keyboard over the page (item 293).
  function offerCaret() {
    if (!touchFirst()) {
      composer.focus();
    }
  }
  const stopCompose = onCompose(offerCaret);
  offerCaret();
  return () => {
    stopCentring();
    stopApprovals();
    stopSetup();
    stopCompose();
    composer.dispose();
    presence.dispose();
  };
}
