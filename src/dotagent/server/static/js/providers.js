// The model providers and the typed judge's service: the daemon and the configuration call them by their ids, the
// page by the names people know.

import { el } from "./dom.js";

const LABELS = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  opencode_go: "OpenCode Go",
  opencode: "OpenCode",
  ollama: "Ollama",
  typesafe: "TypeSafe",
};

// The providers with a mark of their own under static/icons/providers (item 108), OpenCode's free models wearing OpenCode
// Go's; the rest wear the generic one.
const MARKED = new Set(["anthropic", "openai", "ollama", "opencode_go", "opencode"]);

/**
 * The mark of a provider: a span painted by the page as a mask of the provider's SVG (`static/icons/providers/`), in
 * the colour of the text around it; `data-provider` names the mark. An unknown provider wears the generic mark.
 */
export function providerMark(provider) {
  return el("span", { class: "provider-mark", "data-provider": MARKED.has(provider) ? provider : "generic", "aria-hidden": "true" });
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
