// The choices of a run besides its goal, as the pills of a composer (item 108): the mode, the model with its reasoning
// and the Options (approvals and goal mode), each with its menu. The home's box and a chat's dock (item 133) both wear
// them; where they start and where a choice is kept is the box's own (`kept`): this browser's on the home, the chat's
// on a chat's page, which the daemon keeps for its next follow-up (item 199). The model's menu is modelpicker.js
// (item 208), which makes the daemon's default too (item 209).

import { api } from "./api.js";
import { composerPill } from "./composer.js";
import { el, modelName } from "./dom.js";
import { allModels, levelLabel, offersChoice, ONE_MESSAGE, oneMessageChoices, SPEED_HINTS } from "./efforts.js";
import { icon } from "./icons.js";
import { modelPicker } from "./modelpicker.js";
import { anchoredPopover } from "./popover.js";
import { providerMark, providerOf } from "./providers.js";
import { approvalBadge, approvalLabel, approvalsPicker, goalModePicker } from "./runmodes.js";
import { getSnapshot, loadModels, refresh } from "./state.js";
import { attempt, toast, toastError } from "./ui.js";

const MODES = [
  { id: "task", label: "Task", hint: "Do the job from start to finish on this Mac", icon: "target" },
  { id: "chat", label: "Chat", hint: "Talk it through; Dot still uses apps and files when that helps", icon: "message" },
  { id: "research", label: "Research", hint: "Look things up and report back. Changes nothing", icon: "search" },
];
// Auto (item 495), a message's mode on the home and in a chat: the message's own words settle it (the daemon's
// agent/mode_words.py), so the box shows no mode pill until one is named with /task, /chat or /research.
export const AUTO_MODE = { id: "auto", label: "Auto", hint: "Dot decides: it only looks when you ask it to look, and otherwise does the job", icon: "sparkles" };
// The modes a message may ask for: Auto first, then each mode by name.
export const MESSAGE_MODES = [AUTO_MODE, ...MODES];
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;
// What the pills add to their tooltips while a run works on a chat's page: a message waits in the queue and starts
// with them once that run, which keeps its own, has ended (item 148).
const WAITING_NOTE = "For your next message: the task under way keeps its own";

/** Where `word` stands in `text` as a whole word of its own, any case, or -1. */
function wordAt(text, word) {
  const match = new RegExp(`(?<![\\p{L}\\p{N}_])${word}(?![\\p{L}\\p{N}_])`, "iu").exec(text);
  return match === null ? -1 : match.index;
}

function defaultRefOf(snapshot) {
  return snapshot.status === null ? null : snapshot.status.models.main;
}

/** The models that can call tools, grouped by provider: runs need tools, so the others would only fail. */
function toolGroups(providers) {
  return providers
    .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools) }))
    .filter((group) => group.models.length > 0);
}

/** The option row of a popover's list: `lead` (a mark or an icon, or null), the label with its `hint` under it (or ""),
 * and a tick when it is the one chosen. */
export function optionRow({ lead, label, hint, selected, onPick }) {
  return el(
    "button",
    { class: "pop-option", type: "button", role: "option", "aria-selected": String(selected), onclick: onPick },
    lead,
    el("span", { class: "pop-option-text" }, el("span", null, label), hint === "" ? null : el("span", { class: "pop-option-hint" }, hint)),
    icon("check"),
  );
}

