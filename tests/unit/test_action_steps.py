"""The acting steps of a run with what decided them (item 468): a step recorded before keeps no decision, never a
guess, and a step recorded since gives its decision and flags back."""

from dotagent.agent.authorization import StepDecision
from dotagent.server.actions import action_steps
from dotagent.server.views import ActionStep
from dotagent.store.models import EventRecord

_BEFORE: EventRecord = EventRecord(
    id=1,
    run_id="run-1",
    ts=1.0,
    kind="tool_result",
    data={"tool": "write_file", "ok": True, "text": "Wrote notes.md", "kind": "write", "target": "notes.md"},
)
_DECISION: StepDecision = StepDecision(
    verdict="allow", source="rule", reason="Notes are fine", rule_id="rul_1", answered_by="policy", preset="balanced"
)


def test_a_step_recorded_before_has_no_decision_and_one_recorded_since_has_its_own() -> None:
    since: EventRecord = _BEFORE.model_copy(
        update={
            "id": 2,
            "data": {**_BEFORE.data, "decision": _DECISION.model_dump(mode="json"), "flags": ["credential"]},
        }
    )
    steps: list[ActionStep] = action_steps([_BEFORE, since])
    assert [(step.decision, step.flags) for step in steps] == [(None, []), (_DECISION, ["credential"])]
