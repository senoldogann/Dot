// The choices of a run besides its goal, as the pills of a composer (item 108): the mode, the model with its reasoning
// and the Options (approvals and goal mode), each with its menu. The home's box and a chat's dock (item 133) both wear
// them; where they start and where a choice is kept is the box's own (`kept`): this browser's on the home, the chat's
// on a chat's page, which the daemon keeps for its next follow-up (item 199). The model's menu is modelpicker.js
// (item 208), which makes the daemon's default too (item 209).

import { api } from "./api.js";
import { composerPill } from "./composer.js";
import { el, modelName, switchRow } from "./dom.js";
import { t } from "./i18n.js";
import { allModels, levelLabel, offersChoice, ONE_MESSAGE, oneMessageChoices, SPEED_HINTS } from "./efforts.js";
import { icon } from "./icons.js";
import { defaultRowText, modelSections } from "./modelpicker.js";
import { anchoredPopover } from "./popover.js";
import { providerMark, providerOf } from "./providers.js";
import { approvalBadge, approvalLabel, approvalsPicker, goalModePicker } from "./runmodes.js";
import { phoneSheet } from "./sheet.js";
import { getSnapshot, loadModels, refresh } from "./state.js";
import { attempt, toast, toastError } from "./ui.js";

const MODES = [
  { id: "task", label: t("choices.task"), hint: t("choices.taskHint"), icon: "target" },
  { id: "chat", label: t("choices.chat"), hint: t("choices.chatHint"), icon: "message" },
  { id: "research", label: t("choices.research"), hint: t("choices.researchHint"), icon: "search" },
];
// Auto (item 495), a message's mode on the home and in a chat: the message's own words settle it (the daemon's
// agent/mode_words.py), so the box shows no mode pill until one is named with /task, /chat or /research.
export const AUTO_MODE = { id: "auto", label: t("choices.auto"), hint: t("choices.autoHint"), icon: "sparkles" };
// The modes a message may ask for: Auto first, then each mode by name.
export const MESSAGE_MODES = [AUTO_MODE, ...MODES];
// Never a real value of a status, so the first synchronisation always paints.
const NOTHING_SHOWN = false;
// What the pills add to their tooltips while a run works on a chat's page: a message waits in the queue and starts
// with them once that run, which keeps its own, has ended (item 148).
const WAITING_NOTE = t("choices.waiting");

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
    .map((provider) => ({ provider: provider.provider, models: provider.models.filter((model) => model.tools), why: null }))
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

/** A switch as a row of the chat's settings, as the phone draws it: the title with its line under it on the left, the
 * switch on the right; a tap anywhere on the row turns it. */