/**
 * The pills of a run's choices for the composer `frame` (composer.js), whose frame bounds their menus. `modes` are the
 * modes it offers (`MESSAGE_MODES`, Auto among them, item 495, on an agent's page too since item 513). `kept` is where
 * the box starts and keeps them: `mode` (an id of `modes`), `model` (a reference, or "" for the daemon's default),
 * `level` (`read(info)`, the reasoning level chosen for a model, or null for the daemon's; `write(info, level)`, null to
 * go back to the daemon's), `speed` (`read()`, "fast" or "standard"; `write(speed)`: fast mode, which a run asks for
 * only of a model that has it, item 223), `approvals` and `goalMode` (as `approvalsPicker` and `goalModePicker` take
 * them), and `once` (the id of a choice for one message armed as the box opens, or null; item 397).
 * `optionsTitle` is the Options pill's tooltip, `more()` the lines its menu shows after the approvals, `onMode(id)` runs
 * after a mode was chosen, and `onChange()` after any choice was made (`value()` has it then). Goal mode's switch is a
 * row of the Options menu's Execution group (`addExecutionItem`, item 353), and a chat, which has neither, hides it
 * and Plan first.
 *
 * Returns `pills` (`{ mode, model, reasoning, options }`, the elements), `setMode(id)`, `goalMode` (its picker),
 * `ready()` (resolves once the models are loaded), `value()` (what the next message starts its run with: `{ mode, model,
 * effort, speed, approvals, goal_mode, once }`, `once` the id of a choice for one message, item 251, whose level the
 * daemon asks for instead of `effort`), `kept()` (the same without `once`, as a chat keeps them), `armed()` (the id of
 * the choice for one message armed from the menu, or null, not the word in the box), `sent()` once a
 * message went, which puts the choice for one message away, `sync()` after a status change, `setWaiting(on)` (while a
 * run works, the choices wait for the next follow-up and the pills say so), `setWords(on)` (whether the word ultrathink
 * or ultracode in the box arms its choice, item 259) and `dispose()`.
 */
