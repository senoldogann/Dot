// The choices of a run besides its goal, as the pills of a composer (item 108): the mode, the model with its reasoning
// and the Options (approvals and goal mode), each with its menu. The home's box and a chat's dock (item 133) both wear
// them; where they start and where a choice is kept is the box's own (`kept`): this browser's on the home, the open
// run's on a chat's page, held by the page for the next follow-up.

import { composerPill } from "./composer.js";
import { el, modelName } from "./dom.js";
import { allModels, levelLabel, offersChoice } from "./efforts.js";
import { icon } from "./icons.js";
import { anchoredPopover } from "./popover.js";
import { providerLabel, providerMark, providerOf } from "./providers.js";
import { approvalLabel, approvalsPicker, goalModePicker } from "./runmodes.js";
import { getSnapshot, loadModels } from "./state.js";

export const MODES = [
  { id: "task", label: "Task", hint: "Carry the goal out end to end on this Mac", icon: "target" },
  { id: "chat", label: "Chat", hint: "Converse; use tools whenever they help", icon: "message" },
  { id: "research", label: "Research", hint: "Read-only: gather information and report, change nothing", icon: "search" },
];
// The Options pill's icon by approval mode: closed for Ask, a guard for Auto (a review model answers), open for YOLO.
const APPROVAL_ICONS = { ask: "lock", auto: "shield", yolo: "lockOpen" };
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;
// What the pills add to their tooltips while a run works on a chat's page: a message steers that run, which keeps its own.
const WAITING_NOTE = "For the next follow-up: the run under way keeps its own";

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
 * The pills of a run's choices for the composer `frame` (composer.js), whose frame bounds their menus. `kept` is where
 * the box starts and keeps them: `mode` (an id of `MODES`), `model` (a reference, or "" for the daemon's default),
 * `level` (`read(info)`, the reasoning level chosen for a model, or null for the daemon's; `write(info, level)`, null to
 * go back to the daemon's), `approvals` and `goalMode` (as `approvalsPicker` and `goalModePicker` take them).
 * `optionsTitle` is the Options pill's tooltip, `more()` the lines its menu shows after goal mode, and `onMode(id)` runs
 * after a mode was chosen.
 *
 * Returns `pills` (`{ mode, model, reasoning, options }`, the elements), `setMode(id)`, `goalMode` (its picker),
 * `ready()` (resolves once the models are loaded), `value()` (what a run starts with: `{ mode, model, effort, approvals,
 * goal_mode }`), `sync()` after a status change, `setWaiting(on)` (while a run works, the choices wait for the next
 * follow-up and the pills say so) and `dispose()`.
 */
export function runChoices({ frame, kept, optionsTitle, more, onMode }) {
  let waiting = false;

  // A pill's tooltip, which says that the choice waits while it does.
  function setTitle(pill, text) {
    pill.dataset.title = text;
    pill.title = waiting ? (text === "" ? WAITING_NOTE : `${text}\n${WAITING_NOTE}`) : text;
  }

  // The approval mode stays in sight as the Options pill's value and icon, since it decides whether the run asks before
  // it acts.
  const optionsPill = composerPill({ id: "options", caption: "Options", value: "", icon: null });
  setTitle(optionsPill.element, optionsTitle);
  const approvals = approvalsPicker((shown) => {
    optionsPill.setValue(shown === null ? "" : approvalLabel(shown), "");
    optionsPill.setIcon(icon(shown === null ? "lock" : APPROVAL_ICONS[shown]));
    optionsPill.setTone(shown === "yolo" ? "warn" : null);
  }, kept.approvals);
  const goalMode = goalModePicker(kept.goalMode);
  const optionsMenu = anchoredPopover({
    anchor: optionsPill.element,
    frame: frame.element,
    label: "Options",
    build: () => [approvals.element, goalMode.element, ...more(), approvals.warning],
  });

  // The mode (item 108): a pill with the mode's icon, its menu the three modes with what each does. A chat has no goal
  // to check, so the goal switch only belongs to the other two kinds of run.
  const modePill = composerPill({ id: "mode", caption: "Mode", value: "", icon: null });
  let modeId = kept.mode;
  function paintMode() {
    const option = MODES.find((candidate) => candidate.id === modeId);
    modePill.setValue(option.label, "");
    modePill.setIcon(icon(option.icon));
    setTitle(modePill.element, option.hint);
    modeMenu.repaint();
  }
  function showMode(id) {
    modeId = id;
    paintMode();
    goalMode.setAvailable(id !== "chat");
  }
  function setMode(id) {
    showMode(id);
    onMode(id);
  }
  function pickMode(id) {
    showMode(id);
    modeMenu.close();
    onMode(id);
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
  paintMode();
  goalMode.setAvailable(modeId !== "chat");

  // The model: the daemon's default until one is picked, worn with its provider's mark (item 108).
  const modelPill = composerPill({ id: "model", caption: "Model", value: "", icon: null });
  // The reasoning level of a model that offers a choice; hidden for the others.
  const reasoningPill = composerPill({ id: "reasoning", caption: "Reasoning", value: "", icon: icon("brain") });
  setTitle(reasoningPill.element, "Reasoning level");
  reasoningPill.element.hidden = true;
  let chosenRef = kept.model;
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
    setTitle(modelPill.element, info === null ? "" : info.ref);
    const choice = info !== null && offersChoice(info);
    reasoningPill.element.hidden = !choice;
    if (choice) {
      const level = kept.level.read(info) ?? info.default_effort;
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
    kept.level.write(info, level);
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
        const row = optionRow({ lead: providerMark(group.provider), label: modelName(info.ref), hint: info.free ? "Free" : "", selected: chosenRef === info.ref, onPick: () => pickModel(info.ref) });
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
    const chosen = kept.level.read(info);
    const fallback = info.default_effort === null ? "Default" : `Default (${levelLabel(info.default_effort)})`;
    return [
      el(
        "div",
        { role: "listbox", "aria-label": "Reasoning" },
        optionRow({ lead: null, label: fallback, hint: "The daemon's own choice", selected: chosen === null, onPick: () => pickLevel(info, null) }),
        ...info.efforts.map((level) => optionRow({ lead: null, label: levelLabel(level), hint: "", selected: chosen === level, onPick: () => pickLevel(info, level) })),
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

  async function ready() {
    providers = await loadModels();
    paintModel();
  }

  ready().catch((error) => {
    console.warn("the model list is unavailable", error);
    setTitle(modelPill.element, `The model list could not be loaded: ${error.message}`);
  });
  syncDefault();

  const pills = { mode: modePill.element, model: modelPill.element, reasoning: reasoningPill.element, options: optionsPill.element };
  return {
    pills,
    setMode,
    goalMode,
    ready,
    value: () => {
      const info = selectedInfo();
      const level = info !== null && offersChoice(info) ? kept.level.read(info) : null;
      return {
        mode: modeId,
        model: chosenRef === "" ? null : chosenRef,
        effort: level,
        approvals: approvals.value(),
        goal_mode: modeId === "chat" ? null : goalMode.value(),
      };
    },
    // The status carries the daemon's defaults; the pickers show them until the person chose something else.
    sync: () => {
      syncDefault();
      approvals.sync();
      goalMode.sync();
    },
    setWaiting: (on) => {
      waiting = on;
      for (const pill of Object.values(pills)) {
        pill.toggleAttribute("data-waiting", on);
        setTitle(pill, pill.dataset.title ?? "");
      }
    },
    dispose: () => {
      optionsMenu.dispose();
      modeMenu.dispose();
      modelMenu.dispose();
      reasoningMenu.dispose();
    },
  };
}
