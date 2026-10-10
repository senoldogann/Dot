"""How the policy treats memory actions: reading is free, writing is gated by untrusted content and by key-like text."""

import pytest

from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo, ActionKind, Decision, Flag, Preset, Risk, Verdict
from dotagent.store.models import RunMode


def _action(kind: ActionKind, risk: Risk, flags: tuple[Flag, ...]) -> ActionInfo:
    return ActionInfo(tool="memory_test", kind=kind, risk=risk, summary="a note", target="memory", flags=flags)


_WRITE: ActionInfo = _action("memory_write", "low", ("persistence",))
_READ: ActionInfo = _action("memory_read", "safe", ())


@pytest.mark.parametrize(
    ("info", "mode", "tainted", "preset", "verdict", "source"),
    [
        (_WRITE, "task", False, "aggressive", "allow", "preset"),
        (_WRITE, "task", False, "balanced", "allow", "preset"),
        (_WRITE, "task", True, "aggressive", "ask", "taint"),
        (_WRITE, "task", True, "balanced", "ask", "taint"),
        (_WRITE, "task", False, "cautious", "ask", "preset"),
        (
            _action("memory_write", "low", ("persistence", "exfiltration")),
            "task",
            False,
            "aggressive",
            "deny",
            "auto_review",
        ),
        (_WRITE, "research", False, "aggressive", "allow", "preset"),
        (_READ, "research", True, "aggressive", "allow", "preset"),
        (_READ, "task", True, "cautious", "allow", "preset"),
    ],
)
def test_memory_actions_follow_the_policy_layers(
    info: ActionInfo, mode: RunMode, tainted: bool, preset: Preset, verdict: Verdict, source: str
) -> None:
    decision: Decision = decide(info, mode, tainted, preset, [], None)
    assert (decision.verdict, decision.source) == (verdict, source), decision.reason