export function runChoices({ frame, modes, kept, optionsTitle, more, onMode, onChange }) {
  let waiting = false;

  // A choice of the Options is kept as `kept` keeps it, and told as the others are.
  function told(choice) {
    return {
      read: choice.read,
      write: (value) => {
        choice.write(value);
        onChange();
      },
    };
  }

  // A pill's tooltip, which says that the choice waits while it does.
  function setTitle(pill, text) {
    pill.dataset.title = text;
    pill.title = waiting ? (text === "" ? WAITING_NOTE : `${text}\n${WAITING_NOTE}`) : text;
  }

  // The approval mode stays in sight as the Options pill's value and icon, since it decides whether the run asks before
  // it acts.
  const optionsPill = composerPill({ id: "options", caption: "Options", value: "", icon: null });
  setTitle(optionsPill.element, optionsTitle);
  // The pill wears the mode's shorter name and its icon (item 398), its tooltip the whole name.
  const approvals = approvalsPicker((shown) => {
    const badge = shown === null ? null : approvalBadge(shown);
    optionsPill.setValue(badge === null ? "" : badge.short, "");
    optionsPill.setIcon(icon(badge === null ? "lock" : badge.icon));
    setTitle(optionsPill.element, shown === null ? optionsTitle : `${optionsTitle}: ${approvalLabel(shown)}`);
  }, told(kept.approvals));
  const goalMode = goalModePicker(told(kept.goalMode));
  const executionItems = [];
  function addExecutionItem(build) {
    executionItems.push(build);
  }
  const executionGroup = el(
    "div",
    { class: "pop-group pop-execution", role: "group", "aria-label": "How Dot works" },
  );
  const optionsMenu = anchoredPopover({
    anchor: optionsPill.element,
    frame: frame.element,
    label: "Options",
    build: () => {
      const isChat = modeId === "chat";
      executionGroup.hidden = isChat;
      executionGroup.replaceChildren(
        el("div", { class: "pop-heading" }, "How Dot works"),
        ...executionItems.flatMap((build) => build()),
        goalMode.element,
        frame.picks.planRow(),
      );
      return [
        approvals.element,
        ...(isChat ? [] : [executionGroup]),
        ...more(),
      ];
    },
  });

  // The mode (item 108): a pill with the mode's icon, its menu the modes with what each does. A chat has no goal to
  // check, so the goal switch only belongs to the other kinds of run. On Auto (item 495) the pill is away: the box
  // shows a mode only once one is named, and its menu leads back to Auto.
  const modePill = composerPill({ id: "mode", caption: "Mode", value: "", icon: null });
  let modeId = kept.mode;
  function paintMode() {
    const option = modes.find((candidate) => candidate.id === modeId);
    modePill.setValue(option.label, "");
    modePill.setIcon(icon(option.icon));
    setTitle(modePill.element, option.hint);
    modePill.element.hidden = modeId === AUTO_MODE.id;
    modeMenu.repaint();
  }
  function showMode(id) {
    modeId = id;
    paintMode();
    goalMode.setAvailable(id !== "chat");
    frame.picks.setPlanAvailable(id !== "chat");
    executionGroup.hidden = id === "chat";
  }
  function setMode(id) {
    showMode(id);
    onMode(id);
    onChange();
  }
  function pickMode(id) {
    showMode(id);
    modeMenu.close();
    onMode(id);
    onChange();
  }
  const modeMenu = anchoredPopover({
    anchor: modePill.element,
    frame: frame.element,
    label: "Mode",
    build: () => [
      el(
        "div",
        { role: "listbox", "aria-label": "Mode" },
        ...modes.map((option) =>
          optionRow({ lead: icon(option.icon), label: option.label, hint: option.hint, selected: option.id === modeId, onPick: () => pickMode(option.id) }),
        ),
      ),
    ],
  });
  paintMode();
  goalMode.setAvailable(modeId !== "chat");
  frame.picks.setPlanAvailable(modeId !== "chat");
  executionGroup.hidden = modeId === "chat";

  // The model: the daemon's default until one is picked, worn with its provider's mark (item 108).
  const modelPill = composerPill({ id: "model", caption: "Model", value: "", icon: null });
  // The reasoning level of a model that offers a choice, and its speed when it has fast mode; hidden for the others.
  const reasoningPill = composerPill({ id: "reasoning", caption: "Reasoning", value: "", icon: icon("brain") });
  setTitle(reasoningPill.element, "Reasoning level");
  reasoningPill.element.hidden = true;
  let chosenRef = kept.model;
  let providers = [];
  let shownDefault = NOTHING_SHOWN;
  // The choice for one message armed (item 251), an id of ONE_MESSAGE (efforts.js), or null: the next message goes with
  // it, and the one after with the kept level again.
  let once = kept.once;
  // The choice the word in the box named when the frame was last painted (item 259), and whether a word may arm one:
  // not while the box edits a queued message, whose save changes its text alone, nor while its send steers the run
  // under way, which keeps its level (`setWords`).
  let typedShown = null;
  let wordsOn = true;

  // The choice for one message a word of the box names (item 259): `ultrathink` or `ultracode` as a whole word of its
  // own, any case, the first of the two when both are there, or null. The word stays in the text, as in Claude Code.
  function typedChoice() {
    if (!wordsOn) {
      return null;
    }
    const text = frame.textarea.value;
    const found = ONE_MESSAGE.map((choice) => ({ id: choice.id, at: wordAt(text, choice.id) })).filter((hit) => hit.at >= 0);
    found.sort((first, second) => first.at - second.at);
    return found.length === 0 ? null : found[0].id;
  }

  // The model a run would use now: the one picked, else the daemon's default.
  function selectedInfo() {
    const ref = chosenRef === "" ? defaultRefOf(getSnapshot()) : chosenRef;
    return allModels(providers).find((candidate) => candidate.ref === ref) ?? null;
  }

  // Whether a run would ask the model `info` for fast mode: it has it, and fast was chosen.
  function fastOn(info) {
    return info?.fast_mode === true && kept.speed.read() === "fast";
  }

  // The choice for one message the next message goes with: the one armed by the menu, else the one the word in the box
  // names, when the model `info` offers it.
  function onceFor(info) {
    const id = once ?? typedChoice();
    return info === null || id === null ? null : (oneMessageChoices(info).find((choice) => choice.id === id) ?? null);
  }

  // The Model pill shows the mark and the name, its tooltip the whole reference; the Reasoning pill the level chosen,
  // or the one the daemon would use, or the choice for one message armed, and Fast while fast mode is on.
  function paintModel() {
    const info = selectedInfo();
    const ref = chosenRef === "" ? defaultRefOf(getSnapshot()) : chosenRef;
    modelPill.setValue(ref === null ? "Default" : modelName(ref), "");
    modelPill.setIcon(providerMark(ref === null ? "" : providerOf(ref)));
    setTitle(modelPill.element, info === null ? "" : info.ref);
    const choice = info !== null && offersChoice(info);
    const shown = choice || info?.fast_mode === true;
    reasoningPill.element.hidden = !shown;
    // The frame wears the choice for one message while it is armed (item 259): its border is the gradient.
    const boost = onceFor(info);
    typedShown = typedChoice();
    if (boost === null) {
      delete frame.element.dataset.boost;
    } else {
      frame.element.dataset.boost = boost.id;
    }
    if (shown) {
      const level = choice ? (kept.level.read(info) ?? info.default_effort) : null;
      const words = boost !== null ? boost.label : choice ? (level === null ? "Default" : levelLabel(level)) : "";
      reasoningPill.setValue(fastOn(info) ? (words === "" ? "Fast" : `${words} · Fast`) : words, "");
      reasoningPill.setIcon(icon(boost !== null ? boost.icon : fastOn(info) ? "bolt" : "brain"));
      const typed = boost !== null && once === null ? " (the word in the box)" : "";
      setTitle(reasoningPill.element, boost === null ? "Reasoning level" : `${boost.label}: ${boost.hint}${typed}`);
    } else {
      reasoningMenu.close();
    }
    modelMenu.repaint();
    reasoningMenu.repaint();
  }

  function pickModel(ref) {
    chosenRef = ref;
    // A choice for one message the new model does not offer is put away, not kept for another model.
    if (onceFor(selectedInfo()) === null) {
      once = null;
    }
    modelMenu.close();
    paintModel();
    modelPill.element.focus();
    onChange();
  }

  function pickLevel(info, level) {
    kept.level.write(info, level);
    once = null;
    reasoningMenu.close();
    paintModel();
    reasoningPill.element.focus();
    onChange();
  }

  // A choice for one message stays armed until a message goes, or until it is picked again.
  function pickOnce(id) {
    once = once === id ? null : id;
    reasoningMenu.close();
    paintModel();
    reasoningPill.element.focus();
    onChange();
  }

  function pickSpeed(speed) {
    kept.speed.write(speed);
    reasoningMenu.close();
    paintModel();
    reasoningPill.element.focus();
    onChange();
  }

  // The levels of a model that offers a choice, the choices for one message it offers (item 251), then the speeds of
  // one with fast mode (item 223).
  function reasoningRows() {
    const info = selectedInfo();
    if (info === null) {
      return [];
    }
    const rows = [];
    if (offersChoice(info)) {
      const chosen = kept.level.read(info);
      const fallback = info.default_effort === null ? "Default" : `Default (${levelLabel(info.default_effort)})`;
      rows.push(
        el(
          "div",
          { role: "listbox", "aria-label": "Reasoning" },
          optionRow({ lead: null, label: fallback, hint: "Dot's own choice", selected: chosen === null, onPick: () => pickLevel(info, null) }),
          ...info.efforts.map((level) => optionRow({ lead: null, label: levelLabel(level), hint: "", selected: chosen === level, onPick: () => pickLevel(info, level) })),
        ),
      );
    }
    const single = oneMessageChoices(info);
    if (single.length > 0) {
      rows.push(
        el(
          "div",
          { class: "pop-group", role: "group", "aria-label": "This message only" },
          el("div", { class: "pop-heading", "aria-hidden": "true" }, "This message only"),
          el(
            "div",
            { role: "listbox", "aria-label": "This message only" },
            ...single.map((choice) =>
              optionRow({ lead: icon(choice.icon), label: choice.label, hint: choice.hint, selected: (once ?? typedChoice()) === choice.id, onPick: () => pickOnce(choice.id) }),
            ),
          ),
        ),
      );
    }
    if (info.fast_mode) {
      const fast = fastOn(info);
      rows.push(
        el(
          "div",
          { class: "pop-group", role: "group", "aria-label": "Speed" },
          el("div", { class: "pop-heading", "aria-hidden": "true" }, "Speed"),
          el(
            "div",
            { role: "listbox", "aria-label": "Speed" },
            optionRow({ lead: null, label: "Standard", hint: SPEED_HINTS.standard, selected: !fast, onPick: () => pickSpeed("standard") }),
            optionRow({ lead: null, label: "Fast", hint: SPEED_HINTS.fast, selected: fast, onPick: () => pickSpeed("fast") }),
          ),
        ),
      );
    }
    return rows;
  }

  // The daemon's default, made from the model's menu as from the Settings (item 209): told as made, or why it was not;
  // resolves to whether the daemon took it. The chat keeps its own model; a chat that follows the default moves with it.
  async function makeDefault(ref) {
    try {
      await api.post("/models/main", { model: ref });
    } catch (error) {
      console.error("the default model could not be changed", error);
      toastError(error);
      return false;
    }
    toast("ok", `New chats start on ${modelName(ref)}.`);
    await attempt(refresh);
    return true;
  }

  // The model's menu (item 208): the favourites and the providers on a rail, a search, a star on each model.
  const picker = modelPicker({ onPick: pickModel, onMakeDefault: makeDefault });
  const modelMenu = anchoredPopover({
    anchor: modelPill.element,
    frame: frame.element,
    label: "Model",
    build: () => picker.build({ groups: toolGroups(providers), chosen: chosenRef, fallback: defaultRefOf(getSnapshot()) }),
  });
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

  async function ready() {
    providers = await loadModels();
    paintModel();
  }

  ready().catch((error) => {
    console.warn("the model list is unavailable", error);
    setTitle(modelPill.element, `The model list could not be loaded: ${error.message}`);
  });
  syncDefault();

  // The choices kept for the next message, as a chat keeps them.
  function keptNow() {
    const info = selectedInfo();
    const level = info !== null && offersChoice(info) ? kept.level.read(info) : null;
    return {
      mode: modeId,
      model: chosenRef === "" ? null : chosenRef,
      effort: level,
      speed: fastOn(info) ? "fast" : "standard",
      approvals: approvals.value(),
      goal_mode: modeId === "chat" ? null : goalMode.value(),
    };
  }

  // The word in the box arms or puts away the choice as it is typed or cleared; the frame is painted when it changed.
  const unwatch = frame.watchText(() => {
    if (typedChoice() !== typedShown) {
      paintModel();
    }
  });

  const pills = { mode: modePill.element, model: modelPill.element, reasoning: reasoningPill.element, options: optionsPill.element };
  return {
    pills,
    setMode,
    goalMode,
    addExecutionItem,
    ready,
    value: () => {
      const boost = onceFor(selectedInfo());
      return { ...keptNow(), once: boost === null ? null : boost.id };
    },
    kept: keptNow,
    armed: () => once,
    sent: () => {
      once = null;
      paintModel();
    },
    // The status carries the daemon's defaults; the pickers show them until the person chose something else.
    sync: () => {
      syncDefault();
      approvals.sync();
      goalMode.sync();
    },
    setWords: (on) => {
      wordsOn = on;
      if (typedChoice() !== typedShown) {
        paintModel();
      }
    },
    setWaiting: (on) => {
      waiting = on;
      for (const pill of Object.values(pills)) {
        pill.toggleAttribute("data-waiting", on);
        setTitle(pill, pill.dataset.title ?? "");
      }
    },
    dispose: () => {
      unwatch();
      optionsMenu.dispose();
      modeMenu.dispose();
      modelMenu.dispose();
      reasoningMenu.dispose();
    },
  };
}
