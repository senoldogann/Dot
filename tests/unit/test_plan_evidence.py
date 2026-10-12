"""The evidence of a plan step: a long one is cut with an ellipsis at the limit rather than refused (item 117)."""

from dotagent.agent.plan import EVIDENCE_MAX_CHARS
from dotagent.tools.meta import MarkPlanStepArgs, PlanStep


def test_evidence_over_the_limit_is_cut_and_short_evidence_is_kept_as_it_is() -> None:
    long: str = "Read back the file: " + "line of output " * 60
    marked = MarkPlanStepArgs.model_validate({"step": 1, "status": "done", "evidence": f"  {long}  "})
    assert EVIDENCE_MAX_CHARS - 2 <= len(marked.evidence) <= EVIDENCE_MAX_CHARS
    assert marked.evidence.startswith("Read back the file: line of output")
    assert marked.evidence.endswith("…")
    planned = PlanStep.model_validate({"text": "Read it", "status": "done", "evidence": "  wc -l printed 42  "})
    assert planned.evidence == "wc -l printed 42"
