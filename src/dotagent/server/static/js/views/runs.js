// The home page: Dot's presence (presence.js: the face in its state, a greeting, one line of now) and one composer in
// the middle of the window, the Today strip under them. The recent runs live in the sidebar; a run that waits for an
// answer is listed under Waits for you, and its card stays in its own chat (item 434).

import { actorFace, DOT_ID } from "../actors.js";
import { api } from "../api.js";
import { assistant, loadAssistant, onAssistant } from "../assistant.js";
import { messageText, readyToSend } from "../attachments.js";
import { AUTO_MODE, MESSAGE_MODES, optionRow, runChoices } from "../choices.js";
import { composerFrame, composerPill, NEW_CHAT_DRAFT } from "../composer.js";
import { el, modelName } from "../dom.js";
import { dotName } from "../face.js";
import { buildFirstChat } from "../firstchat.js";
import { forgetLevel, KEPT_SPEED, ONE_MESSAGE, savedLevel, saveLevel } from "../efforts.js";
import { icon } from "../icons.js";
import { narrowOr, PHONE, touchFirst, trackHeight } from "../layout.js";
import { LOOP_COMMAND, LOOP_HOW, LOOP_WORD, loopOf } from "../loops.js";
import { anchoredPopover } from "../popover.js";
import { readPref, removePref, writePref } from "../prefs.js";
import { buildAway } from "../away.js";
import { buildPresence } from "../presence.js";
import { providerLabel, providerOf } from "../providers.js";
import { KEPT_APPROVALS, KEPT_GOAL_MODE, standingPicker } from "../runmodes.js";
import { messageKeys } from "../sending.js";
import { onCompose } from "../shortcuts.js";
import { getSnapshot, loadModels, onChange } from "../state.js";
import { attempt, toast } from "../ui.js";

const MAX_GOAL_PX = 320;

// The home's choices are this browser's: a reasoning level (per model), fast mode, an approval mode and goal mode are
// kept until changed again. The mode, the model, the agent, Keep going and a choice for one message are the new chat's
// own (item 397): kept as they are made, with its draft (composer.js), and put away with it once a message goes, so
// that a new chat left for another page shows them again; a new chat starts on Auto (item 495), the daemon's default
// model, Dot, Keep going off and no choice for one message.
const HOME_CHOICES_KEY = "dot.home.choices";
const FRESH_CHOICES = { mode: AUTO_MODE.id, model: "", agent: "", standing: false, once: null };

/** The new chat's own choices as last kept, or `FRESH_CHOICES` when none are kept or what is kept is not theirs. */
function readHomeChoices() {
  const stored = readPref(HOME_CHOICES_KEY);
  if (stored === null) {
    return FRESH_CHOICES;
  }
  try {
    const { mode: kept, model, agent, standing, once } = JSON.parse(stored);
    // Task was a new chat's mode before Auto (item 495), which settles a task unless the words ask only to look.
    const mode = kept === "task" ? AUTO_MODE.id : kept;
    if (
      MESSAGE_MODES.some((option) => option.id === mode) &&
      typeof model === "string" &&
      typeof agent === "string" &&
      typeof standing === "boolean" &&
      (once === null || ONE_MESSAGE.some((choice) => choice.id === once))
    ) {
      return { mode, model, agent, standing, once };
    }
  } catch (error) {
    console.warn("the new chat's choices could not be read and are dropped", error);
  }
  removePref(HOME_CHOICES_KEY);
  return FRESH_CHOICES;
}

/** Whether a message of the mode `id` carries its goal out, as a campaign does: a task, and Auto, whose campaign is a
 * task (item 495). */
function actsOnGoal(id) {
  return id === "task" || id === AUTO_MODE.id;
}

/** Keep the new chat's own choices, or put them away when they are a new chat's first ones. */
function keepHomeChoices(chosen) {
  if (Object.entries(FRESH_CHOICES).every(([name, value]) => chosen[name] === value)) {
    removePref(HOME_CHOICES_KEY);
  } else {
    writePref(HOME_CHOICES_KEY, JSON.stringify(chosen));
  }
}

