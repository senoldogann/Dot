// What the installer's page decides from what the shell tells it (item 575): which rows say what of this Mac, whether the
// choice of provider, model and key can be sent, and how a step is shown. Pure functions: installer.js draws the page
// with them and tests/js/installer.test.js runs them. The shell checks all of it again (`Choice::check` in
// src-tauri/src/installer.rs); the page's checks only keep the person from sending what the shell would refuse.

/** What the shell's `installer_inspect` answers. @typedef {{ok: boolean, detail: string}} Check */
/** @typedef {{provider: string, label: string, needs: string, key: boolean, models: string[]}} Provider */
/**
 * @typedef {object} Inspection
 * @property {string} version
 * @property {Check} macos
 * @property {Check} chrome
 * @property {{running: boolean, models: string[]}} ollama
 * @property {Check} location
 * @property {Provider[]} providers
 * @property {boolean} resuming
 * @property {boolean} modelsDone
 * @property {string | null} blocked
 * @property {string} log
 */

/** One row of "What this Mac has": `state` is ok, warn (the setup goes on) or block (it cannot). */
/** @typedef {{id: string, state: "ok" | "warn" | "block", text: string, action?: {label: string, what: string}}} Row */

/** Model names as the shell takes them: a letter or a digit first, so that no name is read as a flag. */
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/;
const KEY_MIN = 8;
const KEY_MAX = 512;

/** @param {string} model */
export function validModel(model) {
  return MODEL.test(model.trim());
}

/** @param {string} key */
export function validKey(key) {
  const trimmed = key.trim();
  return trimmed.length >= KEY_MIN && trimmed.length <= KEY_MAX && /^[\x21-\x7e]+$/.test(trimmed);
}

/**
 * The rows of "What this Mac has", and whether the person may go on. Chrome and an old macOS only warn: the browser
 * tools and the desktop tools need them, and the rest of Dot does not. An app that runs from its disk image, or a Dot
 * that is installed already, stops the setup.
 * @param {Inspection} inspection
 * @returns {{rows: Row[], canContinue: boolean}}
 */
export function macRows(inspection) {
  /** @type {Row[]} */
  const rows = [];
  rows.push(
    inspection.macos.ok
      ? { id: "macos", state: "ok", text: `${inspection.macos.detail} (15.2 or later is needed)` }
      : {
          id: "macos",
          state: "warn",
          text: `${inspection.macos.detail}. The desktop tools need macOS 15.2 or later; everything else works.`,
        },
  );
  rows.push(
    inspection.chrome.ok
      ? { id: "chrome", state: "ok", text: "Google Chrome is installed" }
      : {
          id: "chrome",
          state: "warn",
          text: "Google Chrome is not installed. The browser tools need it; the setup goes on without it.",
          action: { label: "Get Chrome", what: "chrome" },
        },
  );
  if (!inspection.location.ok) {
    rows.push({
      id: "location",
      state: "block",
      text: "Dot is running from its disk image. Drag Dot to the Applications folder, open it from there, and this goes on.",
    });
  }
  if (inspection.blocked) {
    rows.push({
      id: "blocked",
      state: "block",
      text: `${capitalized(inspection.blocked)}, so there is nothing to set up. Close this window and open Dot.`,
    });
  }
  return { rows, canContinue: rows.every((row) => row.state !== "block") };
}

/**
 * What Continue on "What this Mac has" does. An install that stopped after the models were set up goes on from there
 * with no question to answer: the shell never runs `dotx setup` over the models a second time, so the page does not ask
 * for them. Any other goes to the choice of models.
 * @param {Inspection} inspection
 * @returns {"models" | "resume"}
 */
export function nextScreen(inspection) {
  return inspection.resuming && inspection.modelsDone ? "resume" : "models";
}

/**
 * Whether Change the models is offered on the failure screen. The shell says it: where the models were set up before
 * this session and none was chosen in it (an install that was resumed), a new choice could not be taken, and the shell
 * refuses one in words, so the page does not offer it. A shell that says nothing is taken to be one that can.
 * @param {{canChangeModels?: boolean}} status
 */
export function offersChangeOfModels(status) {
  return status.canChangeModels !== false;
}

/** @param {string} text */
function capitalized(text) {
  return text === "" ? text : text[0].toUpperCase() + text.slice(1);
}

/**
 * What the models screen starts with for `provider`: the model to put in the field, and the names to suggest.
 * Ollama's are the models that Ollama has on this Mac, then what the table suggests; the others' are the table's.
 * @param {Provider} provider
 * @param {Inspection} inspection
 * @returns {{model: string, options: string[]}}
 */
export function modelsFor(provider, inspection) {
  const own = provider.provider === "ollama" ? inspection.ollama.models : [];
  const options = [...own, ...provider.models.filter((name) => !own.includes(name))];
  return { model: options[0] ?? "", options };
}

/**
 * Whether the choice can be sent, and if not, in a few words why.
 * @param {{provider: string | null, model: string, key: string}} choice
 * @param {Inspection} inspection
 * @returns {{ok: boolean, reason: string}}
 */
export function canInstall(choice, inspection) {
  const provider = inspection.providers.find((candidate) => candidate.provider === choice.provider);
  if (!provider) {
    return { ok: false, reason: "Choose where Dot's models come from." };
  }
  if (provider.provider === "ollama" && !inspection.ollama.running) {
    return { ok: false, reason: "Ollama is not running. Open it, then press Check again." };
  }
  if (!validModel(choice.model)) {
    return { ok: false, reason: "Choose a model." };
  }
  if (provider.key && !validKey(choice.key)) {
    return { ok: false, reason: `Paste your ${provider.label} API key.` };
  }
  return { ok: true, reason: "" };
}

/**
 * The text with the key hidden, for a page that never meant to show it: the shell masks what its programs print, and
 * this is a second hand on the same door.
 * @param {string} text
 * @param {string} key
 */
export function redact(text, key) {
  const secret = key.trim();
  return secret.length >= KEY_MIN ? text.split(secret).join("••••") : text;
}

/** The mark a step shows, by its state. @param {string} state */
export function mark(state) {
  switch (state) {
    case "done":
      return "✓";
    case "failed":
      return "✕";
    case "running":
      return "…";
    default:
      return "○";
  }
}

/**
 * What the install screen is: the heading, and whether the failure and the "ready" lines show.
 * @param {{phase: string}} status
 */
export function installHeading(status) {
  switch (status.phase) {
    case "done":
      return "Dot is ready";
    case "failed":
      return "The install stopped";
    default:
      return "Installing Dot";
  }
}
