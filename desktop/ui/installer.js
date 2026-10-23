// The installer's page (item 575): what this Mac has, which models Dot should use, and the install with its steps. It
// talks to the shell with the five commands only this page may call (`installer_*` in src-tauri/src/installer.rs;
// capabilities/installer.json names this window and no other), and draws everything it is told with textContent: a line
// a program printed is text, never markup. The key stays in its password field: it is sent to the shell once, at
// Install, and nowhere else, and the field is emptied when the install is done.

import {
  canInstall,
  installHeading,
  macRows,
  mark,
  modelsFor,
  nextScreen,
  offersChangeOfModels,
  redact,
} from "./installer-model.js";

const bridge = /** @type {any} */ (window).__TAURI__?.core;
const POLL_MS = 500;

/** @param {string} id */
function element(id) {
  return /** @type {HTMLElement} */ (document.getElementById(id));
}

const keyField = /** @type {HTMLInputElement} */ (element("key"));
const modelField = /** @type {HTMLInputElement} */ (element("model"));

/** @type {import("./installer-model.js").Inspection | null} */
let inspection = null;
/** @type {string | null} */
let provider = null;
/** @type {number | undefined} */
let polling;

/** @param {string} name */
function show(name) {
  for (const screen of document.querySelectorAll("[data-screen]")) {
    /** @type {HTMLElement} */ (screen).hidden = /** @type {HTMLElement} */ (screen).dataset.screen !== name;
  }
  if (name === "models") {
    // The button was turned off at Install: it is what the choice says once the screen is back.
    validate();
  }
}

/**
 * @param {string} command
 * @param {Record<string, unknown>} [args]
 */
function call(command, args) {
  return bridge.invoke(command, args);
}

/** @param {string} what */
async function open(what) {
  try {
    await call("installer_open", { what });
  } catch (error) {
    // Said where the person is looking: the problem line of the screen that shows.
    const screen = document.querySelector("[data-screen]:not([hidden])");
    const line = /** @type {HTMLElement | null} */ (screen?.querySelector(".problem") ?? null);
    if (line) {
      line.textContent = String(error);
      line.hidden = false;
    }
  }
}

/** @param {HTMLElement} parent @param {string} tag @param {string} [text] @param {string} [className] */
function add(parent, tag, text, className) {
  const created = document.createElement(tag);
  if (text !== undefined) {
    created.textContent = text;
  }
  if (className) {
    created.className = className;
  }
  parent.append(created);
  return created;
}

function drawMac() {
  if (!inspection) {
    return;
  }
  const { rows, canContinue } = macRows(inspection);
  const list = element("mac-rows");
  list.replaceChildren();
  for (const row of rows) {
    const item = add(list, "li", undefined, "row");
    item.dataset.state = row.state;
    add(item, "span", row.state === "ok" ? "✓" : row.state === "warn" ? "!" : "✕", "mark");
    add(item, "span", row.text);
    if (row.action) {
      const button = add(item, "button", row.action.label, "link");
      button.setAttribute("type", "button");
      const what = row.action.what;
      button.addEventListener("click", () => open(what));
    } else {
      add(item, "span");
    }
  }
  element("resuming").hidden = !inspection.resuming;
  element("resuming-models").hidden = nextScreen(inspection) !== "resume";
  /** @type {HTMLButtonElement} */ (element("mac-next")).disabled = !canContinue;
}

async function inspect() {
  try {
    inspection = await call("installer_inspect");
  } catch (error) {
    inspection = null;
    const list = element("mac-rows");
    list.replaceChildren();
    const item = add(list, "li", undefined, "row");
    item.dataset.state = "block";
    add(item, "span", "✕", "mark");
    add(item, "span", String(error));
    add(item, "span");
    /** @type {HTMLButtonElement} */ (element("mac-next")).disabled = true;
    return;
  }
  drawMac();
  drawProviders();
}

function drawProviders() {
  if (!inspection) {
    return;
  }
  const group = element("providers");
  group.replaceChildren();
  for (const entry of inspection.providers) {
    const label = add(group, "label", undefined, "provider");
    const radio = /** @type {HTMLInputElement} */ (add(label, "input"));
    radio.type = "radio";
    radio.name = "provider";
    radio.value = entry.provider;
    radio.checked = entry.provider === provider;
    radio.addEventListener("change", () => choose(entry.provider));
    const text = add(label, "span");
    add(text, "div", entry.label, "name");
    add(text, "div", entry.needs, "needs");
  }
  drawDetails();
}

/** @param {string} name */
function choose(name) {
  provider = name;
  const entry = inspection?.providers.find((candidate) => candidate.provider === name);
  if (inspection && entry) {
    const { model, options } = modelsFor(entry, inspection);
    modelField.value = model;
    element("model-options").replaceChildren(
      ...options.map((option) => {
        const created = document.createElement("option");
        created.value = option;
        return created;
      }),
    );
  }
  drawDetails();
}

