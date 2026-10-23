// What the installer's page decides from what the shell tells it (item 575).

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canInstall,
  installHeading,
  macRows,
  mark,
  modelsFor,
  nextScreen,
  offersChangeOfModels,
  redact,
  validKey,
  validModel,
} from "../../desktop/ui/installer-model.js";

const KEY = "sk-MARKER-0123456789abcdef";

const PROVIDERS = [
  { provider: "ollama", label: "Ollama", needs: "local models", key: false, models: ["minimax-m2.7:cloud"] },
  { provider: "openai", label: "OpenAI", needs: "an OpenAI API key", key: true, models: ["gpt-5.5"] },
  { provider: "opencode_go", label: "OpenCode Go", needs: "an OpenCode Go API key", key: true, models: [] },
];

function inspected(changes = {}) {
  return {
    version: "0.1.0",
    macos: { ok: true, detail: "macOS 26.0.1" },
    chrome: { ok: true, detail: "" },
    ollama: { running: true, models: ["gpt-oss:20b", "qwen3:8b"] },
    location: { ok: true, detail: "/Applications/Dot.app" },
    providers: PROVIDERS,
    resuming: false,
    modelsDone: false,
    blocked: null,
    log: "/Users/me/Library/Logs/Dot/installer.log",
    ...changes,
  };
}

test("a Mac with everything gets rows that are all in order and may go on", () => {
  const { rows, canContinue } = macRows(inspected());
  assert.deepEqual(
    rows.map((row) => [row.id, row.state]),
    [
      ["macos", "ok"],
      ["chrome", "ok"],
    ],
  );
  assert.equal(canContinue, true);
});

test("a missing Chrome and an old macOS warn and the setup goes on, with a button for Chrome", () => {
  const { rows, canContinue } = macRows(
    inspected({ chrome: { ok: false, detail: "" }, macos: { ok: false, detail: "macOS 14.7" } }),
  );
  assert.deepEqual(
    rows.map((row) => [row.id, row.state]),
    [
      ["macos", "warn"],
      ["chrome", "warn"],
    ],
  );
  assert.match(rows[0].text, /macOS 14\.7\. The desktop tools need macOS 15\.2/);
  assert.deepEqual(rows[1].action, { label: "Get Chrome", what: "chrome" });
  assert.equal(canContinue, true);
});

test("an app on its disk image, or a Dot that is there already, stops the setup and says why", () => {
  const onImage = macRows(inspected({ location: { ok: false, detail: "/Volumes/Dot/Dot.app" } }));
  assert.equal(onImage.canContinue, false);
  assert.match(onImage.rows.at(-1).text, /Drag Dot to the Applications folder/);
  const there = macRows(inspected({ blocked: "a Dot daemon already answers on this Mac" }));
  assert.equal(there.canContinue, false);
  assert.equal(
    there.rows.at(-1).text,
    "A Dot daemon already answers on this Mac, so there is nothing to set up. Close this window and open Dot.",
  );
});

test("a model is written as its provider writes it and never starts like a flag", () => {
  for (const good of ["gpt-5.5", "claude-opus-5-5", "anthropic/claude-sonnet-5.5", "minimax-m2.7:cloud", "m@1+2_3"]) {
    assert.equal(validModel(good), true, good);
  }
  for (const bad of ["", " ", "-flag", "--no-service", "a b", "a;b", "a\nb", "a`b", "x".repeat(129)]) {
    assert.equal(validModel(bad), false, JSON.stringify(bad));
  }
  assert.equal(validModel(" gpt-5.5 "), true, "the shell trims it as the page does");
});

test("a key is visible text of a key's length with no space in it", () => {
  assert.equal(validKey(KEY), true);
  assert.equal(validKey(`  ${KEY}\n`), true);
  for (const bad of ["", "short", "has a space in it, which no key has", `${KEY}é`, "x".repeat(513)]) {
    assert.equal(validKey(bad), false, JSON.stringify(bad));
  }
});

