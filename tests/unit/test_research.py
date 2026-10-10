"""What a research run may do beyond observing (item 34): keep its notes file and memory notes, and the search backend
``auto`` resolves to."""

from pathlib import Path

import pytest

from dotagent.policy.engine import mode_gate
from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.tools.notes import notes_path
from dotagent.web.search import SearchBackend, SearchChoice, resolved_backend


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
    ("choice", "key", "backend"),
    [
        ("auto", True, "ollama"),
        ("auto", False, "duckduckgo"),
        ("duckduckgo", True, "duckduckgo"),
        ("ollama", False, "ollama"),
    ],
)
def test_auto_is_the_hosted_search_with_its_key_and_duckduckgo_without(
    choice: SearchChoice, key: bool, backend: SearchBackend
) -> None:
    assert resolved_backend(choice, key) == backend


def test_the_notes_of_a_run_are_one_file_named_by_the_run() -> None:
    workspace: Path = Path("/w")
    assert notes_path(workspace, "run_1") == workspace / "notes" / "run_1.md"
