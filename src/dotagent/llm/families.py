"""Model lineages for an independent result check."""

_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude", ("claude",)),
    ("gemini", ("gemini",)),
    ("deepseek", ("deepseek",)),
    ("qwen", ("qwen",)),
    ("glm", ("glm",)),
    ("muse", ("muse-spark",)),
    ("minimax", ("minimax",)),
    ("openai", ("gpt", "chatgpt", "codex", "o1", "o3", "o4")),
    ("gemma", ("gemma",)),
    ("mistral", ("mistral", "mixtral", "codestral", "devstral")),
    ("llama", ("llama",)),
    ("kimi", ("kimi",)),
    ("grok", ("grok",)),
)


def model_family(ref: str) -> str | None:
    """Identify a known model lineage across providers and hosted namespaces."""
    provider, _, name = ref.lower().partition(":")
    model: str = name.strip().rsplit("/", 1)[-1].partition(":")[0]
    if provider.strip() == "claude_code":
        return "claude"
    for family, prefixes in _FAMILIES:
        if any(model.startswith(prefix) for prefix in prefixes):
            return family
    return None