function sheetSwitch({ label, hint, on, onToggle }) {
  const toggle = el("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(on), "aria-label": label, onclick: onToggle });
  return switchRow(
    el("div", { class: "sheet-row sheet-switch" }, el("span", { class: "sheet-row-text" }, el("span", null, label), el("span", { class: "sheet-row-hint" }, hint)), toggle),
    toggle,
  );
}

const SVG = "http://www.w3.org/2000/svg";
const BARS = 4;

/** The reasoning level as bars, the pill's mark beside the model's name as the reference app's: four small bars of rising height, as
 * many of them filled as the level is high among the `levels` the model offers (lowest first); a level not among them
 * (the model's default, which the daemon names) fills the middle of the bars. A mark of the pill's ink, hidden from a
 * screen reader: the pill's value says the level in words. */
function levelBars(level, levels) {
  const at = level === null ? -1 : levels.indexOf(level);
  const filled = at < 0 ? BARS / 2 : Math.max(1, Math.round(((at + 1) / levels.length) * BARS));
  const mark = document.createElementNS(SVG, "svg");
  mark.setAttribute("class", "icon level-bars");
  mark.setAttribute("viewBox", "0 0 16 16");
  mark.setAttribute("aria-hidden", "true");
  for (let index = 0; index < BARS; index += 1) {
    const height = 4 + index * 3;
    const bar = document.createElementNS(SVG, "rect");
    bar.setAttribute("x", String(1 + index * 4));
    bar.setAttribute("y", String(15 - height));
    bar.setAttribute("width", "2");
    bar.setAttribute("height", String(height));
    bar.setAttribute("rx", "1");
    if (index < filled) {
      bar.setAttribute("data-on", "");
    }
    mark.append(bar);
  }
  return mark;
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
  // What the model's pill adds after the name on a phone (item 1121), where it alone stands for the choices: the mode
  // when one is named and the reasoning level. The approval mode is not among them: the owner read "Pay or send" as a
  // part of the model's name. Painted once the pills exist.
  let reasoningShort = "";
  let paintPhonePill = () => undefined;

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
  const optionsPill = composerPill({ id: "options", caption: t("choices.options"), value: "", icon: null });
  setTitle(optionsPill.element, optionsTitle);
  // The pill wears the mode's shorter name and its icon (item 398), its tooltip the whole name.
  // The approval mode shown now (null before the daemon's is known), for its row in a phone's chat settings.
  let approvalsShown = null;
  // Paints a phone's chat settings again while they are open, once they exist: the Approvals row says the new mode.
  let repaintSettings = () => undefined;
  const approvals = approvalsPicker((shown) => {
    approvalsShown = shown;
    repaintSettings();
    const badge = shown === null ? null : approvalBadge(shown);
    optionsPill.setValue(badge === null ? "" : badge.short, "");
    optionsPill.setIcon(icon(badge === null ? "lock" : badge.icon));
    // In the least-asking mode the run asks only to pay or send: the box says so even while its choices are away (item
    // 833).
    optionsPill.element.toggleAttribute("data-yolo", shown === "yolo");
    setTitle(optionsPill.element, shown === null ? optionsTitle : `${optionsTitle}: ${approvalLabel(shown)}`);
  }, told(kept.approvals));
  const goalMode = goalModePicker(told(kept.goalMode));
  const executionItems = [];
  function addExecutionItem(build) {
    executionItems.push(build);
  }
  const executionGroup = el(
    "div",
    { class: "pop-group pop-execution", role: "group", "aria-label": t("choices.howDotWorks") },
  );
  const optionsMenu = anchoredPopover({
    anchor: optionsPill.element,
    frame: frame.element,
    label: t("choices.options"),
    build: () => {
      const isChat = modeId === "chat";
      executionGroup.hidden = isChat;
      executionGroup.replaceChildren(
        el("div", { class: "pop-heading" }, t("choices.howDotWorks")),
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
  const modePill = composerPill({ id: "mode", caption: t("choices.mode"), value: "", icon: null });
  let modeId = kept.mode;
  function paintMode() {
    const option = modes.find((candidate) => candidate.id === modeId);
    paintPhonePill();
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
    modelMenu.repaint();
    onMode(id);
    onChange();
  }
  const modeMenu = anchoredPopover({
    anchor: modePill.element,
    frame: frame.element,
    label: t("choices.mode"),
    build: () => [
      el(
        "div",
        { role: "listbox", "aria-label": t("choices.mode") },
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
  const modelPill = composerPill({ id: "model", caption: t("choices.model"), value: "", icon: null });
  // The reasoning level of a model that offers a choice, and its speed when it has fast mode; hidden for the others.
  const reasoningPill = composerPill({ id: "reasoning", caption: t("choices.reasoning"), value: "", icon: icon("brain") });
  setTitle(reasoningPill.element, t("choices.reasoningLevel"));
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
    modelPill.setValue(ref === null ? t("choices.default") : modelName(ref), "");
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
    reasoningShort = "";
    if (shown) {
      const level = choice ? (kept.level.read(info) ?? info.default_effort) : null;
      const words = boost !== null ? boost.label : choice ? (level === null ? t("choices.default") : levelLabel(level)) : "";
      reasoningShort = fastOn(info) ? (words === "" ? t("choices.fast") : `${words} · ${t("choices.fast")}`) : words;
      reasoningPill.setValue(reasoningShort, "");
      reasoningPill.setIcon(
        boost !== null ? icon(boost.icon) : fastOn(info) ? icon("bolt") : choice ? levelBars(level, info.efforts) : icon("brain"),
      );
      const typed = boost !== null && once === null ? ` ${t("choices.typed")}` : "";
      setTitle(reasoningPill.element, boost === null ? t("choices.reasoningLevel") : `${boost.label}: ${boost.hint}${typed}`);
    } else {
      reasoningMenu.close();
    }
    paintPhonePill();
    modelMenu.repaint();
    reasoningMenu.repaint();
  }

  function pickModel(ref) {
    chosenRef = ref;
    // A choice for one message the new model does not offer is put away, not kept for another model.
    if (onceFor(selectedInfo()) === null) {
      once = null;
    }
    // The chat's settings stay open after a pick, on every width (item 2015, as the phone's sheet does).
    paintModel();
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
    paintModel();
    onChange();
  }

  // Fast mode as a switch row, as the phone's Chat settings draw it (the owner's report of 2026-11-27): only for a model
  // that has it; a model without it has no row at all.
  function fastRow(info) {
    if (info?.fast_mode !== true) {
      return null;
    }
    return sheetSwitch({ label: t("choices.fastMode"), hint: SPEED_HINTS.fast, on: fastOn(info), onToggle: () => pickSpeed(fastOn(info) ? "standard" : "fast") });
  }

  // The levels of a model that offers a choice in full words, then the choices for one message it offers (item 251).
  function levelRows(info) {
    if (info === null) {
      return [];
    }
    const rows = [];
    if (offersChoice(info)) {
      const chosen = kept.level.read(info);
      const fallback = info.default_effort === null ? t("choices.default") : t("choices.defaultOf", { level: levelLabel(info.default_effort) });
      rows.push(
        el(
          "div",
          { role: "listbox", "aria-label": t("choices.reasoning") },
          optionRow({ lead: null, label: fallback, hint: t("choices.ownChoice"), selected: chosen === null, onPick: () => pickLevel(info, null) }),
          ...info.efforts.map((level) => optionRow({ lead: null, label: levelLabel(level), hint: "", selected: chosen === level, onPick: () => pickLevel(info, level) })),
        ),
      );
    }
    const single = oneMessageChoices(info);
    if (single.length > 0) {
      rows.push(
        el(
          "div",
          { class: "pop-group", role: "group", "aria-label": t("choices.messageOnly") },
          el("div", { class: "pop-heading", "aria-hidden": "true" }, t("choices.messageOnly")),
          el(
            "div",
            { role: "listbox", "aria-label": t("choices.messageOnly") },
            ...single.map((choice) =>
              optionRow({ lead: icon(choice.icon), label: choice.label, hint: choice.hint, selected: (once ?? typedChoice()) === choice.id, onPick: () => pickOnce(choice.id) }),
            ),
          ),
        ),
      );
    }
    return rows;
  }

  // The Reasoning pill's menu on a desk: the levels, the choices for one message, then Fast mode as a switch.
  function reasoningRows() {
    const info = selectedInfo();
    const fast = fastRow(info);
    return [...levelRows(info), ...(fast === null ? [] : [el("div", { class: "pop-switches" }, fast)])];
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
    toast("ok", t("choices.newDefault", { model: modelName(ref) }));
    await attempt(refresh);
    return true;
  }

  function modelArgs() {
    const fallback = defaultRefOf(getSnapshot());
    return { groups: toolGroups(providers), chosen: chosenRef, fallback, blank: defaultRowText(fallback) };
  }

  // On a phone the model's pill opens the chat's settings instead (item 1202, the owner's ask after another
  // assistant's thread settings; items 1121 and 1122 had them as tabs of a menu over the box): a sheet from the foot of
  // the screen with the models in a section per provider, then the Options, Reasoning and Mode as rows that open in
  // place, the approvals and how Dot works, and the search of the models at its foot. A pick keeps the sheet open, as
  // the other assistant's does; Done or Close puts it away. The other pills stay on a Mac's wider box.
  const sections = modelSections({ onPick: pickModel, onMakeDefault: makeDefault });
  // The rows of the Options opened by hand, by id, kept while the sheet repaints after a pick.
  const openRows = new Set();

  function disclosure(id, label, value, rows) {
    const details = el(
      "details",
      { class: "sheet-disclosure", open: openRows.has(id) },
      el("summary", { class: "sheet-row" }, el("span", { class: "sheet-row-text" }, label), el("span", { class: "sheet-row-value" }, value), icon("chevron")),
      el("div", { class: "sheet-disclosure-body" }, ...rows),
    );
    details.addEventListener("toggle", () => {
      if (details.open) {
        openRows.add(id);
      } else {
        openRows.delete(id);
      }
    });
    return details;
  }

  // The phone's Chat settings in the web (the owner's report of 2026-11-27): the models, then Options as one card in the
  // phone's order and words (Reasoning with the levels in full words, Fast mode only for a model that has it, Approvals,
  // Plan first), then the web's own Mode and how Dot works.
  function settingsParts() {
    const isChat = modeId === "chat";
    executionGroup.hidden = isChat;
    executionGroup.replaceChildren(el("div", { class: "pop-heading" }, t("choices.howDotWorks")), ...executionItems.flatMap((build) => build()), goalMode.element);
    const info = selectedInfo();
    const levels = levelRows(info);
    const fast = fastRow(info);
    const level = info !== null && offersChoice(info) ? (kept.level.read(info) ?? null) : null;
    const levelWords = level !== null ? levelLabel(level) : info?.default_effort ? t("choices.defaultOf", { level: levelLabel(info.default_effort) }) : t("choices.default");
    const mode = modes.find((option) => option.id === modeId);
    const modeRows = el(
      "div",
      { role: "listbox", "aria-label": t("choices.mode") },
      ...modes.map((option) => optionRow({ lead: icon(option.icon), label: option.label, hint: option.hint, selected: option.id === modeId, onPick: () => pickMode(option.id) })),
    );
    const plan = isChat
      ? null
      : sheetSwitch({
          label: t("options.planFirst"),
          hint: t("choices.planFirstNext"),
          on: frame.picks.planFirst(),
          // The sheet is painted again so that its switch says the new state, as Fast mode's is (pickSpeed).
          onToggle: () => {
            frame.picks.setPlanFirst(!frame.picks.planFirst());
            repaintSettings();
          },
        });
    const models = sections.build(modelArgs());
    return {
      body: [
        models.list,
        el("div", { class: "sheet-heading" }, t("choices.options")),
        el(
          "div",
          { class: "sheet-card sheet-options" },
          ...(levels.length === 0 ? [] : [disclosure("reasoning", t("choices.reasoning"), levelWords, levels)]),
          ...(fast === null ? [] : [fast]),
          disclosure("approvals", t("options.approvals"), approvalsShown === null ? "" : approvalLabel(approvalsShown), [approvals.element]),
          ...(plan === null ? [] : [plan]),
        ),
        el("div", { class: "sheet-card" }, disclosure("mode", t("choices.mode"), mode === undefined ? "" : mode.label, [modeRows])),
        el("div", { class: "sheet-card sheet-groups" }, ...(isChat ? [] : [executionGroup]), ...more()),
      ],
      foot: [models.search],
    };
  }

  // The model's pill opens the chat's settings on every width (item 2015, as DotNext's chip does): the same sheet, from
  // the foot of a phone's screen and over the middle of a wider window's.
  function makeModelMenu() {
    return phoneSheet({ anchor: modelPill.element, title: t("choices.chatSettings"), build: settingsParts });
  }
  const modelMenu = makeModelMenu();
  repaintSettings = () => modelMenu.repaint();

  paintPhonePill = () => {
    const option = modes.find((candidate) => candidate.id === modeId);
    const named = option === undefined || option.id === AUTO_MODE.id ? "" : option.label;
    const extra = [named, reasoningShort].filter((word) => word !== "").join(" · ");
    const value = modelPill.element.querySelector(".composer-pill-value")?.textContent ?? "";
    modelPill.setValue(value, extra);
  };
  paintPhonePill();
  const reasoningMenu = anchoredPopover({ anchor: reasoningPill.element, frame: frame.element, label: t("choices.reasoning"), build: reasoningRows });

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
    setTitle(modelPill.element, t("choices.modelsFailed", { problem: error.message }));
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