test("the choice can be sent once a provider, a model and, where the provider needs one, a key are there", () => {
  const inspection = inspected();
  const verdict = (provider, model, key) => canInstall({ provider, model, key }, inspection);
  assert.deepEqual(verdict(null, "", ""), { ok: false, reason: "Choose where Dot's models come from." });
  assert.equal(verdict("ollama", "gpt-oss:20b", "").ok, true);
  assert.equal(verdict("openai", "gpt-5.5", KEY).ok, true);
  assert.deepEqual(verdict("openai", "gpt-5.5", ""), { ok: false, reason: "Paste your OpenAI API key." });
  assert.deepEqual(verdict("openai", "", KEY), { ok: false, reason: "Choose a model." });
  assert.deepEqual(verdict("openai", "--x", KEY), { ok: false, reason: "Choose a model." });
  assert.equal(verdict("nobody", "m", KEY).ok, false);
  // Ollama's models run on this Mac: with no Ollama there is nothing to install on.
  const off = inspected({ ollama: { running: false, models: [] } });
  assert.deepEqual(canInstall({ provider: "ollama", model: "x", key: "" }, off), {
    ok: false,
    reason: "Ollama is not running. Open it, then press Check again.",
  });
  assert.equal(canInstall({ provider: "openai", model: "gpt-5.5", key: KEY }, off).ok, true);
});

test("the models screen starts with Ollama's own models first, then the table's, and with none for a provider without any", () => {
  const inspection = inspected();
  assert.deepEqual(modelsFor(PROVIDERS[0], inspection), {
    model: "gpt-oss:20b",
    options: ["gpt-oss:20b", "qwen3:8b", "minimax-m2.7:cloud"],
  });
  assert.deepEqual(modelsFor(PROVIDERS[1], inspection), { model: "gpt-5.5", options: ["gpt-5.5"] });
  assert.deepEqual(modelsFor(PROVIDERS[2], inspection), { model: "", options: [] });
  const bare = inspected({ ollama: { running: true, models: [] } });
  assert.deepEqual(modelsFor(PROVIDERS[0], bare), { model: "minimax-m2.7:cloud", options: ["minimax-m2.7:cloud"] });
});

test("the key is hidden in any text the page would show, and a short value is not mistaken for one", () => {
  assert.equal(redact(`the provider refused ${KEY} today`, KEY), "the provider refused •••• today");
  assert.equal(redact(`${KEY} and ${KEY}`, `  ${KEY} `), "•••• and ••••");
  assert.equal(redact("the key", "the"), "the key");
  assert.equal(redact("nothing here", ""), "nothing here");
});

test("a step shows a mark for its state and the screen a heading for the install's", () => {
  assert.deepEqual(["waiting", "running", "done", "failed"].map(mark), ["○", "…", "✓", "✕"]);
  assert.equal(installHeading({ phase: "running" }), "Installing Dot");
  assert.equal(installHeading({ phase: "failed" }), "The install stopped");
  assert.equal(installHeading({ phase: "done" }), "Dot is ready");
});

test("an install that stopped after the models goes on with nothing to choose, and any other asks for the models", () => {
  assert.equal(nextScreen(inspected({ resuming: true, modelsDone: true })), "resume");
  // The package only: the models are still to be chosen.
  assert.equal(nextScreen(inspected({ resuming: true, modelsDone: false })), "models");
  // A first install, and a Mac that has a Dot already: the models screen, as before.
  assert.equal(nextScreen(inspected()), "models");
  assert.equal(nextScreen(inspected({ resuming: false, modelsDone: true })), "models");
});

test("Change the models is offered unless the shell says a new choice would not be taken", () => {
  assert.equal(offersChangeOfModels({ canChangeModels: true }), true);
  assert.equal(offersChangeOfModels({ canChangeModels: false }), false);
  // A shell that says nothing is one of an older build: the button stays.
  assert.equal(offersChangeOfModels({}), true);
});
