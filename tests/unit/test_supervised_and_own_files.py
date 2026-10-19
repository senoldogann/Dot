"""The approval modes of item 398: a change of Dot's own files is the user's question in every mode but yolo, which no
rule and no review model answers in the user's place, and Supervised asks before every change the preset lets through
while reads, a run's own notes and the user's own allow rules go on."""

from pathlib import Path

import pytest

from dotagent.policy.engine import YOLO_WAIVED_SOURCES, decide, route_for, supervised
from dotagent.policy.paths import CHANGES_DOT
from dotagent.policy.types import ActionInfo, ActionKind, ApprovalMode, Decision, Risk
from dotagent.store.models import Behavior, RuleRecord
from dotagent.tools.files import read_action, write_action


def _rule(tool_glob: str, behavior: Behavior) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior=behavior,
        tool_glob=tool_glob,
        match_regex="",
        description="the user's own answer",
        created_at=0.0,
    )


def _action(kind: ActionKind, risk: Risk) -> ActionInfo:
    return ActionInfo(tool="tool", kind=kind, risk=risk, summary=f"a {kind} action", target="", flags=())


def test_a_change_of_dots_own_files_asks_the_user_in_every_mode_but_yolo(tmp_path: Path) -> None:
    workspace: Path = tmp_path / "work"
    own: Path = tmp_path / "Dot" / "src"
    path: Path = own / "dotagent" / "server" / "static" / "css" / "app.css"
    write: ActionInfo = write_action("edit_file", path, workspace, (), (own,), "Edit")
    assert (write.risk, write.flags) == ("high", ("self_change",))
    assert write.summary == f"Edit {path} ({CHANGES_DOT})"
    decision: Decision = decide(write, "task", False, "aggressive", [], None)
    assert (decision.verdict, decision.source) == ("ask", "own_files"), decision
    # Never the review model's; a rule of the user that allows writes does not answer it either.
    modes: tuple[ApprovalMode, ...] = ("supervised", "ask", "auto")
    assert [route_for(decision, mode) for mode in modes] == ["user", "user", "user"]
    ruled: Decision = decide(write, "task", False, "aggressive", [_rule("edit_file", "allow")], None)
    assert ruled.source == "own_files"
    # Yolo waives it, as it waived the refusal before; a research run still writes nothing.
    assert "own_files" in YOLO_WAIVED_SOURCES
    assert decide(write, "research", False, "aggressive", [], None).verdict == "deny"
    # Reading Dot's own files is a read like any other.
    read: ActionInfo = read_action("read_file", path, workspace, (), (own,), "Read")
    assert decide(read, "task", False, "aggressive", [], None).verdict == "allow"


@pytest.mark.parametrize(
    ("kind", "risk", "verdict"),
    [
        ("read", "safe", "allow"),
        ("network", "safe", "allow"),
        ("browser_navigate", "low", "allow"),
        ("memory_write", "low", "allow"),
        ("notes_write", "low", "allow"),
        ("meta", "safe", "allow"),
        ("delegate", "low", "allow"),
        ("shell", "safe", "allow"),
        ("shell", "low", "ask"),
        ("write", "low", "ask"),
        ("browser_input", "low", "ask"),
        ("browser_click", "low", "ask"),
        ("desktop_input", "low", "ask"),
        ("app_control", "low", "ask"),
        ("message", "medium", "ask"),
        ("schedule", "low", "ask"),
        ("mcp", "low", "ask"),
    ],
)
def test_supervised_asks_before_every_change_and_lets_reads_through(kind: ActionKind, risk: Risk, verdict: str) -> None:
    action: ActionInfo = _action(kind, risk)
    allowed: Decision = decide(action, "task", False, "aggressive", [], None)
    assert allowed.verdict == "allow"
    decision: Decision = supervised(allowed, action)
    assert decision.verdict == verdict
    if verdict == "ask":
        assert (decision.source, route_for(decision, "supervised")) == ("supervised", "user")
    else:
        assert decision is allowed


def test_supervised_keeps_the_users_own_rules_and_every_stricter_answer() -> None:
    write: ActionInfo = _action("write", "low")
    ruled: Decision = decide(write, "task", False, "aggressive", [_rule("tool", "allow")], None)
    assert supervised(ruled, write) is ruled and ruled.verdict == "allow"
    asked: Decision = decide(write, "task", False, "cautious", [], None)
    assert supervised(asked, write) is asked and asked.source == "preset"
    refused: Decision = decide(write, "research", False, "aggressive", [], None)
    assert supervised(refused, write) is refused and refused.verdict == "deny"
