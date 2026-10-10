// The model providers and the typed judge's service: the daemon and the configuration call them by their ids, the
// page by the names people know.

const LABELS = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  opencode_go: "OpenCode Go",
  ollama: "Ollama",
  typesafe: "TypeSafe",
};

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
