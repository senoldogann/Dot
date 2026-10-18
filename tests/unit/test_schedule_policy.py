"""How schedules reach the policy and the model: creating one is persistence, listing is free, scheduled runs know that
nobody is watching."""

from pathlib import Path

import pytest

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo, ActionKind, Decision, Flag, Preset, Risk, Verdict
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.models import RunMode


def _action(tool: str, kind: ActionKind, risk: Risk, flags: tuple[Flag, ...]) -> ActionInfo:
    return ActionInfo(tool=tool, kind=kind, risk=risk, summary="a schedule", target="schedules", flags=flags)


_CREATE: ActionInfo = _action("schedule_create", "schedule", "medium", ("persistence",))
_DELETE: ActionInfo = _action("schedule_delete", "schedule", "low", ("persistence",))
_LIST: ActionInfo = _action("schedule_list", "meta", "safe", ())


@pytest.mark.parametrize(
    ("info", "mode", "tainted", "preset", "verdict", "source"),
    [
        (_CREATE, "task", False, "aggressive", "allow", "preset"),
        (_CREATE, "task", False, "balanced", "allow", "preset"),
        (_CREATE, "task", False, "cautious", "ask", "preset"),
        (_CREATE, "task", True, "aggressive", "ask", "taint"),
        (_CREATE, "task", True, "balanced", "ask", "taint"),
        (_CREATE, "research", False, "aggressive", "deny", "mode"),
        (_DELETE, "task", False, "aggressive", "allow", "preset"),
        (_DELETE, "task", True, "aggressive", "ask", "taint"),
        (_DELETE, "research", False, "aggressive", "deny", "mode"),
        (_LIST, "research", True, "aggressive", "allow", "preset"),
        (_LIST, "task", True, "cautious", "allow", "preset"),
    ],
)
def test_schedules_follow_the_policy_layers(
    info: ActionInfo, mode: RunMode, tainted: bool, preset: Preset, verdict: Verdict, source: str
) -> None:
    decision: Decision = decide(info, mode, tainted, preset, [], None)
    assert (decision.verdict, decision.source) == (verdict, source), decision.reason


def _prompt(source: str) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            source,
            "aggressive",
            "ask",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            [],
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            None,
            "full",
        )
    )


def test_a_scheduled_run_is_told_that_nobody_is_watching() -> None:
    scheduled: str = _prompt(SCHEDULE_SOURCE)
    typed: str = _prompt("cli")
    assert "started by a schedule and nobody is watching" in scheduled
    assert "started by a schedule" not in typed
    assert "# Schedules" in scheduled
    assert "# Schedules" in typed
