// The reasoning level a person prefers for each model. A model lists the levels it offers, lowest first (fewer than two
// means there is nothing to choose); the choice is kept per browser and goes with every run started from here.

import { readPref, writePref } from "./prefs.js";

const KEY_PREFIX = "dot.effort.";
const LABELS = {
  off: "Off",
  on: "On",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

function keyOf(model) {
  return `${KEY_PREFIX}${model.ref}`;
}

/** The words for a level, for buttons and chips. */
export function levelLabel(level) {
  return LABELS[level] ?? level;
}

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
