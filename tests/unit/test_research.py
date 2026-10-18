"""What a research run may do beyond observing (item 34): keep its notes file and memory notes, and the engines the
search ladder ``auto`` climbs (item 59)."""

from pathlib import Path

import pytest

from dotagent.policy.engine import mode_gate
from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.tools.notes import notes_path
from dotagent.web.ladder import SearchEngine
from dotagent.web.search import AnthropicRung, SearchChoice, search_ladder


def _action(kind: ActionKind) -> ActionInfo:
    return ActionInfo(tool="t", kind=kind, risk="low", summary="s", target="x", flags=())


@pytest.mark.parametrize(
    ("kind", "denied"),
    [("notes_write", False), ("memory_write", False), ("read", False), ("write", True), ("shell", True)],
)
def test_a_research_run_keeps_notes_and_memory_but_changes_nothing_else(kind: ActionKind, denied: bool) -> None:
    decision = mode_gate(_action(kind), "research")
    assert (decision is not None) == denied
    assert mode_gate(_action(kind), "task") is None


@pytest.mark.parametrize(
    ("choice", "key", "anthropic", "browser", "searxng", "ladder"),
    [
        ("auto", True, None, True, False, ("ollama", "duckduckgo", "brave", "browser")),
        ("auto", True, "auto", True, False, ("ollama", "anthropic", "duckduckgo", "brave", "browser")),
        ("auto", True, "first", False, False, ("anthropic", "ollama", "duckduckgo", "brave")),
        ("auto", False, "auto", False, False, ("anthropic", "duckduckgo", "brave")),
        ("auto", True, "off", False, False, ("ollama", "duckduckgo", "brave")),
        ("auto", False, None, False, False, ("duckduckgo", "brave")),
        ("auto", True, "auto", True, True, ("searxng", "ollama", "anthropic", "duckduckgo", "brave", "browser")),
        ("duckduckgo", True, "auto", True, True, ("duckduckgo",)),
        ("brave", False, None, False, False, ("brave",)),
        ("ollama", False, None, True, False, ("ollama",)),
        ("anthropic", False, None, True, False, ("anthropic",)),
        ("searxng", False, None, False, True, ("searxng",)),
    ],
)
def test_auto_climbs_the_whole_ladder_and_a_named_engine_stands_alone(
    choice: SearchChoice,
    key: bool,
    anthropic: AnthropicRung | None,
    browser: bool,
    searxng: bool,
    ladder: tuple[SearchEngine, ...],
) -> None:
    assert search_ladder(choice, key, anthropic, browser, searxng) == ladder


def test_the_notes_of_a_run_are_one_file_named_by_the_run() -> None:
    workspace: Path = Path("/w")
    assert notes_path(workspace, "run_1") == workspace / "notes" / "run_1.md"
