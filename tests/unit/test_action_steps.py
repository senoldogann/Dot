"""The acting steps of a run with what decided them (item 468): a step recorded before keeps no decision, never a
guess, a step recorded since gives its decision and flags back, and one recorded before item 474 reads with its taint
not kept."""

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
    verdict="allow",
    source="rule",
    reason="Notes are fine",
    rule_id="rul_1",
    answered_by="policy",
    preset="balanced",
    tainted=True,
)


def test_a_step_recorded_before_has_no_decision_and_one_recorded_since_has_its_own() -> None:
    since: EventRecord = _BEFORE.model_copy(
        update={
            "id": 2,
            "data": {**_BEFORE.data, "decision": _DECISION.model_dump(mode="json"), "flags": ["credential"]},
        }
    )
    untold: dict[str, object] = {
        key: value for key, value in _DECISION.model_dump(mode="json").items() if key != "tainted"
    }
    between: EventRecord = _BEFORE.model_copy(
        update={"id": 3, "data": {**_BEFORE.data, "decision": untold, "flags": []}}
    )
    steps: list[ActionStep] = action_steps([_BEFORE, since, between])
    assert [(step.decision, step.flags) for step in steps] == [
        (None, []),
        (_DECISION, ["credential"]),
        (_DECISION.model_copy(update={"tainted": None}), []),
    ]
