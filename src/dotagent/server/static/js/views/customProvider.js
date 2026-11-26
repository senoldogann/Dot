// Custom providers (item 1949): the form that adds or changes one, in a dialog over Settings › AI services. A
// custom provider is a server that speaks a protocol Dot already speaks (OpenAI's Chat Completions or Responses,
// Anthropic's Messages): its name is the prefix of its models (`groq:llama-4-scout`), and the daemon keeps it in
// `[providers.custom.<name>]` of the config file. Fill from docs reads the provider's documentation page and proposes
// the fields, which the owner reviews; Test makes one small real call, only when pressed; nothing is saved before Save.
// The key is typed in the form and saved through the key routes, like every provider's; it never comes back.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { field } from "../panels.js";
import { formDialog, toast } from "../ui.js";

export const PROTOCOLS = {
  chat: "OpenAI Chat Completions",
  responses: "OpenAI Responses",
  messages: "Anthropic Messages",
};
// A custom provider's name, as the daemon takes one: lower case, from a letter, up to 32 (`custom_provider_name`).
const NAME_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;
// The built-in providers' names and the typed judge's, which a custom provider cannot take.
const TAKEN = new Set(["ollama", "openai", "google", "opencode_go", "anthropic", "openrouter", "antigravity", "opencode", "claude_code", "typesafe"]);
const KEY_PATTERN = /^[!-~]+$/;
const DEFAULT_CONTEXT = 128000;
const DEFAULT_OUTPUT = 8192;

/** Where an address sends a request (its scheme, host and port), "" for one that is no address. */
function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

/** Whether `name` can name a custom provider. */
function customName(name) {
  return NAME_PATTERN.test(name) && !TAKEN.has(name);
}

/** The extra headers as the form's lines hold them, `Name: value` each; a line without a colon is refused. */
function parseHeaders(text) {
  const headers = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "") {
      continue;
    }
    const at = line.indexOf(":");
    if (at <= 0) {
      throw new Error(`"${line}" is not a header: write it as Name: value.`);
    }
    headers[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return headers;
}

/** One row of the models table: its id and what it can do. */
function modelRow(model, onRemove) {
  const id = el("input", { class: "input sm mono", value: model.id, placeholder: "model id", "aria-label": "Model id", spellcheck: "false", autocomplete: "off" });
  const box = (label, checked) => el("label", { class: "custom-check" }, el("input", { type: "checkbox", checked }), label);
  const tools = box("tools", model.tools);
  const vision = box("images", model.vision);
  const thinking = box("thinks", model.thinking);
  const context = el("input", { class: "input sm", type: "number", min: "1000", step: "1000", value: String(model.context), "aria-label": "Context size in tokens" });
  const remove = el("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Remove this model", onclick: onRemove }, icon("trash"));
  const element = el("div", { class: "custom-model" }, id, el("div", { class: "custom-model-caps" }, tools, vision, thinking), context, remove);
  return {
    element,
    read() {
      return {
        id: id.value.trim(),
        tools: tools.querySelector("input").checked,
        vision: vision.querySelector("input").checked,
        thinking: thinking.querySelector("input").checked,
        efforts: model.efforts ?? [],
        context: Number(context.value) || DEFAULT_CONTEXT,
        max_output: model.max_output ?? DEFAULT_OUTPUT,
      };
    },
  };
}

function blankModel(id) {
  return { id, tools: true, vision: false, thinking: false, efforts: [], context: DEFAULT_CONTEXT, max_output: DEFAULT_OUTPUT };
}

/**
 * The dialog that adds a provider (`existing` null) or changes one (`existing`: `{ name, config, key_set }`), and calls
 * `done()` once it is saved.
 */
