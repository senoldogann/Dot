// The model providers and the typed judge's service: the daemon and the configuration call them by their ids, the
// page by the names people know.

import { el } from "./dom.js";
import { drawInked } from "./inked.js";

const LABELS = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  opencode_go: "OpenCode Go",
  openrouter: "OpenRouter",
  opencode: "OpenCode",
  claude_code: "Claude Code",
  ollama: "Ollama",
  typesafe: "TypeSafe",
};

// The providers with a mark of their own under static/icons/providers (item 108), OpenCode's free models wearing OpenCode
// Go's and the owner's Claude plan Anthropic's (item 1013); the rest wear the generic one.
const MARKED = new Set(["anthropic", "openai", "ollama", "opencode_go", "openrouter", "opencode", "claude_code"]);

// The file each marked provider wears, OpenCode's free models OpenCode Go's, Claude Code Anthropic's.
const FILES = { opencode: "opencode_go", claude_code: "anthropic" };

// The providers whose models answer only as an agent program of their own, to which a run goes whole (items 283 and
// 1013, `WHOLE_RUN_AGENTS` in llm/types.py): the program keeps its own window, and none can be a fallback model.
export const WHOLE_RUN = new Set(["opencode", "claude_code"]);

/** Whether a model of `provider` can do the work of `role` of Dot's own work (`answers_role` in llm/types.py): one that
 * answers only as its own agent cannot, but the owner's Claude plan may answer the review model's single questions,
 * and those of the improvement loop's roles, which play on it (items 1032 and 1033). */
export function answersRole(role, provider) {
  return !WHOLE_RUN.has(provider) || (role === "review" && provider === "claude_code");
}

/** Whether a run on a model of `provider` has Dot's tools (`runs_dots_tools` in llm/types.py): any but Zen's free
 * models, whose OpenCode agent gets a part of them; the Claude plan's run has every one (item 1063). */
export function runsDotsTools(provider) {
  return !WHOLE_RUN.has(provider) || provider === "claude_code";
}

/**
 * The mark of a provider: the provider's SVG (`static/icons/providers/`) drawn inline in the colour of the text around
 * it (item 959, `inked.js`); `data-provider` names the mark. An unknown provider wears the generic mark.
 * @param {string} provider
 */
export function providerMark(provider) {
  const named = MARKED.has(provider) ? provider : "generic";
  const mark = el("span", { class: "provider-mark", "data-provider": named, "aria-hidden": "true" });
  return drawInked(mark, `/icons/providers/${FILES[named] ?? named}.svg`);
}

/** The name of a provider as people know it, for headings and menus. */
export function providerLabel(provider) {
  const label = LABELS[provider];
  if (label === undefined) {
    throw new Error(`unknown provider: ${provider}`);
  }
  return label;
}

/** The provider of a model reference such as "openai:gpt-5.5" ("" when the reference names none). */
export function providerOf(ref) {
  const at = ref.indexOf(":");
  return at === -1 ? "" : ref.slice(0, at);
}
