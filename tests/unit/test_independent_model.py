"""A checker must be from a known family different from the worker's."""

import pytest

from dotagent.llm.roles import independent_model


@pytest.mark.parametrize(
    ("worker", "checker", "eligible"),
    [
        ("claude_code:opus", "anthropic:claude-sonnet-5-5", False),
        ("ollama:deepseek-v4:cloud", "openrouter:deepseek/deepseek-v4", False),
        ("antigravity:gemini-3.8-flash-high", "google:gemini-3.8-pro", False),
        ("anthropic:claude-opus-5-5", "opencode_go:deepseek-v4.1-flash", True),
        ("ollama:local-experiment", "anthropic:claude-opus-5-5", False),
        ("anthropic:claude-opus-5-5", "", False),
    ],
)
def test_independence_uses_lineage_across_connectors(worker: str, checker: str, eligible: bool) -> None:
    assert independent_model(worker, checker) == (checker if eligible else None)
