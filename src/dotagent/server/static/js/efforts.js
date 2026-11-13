// The reasoning level a person prefers for each model. A model lists the levels it offers, lowest first (fewer than two
// means there is nothing to choose); the choice is kept per browser and goes with every run started from here. Beside
// it, fast mode (item 223), for the Claude models that have it, and the choices for one message (item 251).

import { t } from "./i18n.js";
import { readPref, writePref } from "./prefs.js";

const KEY_PREFIX = "dot.effort.";
const SPEED_KEY = "dot.speed";
// What each speed is, under its name in the reasoning menu.
export const SPEED_HINTS = {
  standard: t("efforts.standardHint"),
  fast: t("efforts.fastHint"),
};
// The names of the levels a person reads; the ids (the keys) are what the daemon and the models take, never these.
const LABELS = {
  off: t("efforts.off"),
  on: t("efforts.on"),
  minimal: t("efforts.minimal"),
  low: t("efforts.low"),
  medium: t("efforts.medium"),
  high: t("efforts.high"),
  xhigh: t("efforts.xhigh"),
  max: t("efforts.max"),
};

function keyOf(model) {
  return `${KEY_PREFIX}${model.ref}`;
}

// The choices for one message (item 251), named after Claude Code's: the level each asks the model for (the daemon's
// ONE_MESSAGE_LEVELS in llm/types.py), and what it does, under its name in the reasoning menu. Nothing keeps them: the
// message after goes with the level chosen before.
export const ONE_MESSAGE = [
  { id: "ultrathink", label: "Ultrathink", level: "max", icon: "sparkle", hint: t("efforts.ultrathinkHint") },
  {
    id: "ultracode",
    label: "Ultracode",
    level: "xhigh",
    icon: "branch",
    hint: t("efforts.ultracodeHint"),
  },
];

/** The choices for one message that `model` offers: those whose level it has. */
export function oneMessageChoices(model) {
  return ONE_MESSAGE.filter((choice) => model.efforts.includes(choice.level));
}

/** The words for a level, for buttons and chips. */
export function levelLabel(level) {
  return LABELS[level] ?? level;
}

/** The words for the level a run asked for, and the choice for one message it was started with (item 251) beside it. */
export function askedLabel(level, once) {
  const choice = ONE_MESSAGE.find((candidate) => candidate.id === once);
  return choice === undefined ? levelLabel(level) : `${levelLabel(level)}, ${choice.label}`;
}

/** The home's choice of speed, kept per browser: "fast" for fast mode, else "standard". */
export const KEPT_SPEED = {
  read: () => (readPref(SPEED_KEY) === "fast" ? "fast" : "standard"),
  write: (speed) => writePref(SPEED_KEY, speed),
};

/** Whether the model has more than one level to choose from. */
export function offersChoice(model) {
  return model.efforts.length >= 2;
}

/** The level saved for the model, or null when none was saved or the model no longer offers the saved one. */
export function savedLevel(model) {
  const stored = readPref(keyOf(model));
  return stored !== null && model.efforts.includes(stored) ? stored : null;
}

export function saveLevel(model, level) {
  if (!model.efforts.includes(level)) {
    throw new Error(`${model.ref} offers no reasoning level ${level}`);
  }
  writePref(keyOf(model), level);
}

/** Go back to the daemon's own choice for the model. */
export function forgetLevel(model) {
  writePref(keyOf(model), "");
}

/** Every model of every provider in one list, as `loadModels()` returns them grouped. */
export function allModels(providers) {
  return providers.flatMap((provider) => provider.models);
}

/**
 * The level a form sends for the model `ref`: what its select says (`picked`, "" for the default) once `models` lists
 * that model's levels; until then (the list is loading or failed, or the model is not offered now) the level `kept`
 * saved with the model `keptRef`, which the form could not show, so a save does not drop it.
 */
export function chosenLevel(models, ref, picked, keptRef, kept) {
  if (models.some((candidate) => candidate.ref === ref)) return picked === "" ? null : picked;
  return ref === keptRef ? kept : null;
}
