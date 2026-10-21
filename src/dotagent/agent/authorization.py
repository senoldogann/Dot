"""What the policy said of a step that reached the gate (item 468), kept with the step's result: the verdict, where it
came from (a rule, the preset, the guard, the taint...), the rule when one decided, who answered the question when
there was one (nobody, as the policy let the step through; the owner; the review model; or yolo, which asks nothing),
the preset of the run, and whether the run was tainted when the gate decided (item 474). A step the gate refused or
handed off raised and has its ``policy`` event instead."""

from typing import Literal

from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.policy.types import Decision, DecisionSource, Preset, Verdict

# Who answered for a step that went ahead.
type Answerer = Literal["policy", "user", "reviewer", "yolo"]
# The policy's reason, as a step's record keeps it.
_REASON_CHARS: int = 300


class StepDecision(FrozenModel):
    verdict: Verdict
    source: DecisionSource
    reason: str
    rule_id: str | None
    answered_by: Answerer
    preset: Preset
    # Whether the run was tainted when the gate decided the step (item 474); ``None`` in a record written before, which
    # did not keep it.
    tainted: bool | None = None


class Authorization(FrozenModel):
    """A step let through: the note the owner wrote when approving it (empty when none), and what decided it."""

    note: str
    decision: StepDecision


def step_decision(decision: Decision, answered_by: Answerer, preset: Preset, tainted: bool) -> StepDecision:
    return StepDecision(
        verdict=decision.verdict,
        source=decision.source,
        reason=truncate_middle(decision.reason, _REASON_CHARS),
        rule_id=decision.rule_id,
        answered_by=answered_by,
        preset=preset,
        tainted=tainted,
    )