export function openProviderForm({ existing, returnFocus, done }) {
  const config = existing?.config ?? null;
  const docs = el("input", { class: "input", type: "url", placeholder: "https://… the provider's API documentation", "aria-label": "Documentation address", autocomplete: "off", spellcheck: "false" });
  const fill = el("button", { class: "btn", type: "button" }, icon("sparkle"), "Fill from docs");
  const docsNote = el("div", { class: "note", hidden: true, role: "status" });
  const name = el("input", { class: "input mono", value: existing?.name ?? "", placeholder: "groq", "aria-label": "Name", autocomplete: "off", spellcheck: "false", maxlength: "32" });
  name.disabled = existing !== null;
  const baseUrl = el("input", { class: "input mono", type: "url", value: config?.base_url ?? "", placeholder: "https://api.example.com/v1", "aria-label": "Base URL", autocomplete: "off", spellcheck: "false" });
  const protocol = el(
    "select",
    { class: "select", "aria-label": "Protocol" },
    Object.entries(PROTOCOLS).map(([value, label]) => el("option", { value, selected: (config?.protocol ?? "chat") === value }, label)),
  );
  const needsKey = el("input", { type: "checkbox", checked: config === null ? true : config.api_key_env !== "" });
  const key = el("input", {
    class: "input mono",
    type: "password",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: existing?.key_set ? "Saved · type to replace it" : "Paste the key",
    "aria-label": "Key",
    maxlength: "1000",
  });
  const keyField = field("Key", key, "Kept on this Mac, sent to the base URL alone, never shown again.");
  const listModels = el("input", { type: "checkbox", checked: config?.list_models ?? false });
  const headers = el("textarea", {
    class: "textarea mono",
    rows: "2",
    spellcheck: "false",
    placeholder: "X-Title: Dot",
    "aria-label": "Extra headers",
  });
  headers.value = Object.entries(config?.headers ?? {})
    .map(([header, value]) => `${header}: ${value}`)
    .join("\n");

  const rows = [];
  const modelList = el("div", { class: "custom-models" });
  function addModel(model) {
    const row = modelRow(model, () => {
      rows.splice(rows.indexOf(row), 1);
      row.element.remove();
    });
    rows.push(row);
    modelList.append(row.element);
  }
  for (const model of config?.models ?? []) {
    addModel(model);
  }
  const addButton = el("button", { class: "btn sm", type: "button", onclick: () => addModel(blankModel("")) }, icon("plus"), "Add model");
  const outcome = el("div", { class: "note", hidden: true, role: "status" });
  const test = el("button", { class: "btn", type: "button" }, icon("refresh"), "Test");

  function paintKey() {
    keyField.hidden = !needsKey.checked;
  }
  needsKey.addEventListener("change", paintKey);
  paintKey();

  /** The provider as the form holds it, as the daemon takes it; throws with what to fix. */
  function provider() {
    const models = rows.map((row) => row.read()).filter((model) => model.id !== "");
    if (models.length === 0 && !listModels.checked) {
      throw new Error("Add a model, or let Dot list the provider's models.");
    }
    return {
      base_url: baseUrl.value.trim(),
      protocol: protocol.value,
      needs_key: needsKey.checked,
      headers: parseHeaders(headers.value),
      models,
      list_models: listModels.checked,
    };
  }

  /** The key typed in the form, or null for the saved one; throws for one with a space inside. */
  function typedKey() {
    const value = key.value.trim();
    if (!needsKey.checked || value === "") {
      return null;
    }
    if (!KEY_PATTERN.test(value)) {
      throw new Error("A key has no spaces or line breaks in it. Copy it again from the provider's page.");
    }
    return value;
  }

  function say(ok, title, detail) {
    outcome.className = ok ? "note ok" : "note bad";
    outcome.replaceChildren(icon(ok ? "check" : "alert"), el("span", null, el("strong", null, title), detail === "" ? "" : ` — ${detail}`));
    outcome.hidden = false;
  }

  fill.addEventListener("click", async () => {
    const url = docs.value.trim();
    if (!/^https?:\/\//.test(url)) {
      docs.focus();
      return;
    }
    fill.disabled = true;
    docsNote.className = "note";
    docsNote.replaceChildren(el("span", { class: "spinner" }), el("span", null, "Reading the page…"));
    docsNote.hidden = false;
    try {
      const proposal = await api.post("/providers/custom/docs", { url });
      if (proposal.base_url !== null) {
        baseUrl.value = proposal.base_url;
      }
      if (proposal.protocol !== null) {
        protocol.value = proposal.protocol;
      }
      const known = new Set(rows.map((row) => row.read().id));
      for (const model of proposal.models) {
        if (!known.has(model)) {
          addModel(blankModel(model));
        }
      }
      docsNote.className = "note";
      docsNote.replaceChildren(
        icon("info"),
        el(
          "span",
          null,
          proposal.source === "page" ? "Filled from the page. " : "Filled with the fast model's help. ",
          "Check each field before you save: nothing is saved yet.",
          ...proposal.notes.map((note) => el("span", { class: "custom-note" }, ` ${note}`)),
        ),
      );
    } catch (error) {
      docsNote.className = "note bad";
      docsNote.replaceChildren(icon("alert"), el("span", null, `The page could not be read: ${error.message}`));
    } finally {
      fill.disabled = false;
    }
  });

  test.addEventListener("click", async () => {
    let body;
    try {
      const shaped = provider();
      const first = shaped.models[0]?.id ?? "";
      body = { name: name.value.trim(), provider: shaped, key: typedKey(), model: shaped.list_models ? "" : first };
    } catch (error) {
      say(false, "Not ready to test", error.message);
      return;
    }
    test.disabled = true;
    outcome.className = "note";
    outcome.replaceChildren(el("span", { class: "spinner" }), el("span", null, "Asking the provider…"));
    outcome.hidden = false;
    try {
      const result = await api.post("/providers/custom/test", body);
      say(result.ok, result.ok ? "It answers" : "The test failed", result.detail);
    } catch (error) {
      say(false, "The test failed", error.message);
    } finally {
      test.disabled = false;
    }
  });

  const body = [
    el("div", { class: "custom-docs" }, field("Documentation", el("div", { class: "custom-docs-row" }, docs, fill), "Optional: Dot reads the page and proposes the fields."), docsNote),
    field("Name", name, existing === null ? "The prefix of its models, such as groq:llama-4-scout. Lower case; it cannot be changed later." : "The prefix of its models."),
    field("Base URL", baseUrl, "The address before /chat/completions or /messages, often ending in /v1. https, or http://localhost."),
    field("Protocol", protocol, "Dot speaks these three; nothing of a provider beyond them."),
    el("label", { class: "custom-check" }, needsKey, "It takes an API key"),
    keyField,
    el(
      "div",
      { class: "field" },
      el("span", { class: "field-label" }, "Models"),
      el("span", { class: "hint" }, "Each with its id as the provider names it, what it can do, and how many tokens it holds."),
      modelList,
      el("div", { class: "custom-models-foot" }, addButton, el("label", { class: "custom-check" }, listModels, "Also list the models it offers (GET /models)")),
    ),
    field("Extra headers", headers, "Optional, one per line. No keys here: a header named like a key or token is refused."),
    outcome,
  ];

  return formDialog({
    title: existing === null ? "Add a provider" : `Change ${existing.name}`,
    description: "Any service that speaks the OpenAI or Anthropic API. Dot sends it what a run on its models sends.",
    body,
    leading: [test],
    submitLabel: "Save",
    returnFocus,
    onSubmit: async () => {
      const chosen = name.value.trim();
      if (!customName(chosen)) {
        name.focus();
        throw new Error("The name is lower case letters, digits, _ and -, from a letter, and not a built-in provider's.");
      }
      const shaped = provider();
      const secret = typedKey();
      // A saved key goes to the address it was saved for alone: a new provider, a new address, or a key asked for again
      // after none, takes the key typed.
      const kept = config !== null && existing.key_set && config.api_key_env !== "" && originOf(shaped.base_url) === originOf(config.base_url);
      if (needsKey.checked && secret === null && !kept) {
        key.focus();
        throw new Error(
          config === null || !existing.key_set
            ? "Paste the provider's key, or untick “It takes an API key”."
            : "The address changed: paste the key again, so that Dot sends it to the new address knowingly.",
        );
      }
      const saved = await api.put(`/providers/custom/${encodeURIComponent(chosen)}`, shaped);
      if (secret !== null) {
        await api.post(`/keys/${encodeURIComponent(saved.config.api_key_env)}`, { value: secret });
      }
      key.value = "";
      toast("ok", `${chosen} saved. Its models are in the pickers now.`);
      await done(saved);
      return true;
    },
  });
}
