"""The pure parts of proof checks (item 1853): how a check's call is read, what the run is told, what ends it, and who
may carry checks."""

import pytest

from dotagent.agent.loop import RunOutcome, SendBack, _after_proof, _after_proof_at_limit
from dotagent.agent.proof import (
    ProofOutcome,
    added,
    claim_refusal,
    failed_note,
    limit_refused,
    output_tail,
    over_limit,
    proof_end,
    proof_refused,
)
from dotagent.store.endings import GOAL_CHECK_MARK
from dotagent.store.models import MAX_PROOF_CHECKS, ProofCheck

TESTS: ProofCheck = ProofCheck(command="uv run pytest -q", timeout_s=300, by="owner")
FILE: ProofCheck = ProofCheck(command="test -s report.pdf", timeout_s=10, by="run")
ENDED: RunOutcome = RunOutcome(status="done", result="All done.", needs="")


def _outcome(check: ProofCheck, end: str, text: str) -> ProofOutcome:
    return ProofOutcome.model_validate({"check": check.model_dump(), "end": end, "text": text})


def test_a_check_passes_on_exit_0_fails_otherwise_and_did_not_run_when_refused_or_never_reaching_its_tool() -> None:
    assert proof_end(is_error=False, refused=False, reached_tool=True) == "passed"
    # An exit other than 0, or the kill at its time limit, is an error result of the shell tool.
    assert proof_end(is_error=True, refused=False, reached_tool=True) == "failed"
    assert proof_end(is_error=True, refused=True, reached_tool=True) == "not_run"
    assert proof_end(is_error=True, refused=False, reached_tool=False) == "not_run"


def test_checks_are_only_added_each_command_once_and_never_past_the_limit() -> None:
    assert added((TESTS,), (TESTS, FILE, FILE)) == (FILE,)
    many: tuple[ProofCheck, ...] = tuple(
        ProofCheck(command=f"test -f {index}", timeout_s=1, by="run") for index in range(MAX_PROOF_CHECKS)
    )
    with pytest.raises(ValueError, match=f"at most {MAX_PROOF_CHECKS} proof checks"):
        added(many, (TESTS,))
    # One already held is no addition, even at the limit.
    assert added(many, (many[0],)) == ()


def test_only_a_task_of_dots_own_loop_carries_checks() -> None:
    assert proof_refused("task", "ollama") is None
    assert proof_refused("chat", "ollama") is not None
    assert proof_refused("research", "ollama") is not None
    assert proof_refused("task", "claude_code") is not None
    assert proof_refused("task", "opencode") is not None


def test_a_failing_check_sends_the_run_back_with_the_end_of_its_output_until_patience_runs_out() -> None:
    long_output: str = "x" * 5000 + "\nFAILED tests/test_a.py::test_b"
    outcomes: tuple[ProofOutcome, ...] = (
        _outcome(TESTS, "failed", f"[exit 1 in 2.0s] {long_output}"),
        _outcome(FILE, "passed", "[exit 0 in 0.0s]"),
    )
    sent: RunOutcome | SendBack | None = _after_proof(outcomes, ENDED, 0, 4)
    assert isinstance(sent, SendBack)
    assert sent.note == failed_note(outcomes)
    assert "`uv run pytest -q` (limit 300 s)" in sent.note and "FAILED tests/test_a.py::test_b" in sent.note
    assert "test -s report.pdf" not in sent.note
    assert "characters before]" in sent.note
    ended: RunOutcome | SendBack | None = _after_proof(outcomes, ENDED, 4, 4)
    assert isinstance(ended, RunOutcome) and ended.status == "failed"
    assert ended.result == (
        f"All done.{GOAL_CHECK_MARK}the proof did not pass (`uv run pytest -q` still fail) and after 4 rounds of "
        "further work."
    )


def test_every_check_passing_leaves_the_end_to_the_goal_check_and_one_that_could_not_run_ends_the_run() -> None:
    passed: tuple[ProofOutcome, ...] = (_outcome(TESTS, "passed", "[exit 0 in 1.0s] 12 passed"),)
    assert _after_proof(passed, ENDED, 0, 4) is None
    assert _after_proof_at_limit(passed, ENDED) is None
    refused: tuple[ProofOutcome, ...] = (_outcome(TESTS, "not_run", "The owner said no."),)
    for ended in (_after_proof(refused, ENDED, 0, 4), _after_proof_at_limit(refused, ENDED)):
        assert isinstance(ended, RunOutcome) and ended.status == "failed"
        assert ended.result.endswith(
            "the proof could not run, so the answer is not proven: `uv run pytest -q`: The owner said no."
        )
    failed: tuple[ProofOutcome, ...] = (_outcome(TESTS, "failed", "[exit 1 in 1.0s]"),)
    at_limit: RunOutcome | None = _after_proof_at_limit(failed, ENDED)
    assert at_limit is not None and at_limit.result.endswith("and the run's limit is reached.")


def test_a_campaigns_claim_is_refused_with_the_output_or_told_to_ask_the_owner() -> None:
    failed: str = claim_refusal((_outcome(TESTS, "failed", "[exit 1 in 1.0s] 1 failed"),))
    assert failed.startswith("Not accepted: the campaign's proof does not pass.") and "1 failed" in failed
    not_run: str = claim_refusal((_outcome(TESTS, "not_run", "the session has no shell tool"),))
    assert "ask the owner with ask_user" in not_run


def test_the_tail_keeps_the_end_and_says_what_it_dropped() -> None:
    assert output_tail("short", 10) == "short"
    assert output_tail("abcdefghij", 4) == "[… 6 characters before]\nghij"


def test_a_check_past_the_shells_limit_is_refused_as_set_and_does_not_run() -> None:
    long_check: ProofCheck = ProofCheck(command="make soak", timeout_s=3600, by="owner")
    assert over_limit((TESTS, long_check), 1800) == (long_check,)
    assert limit_refused((TESTS,), 1800) is None
    refused: str | None = limit_refused((TESTS, long_check), 1800)
    assert (
        refused is not None and "`make soak` (limit 3600 s)" in refused and "[shell] max_timeout_s (1800 s)" in refused
    )