function drawDetails() {
  const entry = inspection?.providers.find((candidate) => candidate.provider === provider);
  const note = element("ollama-note");
  const again = element("ollama-again");
  element("model-field").hidden = !entry;
  element("key-field").hidden = !entry?.key;
  element("key-note").hidden = !entry?.key;
  note.hidden = entry?.provider !== "ollama";
  again.hidden = entry?.provider !== "ollama";
  if (entry?.provider === "ollama" && inspection) {
    note.replaceChildren();
    if (inspection.ollama.running) {
      note.textContent =
        "Ollama is running on this Mac. A model with “cloud” in its name needs an Ollama account: open Ollama and sign in.";
    } else {
      note.textContent =
        "Ollama is not running. Install and open it, then press Check again. A model with “cloud” in its name needs an Ollama account: open Ollama and sign in.";
      const get = add(note, "button", "Get Ollama", "link");
      get.setAttribute("type", "button");
      get.addEventListener("click", () => open("ollama"));
    }
  }
  if (entry?.key) {
    element("key-label").textContent = `${entry.label} API key`;
  }
  validate();
}

function validate() {
  const button = /** @type {HTMLButtonElement} */ (element("install"));
  if (!inspection) {
    button.disabled = true;
    return;
  }
  const verdict = canInstall({ provider, model: modelField.value, key: keyField.value }, inspection);
  button.disabled = !verdict.ok;
  const problem = element("models-problem");
  // Say why only once the person has started to choose: an empty form needs no scolding.
  problem.hidden = verdict.ok || provider === null;
  problem.textContent = verdict.ok ? "" : verdict.reason;
}

/** @param {{phase: string, steps: {id: string, title: string, state: string, output: string[]}[], failure: any, canChangeModels?: boolean}} status */
function drawStatus(status) {
  const key = keyField.value;
  element("install-title").textContent = installHeading(status);
  const list = element("steps");
  list.replaceChildren();
  for (const step of status.steps) {
    const item = add(list, "li", undefined, "step");
    item.dataset.state = step.state;
    add(item, "span", mark(step.state), "mark");
    add(item, "span", step.title);
    add(item, "span");
    const lines = add(item, "pre", undefined, "lines");
    if (step.state === "running") {
      lines.textContent = step.output.slice(-6).map((line) => redact(line, key)).join("\n");
    }
  }
  const failure = element("failure");
  failure.hidden = status.phase !== "failed" || !status.failure;
  element("failure-change").hidden = !offersChangeOfModels(status);
  if (status.failure) {
    element("failure-title").textContent = `${status.failure.title} did not finish`;
    element("failure-message").textContent = redact(status.failure.message, key);
    element("failure-output").textContent = status.failure.output.map((line) => redact(line, key)).join("\n");
    element("failure-fix").textContent = status.failure.fix;
  }
  element("done").hidden = status.phase !== "done";
  if (status.phase === "done") {
    keyField.value = "";
  }
}

async function poll() {
  window.clearTimeout(polling);
  try {
    const status = await call("installer_status");
    drawStatus(status);
    if (status.phase === "running") {
      polling = window.setTimeout(poll, POLL_MS);
    }
  } catch (error) {
    element("failure-message").textContent = String(error);
    element("failure").hidden = false;
  }
}

async function start() {
  const button = /** @type {HTMLButtonElement} */ (element("install"));
  button.disabled = true;
  try {
    await call("installer_start", {
      choice: { provider, model: modelField.value.trim(), key: keyField.value.trim() === "" ? null : keyField.value.trim() },
    });
  } catch (error) {
    const problem = element("models-problem");
    problem.textContent = String(error);
    problem.hidden = false;
    validate();
    return;
  }
  show("install");
  poll();
}

/** An install that stopped after the models: go on from where it stopped, with nothing to choose. */
async function resume() {
  try {
    await call("installer_retry");
  } catch (error) {
    const problem = element("mac-problem");
    problem.textContent = String(error);
    problem.hidden = false;
    return;
  }
  show("install");
  poll();
}

async function again() {
  try {
    await call("installer_retry");
  } catch (error) {
    element("failure-message").textContent = String(error);
    return;
  }
  poll();
}

if (!bridge) {
  element("outside").hidden = false;
} else {
  element("mac-again").addEventListener("click", inspect);
  element("mac-next").addEventListener("click", () => {
    if (inspection && nextScreen(inspection) === "resume") {
      resume();
    } else {
      show("models");
    }
  });
  element("ollama-again").addEventListener("click", async () => {
    await inspect();
    if (provider === "ollama" && modelField.value === "") {
      choose("ollama");
    }
  });
  element("models-back").addEventListener("click", () => show("mac"));
  element("install").addEventListener("click", start);
  element("failure-again").addEventListener("click", again);
  element("failure-change").addEventListener("click", () => show("models"));
  element("failure-log").addEventListener("click", () => open("log"));
  modelField.addEventListener("input", validate);
  keyField.addEventListener("input", validate);
  window.addEventListener("pagehide", () => {
    keyField.value = "";
  });
  show("mac");
  inspect();
  // An install that is under way, or stopped, in the shell: the page shows it as it is.
  call("installer_status").then((status) => {
    if (status.phase !== "idle") {
      show("install");
      drawStatus(status);
      if (status.phase === "running") {
        poll();
      }
    }
  });
}