const HOME_KEPT = {
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

/** The words in the home's empty box, with the name the owner calls Dot by (item 420); a phone's line holds about thirty
 * characters, so it only says that the at sign and the slash open something. */
function placeholderFor(name) {
  return narrowOr(`Message ${name} (@ or /)`, `Message ${name} (@ for apps, / for skills and commands)`);
}

/** `listener` is the home's face (presence.js `listen`): it hears the box and the send. */
function buildComposer(ctx, listener) {
  const start = readHomeChoices();
  const frame = composerFrame({
    label: "New chat",
    boxLabel: "Goal",
    placeholder: placeholderFor(dotName()),
    maxPx: MAX_GOAL_PX,
    commands: [
      ...MESSAGE_MODES.map((option) => ({
        label: `/${option.id}`,
        hint: `${option.label}: ${option.hint}`,
        insert: "",
        run: () => choices.setMode(option.id),
      })),
      LOOP_COMMAND,
    ],
    send: submit,
    sendLabel: "Start",
    draftKey: NEW_CHAT_DRAFT,
    listener,
  });
  const { textarea: goal, attachments } = frame;
  // The name the box invites a message to, and the words again when the window crosses the phone's width.
  let called = dotName();
  const rephrase = () => {
    goal.placeholder = placeholderFor(called);
  };
  PHONE.addEventListener("change", rephrase);
  // The mode, the model with its reasoning and the Options (choices.js).
  const choices = runChoices({
    frame,
    modes: MESSAGE_MODES,
    kept: { ...HOME_KEPT, mode: start.mode, model: start.model, once: start.once },
    optionsTitle: "Approvals",
    more: () => [],
    onMode: modePicked,
    // HOME_KEPT keeps what it keeps as it is chosen; the new chat's own choices are kept here.
    onChange: keepChoices,
  });
  // A campaign checks its outcome itself, so goal mode does not apply to one; the switch only shows for a task. It is a
  // row of the Options menu's Execution group (item 353).
  const standing = standingPicker((on) => {
    choices.goalMode.setAvailable(!on);
    keepChoices();
  });
  choices.addExecutionItem(() => [standing.element]);
  // Only a task of Dot's keeps going, and goal mode waits while it does; a campaign sent on Auto is a task (item 495).
  function modePicked(id) {
    standing.setAvailable(actsOnGoal(id) && agentId === "");
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
  // Until the agents are listed (and when they cannot be), the new chat keeps the agent it had: a choice made meanwhile
  // must not forget it.
  let listed = false;
  function chosenAgent() {
    return agents.find((candidate) => candidate.id === agentId) ?? null;
  }
  function paintAgent() {
    const chosen = chosenAgent();
    const name = chosen === null ? "Dot" : chosen.name;
    called = name;
    rephrase();
    agentPill.setValue(chosen === null ? "Dot" : chosen.name, "");
    agentPill.setIcon(chosen === null ? actorFace(DOT_ID, "idle", null) : actorFace(chosen.id, "idle", chosen.look));
    standing.setAvailable(chosen === null && actsOnGoal(choices.value().mode));
    agentMenu.repaint();
  }
  function pickAgent(id) {
    agentId = id;
    agentMenu.close();
    paintAgent();
    keepChoices();
    goal.focus();
  }
  function keepChoices() {
    const chosen = choices.value();
    const agent = listed ? agentId : start.agent;
    keepHomeChoices({ mode: chosen.mode, model: chosen.model ?? "", agent, standing: standing.value(), once: choices.armed() });
  }
  // Keep going as the new chat left it, where a task of Dot's may keep going; its agent once the agents are listed.
  if (start.standing && actsOnGoal(start.mode) && start.agent === "") {
    standing.set(true);
  }
  standing.setAvailable(actsOnGoal(start.mode) && start.agent === "");
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
      listed = true;
      agentPill.element.hidden = agents.length === 0;
      // The agent the new chat had, while it is still one of them; its choices then keep the one shown.
      if (start.agent !== "") {
        agentId = agents.some((record) => record.id === start.agent) ? start.agent : "";
        paintAgent();
        keepChoices();
      }
      agentMenu.repaint();
    })
    .catch((error) => {
      console.warn("the agents could not be listed", error);
    });

  // True from the moment a run is being started until the answer is in: Enter does not look at the disabled button, so
  // a second press must be refused here, or one goal would start two runs.
  let starting = false;
  const keys = messageKeys();
  // The approval mode a send from outside the box asks for, in place of the box's choice; null otherwise.
  let sentApprovals = null;

  async function submit() {
    if (starting) {
      return;
    }
    // A message of files alone goes too (item 445): a pasted text or a picture sent without a word typed.
    const text = messageText(goal.value, attachments);
    if (text === "") {
      goal.focus();
      return;
    }
    // "/loop 30m <message>" (item 400): the message starts the chat, and a schedule of the chat asks it again on that
    // rhythm, as "/loop" in the box of a run makes one (item 281).
    const loop = loopOf(text);
    if (loop === null && LOOP_WORD.test(text)) {
      toast("error", LOOP_HOW);
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
          goal: loop === null ? text : loop.message,
          mode: chosen.mode,
          model: chosen.model,
          effort: chosen.effort,
          speed: chosen.speed,
          once: chosen.once,
          approvals: sentApprovals ?? chosen.approvals,
          goal_mode: standing.value() ? null : chosen.goal_mode,
          standing: standing.value(),
          thread_id: null,
          attachments: attachments.ids(),
          agent_id: agentId === "" ? null : agentId,
          loop: loop === null ? null : loop.every,
          ...frame.picks.body(),
        };
        const created = await api.post("/runs", { ...body, key: keys.of("run", body) });
        keys.sent();
        choices.sent();
        frame.picks.sent();
        frame.clear();
        removePref(HOME_CHOICES_KEY);
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
    // Send `text` as the owner would, with the choices of the box but the approval mode `approvals` (the first
    // conversation's look, item 420, which starts in the mode its promises were said in).
    send: async (text, approvals) => {
      goal.value = text;
      sentApprovals = approvals;
      try {
        await submit();
      } finally {
        sentApprovals = null;
      }
    },
    rename: (name) => {
      called = name;
      rephrase();
    },
    dispose: () => {
      PHONE.removeEventListener("change", rephrase);
      stopStatus();
      choices.dispose();
      agentMenu.dispose();
      frame.dispose();
    },
  };
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

/** Whether a first open is due its first conversation (item 420): Dot has not introduced itself, and there is no
 * chat yet. */
async function firstChatDue() {
  const names = assistant() ?? (await loadAssistant());
  if (names.introduced) {
    return false;
  }
  const chats = await api.get("/threads?limit=1");
  return chats.length === 0;
}

export function mount(root, ctx) {
  const presence = buildPresence();
  let first = null;
  // A message the owner sends ends the first conversation too.
  const listen = {
    ...presence.listen,
    sending: () => {
      presence.listen.sending();
      first?.sent();
    },
  };
  const composer = buildComposer(ctx, listen);
  const setup = buildSetup();
  // The setup card stands over the greeting, in the block the page keeps in the middle. What a run waits for is
  // answered in its own chat (item 434): the home lists it under Waits for you, which leads there.
  const start = el("div", { class: "home-start" }, setup.element, presence.element, composer.element);
  // What schedules and campaigns did while the owner was away (item 466) stands over the Today strip.
  const away = buildAway();
  const home = el("div", { class: "home" }, el("h1", { class: "visually-hidden" }, "New chat"), start, away.element, presence.strip);
  root.append(home);
  // The greeting and the composer stand in the middle of the space under the top bar (item 124): the row above them
  // takes half of what their height leaves of it (app.css), so the page keeps that height as it changes.
  const stopCentring = trackHeight(start, home, "--home-start-h");
  const stopSetup = onChange(setup.sync);
  // On a touch screen the caret waits for a tap: a box that takes the focus opens the keyboard over the page (item 293).
  function offerCaret() {
    if (!touchFirst()) {
      composer.focus();
    }
  }
  const stopCompose = onCompose(offerCaret);
  offerCaret();
  const stopNames = onAssistant((names) => {
    composer.rename(names.name);
    presence.renamed();
  });
  let disposed = false;
  // The first conversation stands in the place of the greeting, over the composer, once it is known to be due; a
  // daemon that cannot say whether it is is shown, and the home keeps its greeting.
  void attempt(async () => {
    const due = await firstChatDue();
    if (!due || disposed) {
      return;
    }
    first = buildFirstChat({
      startLook: (goal, approvals) => composer.send(goal, approvals),
      onSkipped: () => {
        first.element.replaceWith(presence.element);
        first.dispose();
        first = null;
      },
    });
    presence.element.replaceWith(first.element);
  });
  return () => {
    disposed = true;
    first?.dispose();
    stopNames();
    stopCentring();
    stopSetup();
    stopCompose();
    composer.dispose();
    presence.dispose();
    away.dispose();
  };
}
