"""A rule tried on past steps (item 469), as a pure count: which steps it fits, which an earlier rule decides first,
which no rule decides (a yolo run's, and what the policy settles before any rule), how what it would decide compares
with what was decided then, the examples masked and the newest twenty, the rule that decides being the one the policy
picks, and a pattern that takes too long stopping the count with the problem said."""

import time

import pytest

from dotagent.agent.authorization import StepDecision
from dotagent.core.types import JsonObject
from dotagent.policy import engine
from dotagent.policy.types import ActionInfo, Decision, DecisionSource
from dotagent.server.rule_replay import CANDIDATE_ID, Tally, candidate_rule, preview_of, tally_steps
from dotagent.server.views import RulePreview
from dotagent.store.models import Behavior, RuleRecord, StepRow

_TOKEN: str = "ghp_" + "a1B2c3D4e5" * 4
_NO_DEADLINE: float = float("inf")


def _decision(verdict: str, source: DecisionSource, answered_by: str) -> JsonObject:
    return StepDecision.model_validate(
        {
            "verdict": verdict,
            "source": source,
            "reason": "what decided it",
            "rule_id": None,
            "answered_by": answered_by,
            "preset": "balanced",
        }
    ).model_dump(mode="json")


def _step(event_id: int, summary: str, decision: JsonObject | None, recorded: bool, yolo: bool) -> StepRow:
    return StepRow(
        event_id=event_id,
        run_id="run-1",
        at=1_760_000_000.0 + event_id,
        tool="shell",
        kind="shell",
        risk="medium",
        summary=summary,
        target=summary.removeprefix("Run: "),
        ok=True,
        decision=decision,
        recorded=recorded,
        yolo=yolo,
    )


def _rule(position: int, behavior: str, tool_glob: str, match_regex: str, description: str) -> RuleRecord:
    return RuleRecord.model_validate(
        {
            "id": f"rul_{position}",
            "position": position,
            "enabled": True,
            "behavior": behavior,
            "tool_glob": tool_glob,
            "match_regex": match_regex,
            "description": description,
            "created_at": 0.0,
        }
    )


def _tried(behavior: Behavior, existing: list[RuleRecord], steps: list[StepRow]) -> RulePreview:
    tally: Tally = Tally()
    assert tally_steps(
        tally, existing, candidate_rule(behavior, "shell", r"\bgit push\b", existing), steps, _NO_DEADLINE
    )
    return preview_of(tally, existing, False)


def test_a_rule_counts_what_it_fits_what_it_decides_and_what_an_earlier_rule_decides_first() -> None:
    existing: list[RuleRecord] = [_rule(1, "handoff", "shell", r"--force", "Force pushes are mine")]
    candidate: RuleRecord = candidate_rule("allow", "shell", r"\bgit push\b", existing)
    assert (candidate.id, candidate.position) == (CANDIDATE_ID, 2)
    renamed: StepRow = _step(2, "Run: git push", None, False, False).model_copy(update={"kind": "renamed_kind"})
    written: StepRow = _step(3, "Write notes.md", None, False, False).model_copy(
        update={"tool": "write_file", "kind": "write"}
    )
    steps: list[StepRow] = [
        _step(6, "Run: git push origin main", _decision("ask", "preset", "user"), True, False),
        _step(5, "Run: git push --force origin main", _decision("handoff", "rule", "user"), True, False),
        _step(4, "Run: ls", _decision("allow", "preset", "policy"), True, False),
        written,
        renamed,
        _step(1, f"Run: git push https://{_TOKEN}@github.com/o/r", None, False, False),
    ]
    preview: RulePreview = _tried("allow", existing, steps)
    assert (preview.scanned, preview.fits, preview.would_decide) == (6, 3, 2)
    assert [(shadow.rule_id, shadow.description, shadow.steps) for shadow in preview.shadowed_by] == [
        ("rul_1", "Force pushes are mine", 1)
    ]
    # The push that asked would now go ahead: looser; the old step kept no decision.
    assert (preview.stricter, preview.looser, preview.same, preview.stopped, preview.unknown) == (0, 1, 0, 0, 1)
    assert [example.event_id for example in preview.examples] == [6, 5, 1]
    assert all(_TOKEN not in f"{example.summary} {example.target}" for example in preview.examples)
    assert preview.since == steps[-1].at and preview.problem is None


def test_what_no_rule_decides_is_counted_apart_and_the_rest_against_what_was_decided() -> None:
    steps: list[StepRow] = [
        # A yolo run asks nothing whatever the rules say.
        _step(9, "Run: git push", _decision("ask", "preset", "yolo"), True, True),
        _step(8, "Run: git push", _decision("allow", "preset", "policy"), True, True),
        # The guard of messages and posts gives way to a hand-off rule only; money and credentials to none.
        _step(7, "Run: git push", _decision("ask", "guard", "user"), True, False),
        _step(6, "Run: git push", _decision("handoff", "hard_stop", "user"), True, False),
        # The taint makes an allow a question again; a pre-approval it does not.
        _step(5, "Run: git push", _decision("ask", "taint", "user"), True, False),
        # Asked by the preset in Supervised: a rule of the owner's stands in its place.
        _step(4, "Run: git push", _decision("ask", "supervised", "user"), True, False),
        # Refused, declined or handed off: the gate kept no decision; and one from before item 468.
        _step(3, "Run: git push", None, True, False),
        _step(2, "Run: git push", None, False, False),
    ]
    allowed: RulePreview = _tried("allow", [], steps)
    assert (allowed.fits, allowed.in_yolo, allowed.before_rules, allowed.would_decide) == (8, 2, 2, 4)
    assert (allowed.stricter, allowed.looser, allowed.same, allowed.stopped, allowed.unknown) == (0, 1, 1, 1, 1)
    handed: RulePreview = _tried("handoff", [], steps)
    assert (handed.in_yolo, handed.before_rules, handed.would_decide) == (2, 1, 5)
    assert (handed.stricter, handed.looser, handed.same, handed.stopped, handed.unknown) == (3, 0, 0, 1, 1)
    preapproved: RulePreview = _tried("preapproved", [], steps)
    assert (preapproved.looser, preapproved.same) == (2, 0)


def test_the_rule_that_decides_is_the_one_the_policy_picks() -> None:
    """The try-out finds the rule that decides as the policy does; were the policy to pick otherwise (a hand-off rule
    before an earlier one, say), this fails, and the try-out must follow it."""
    existing: list[RuleRecord] = [
        _rule(1, "allow", "shell", r"\bgit status\b", "Status is fine"),
        _rule(2, "ask", "shell", r"--force", "Ask before forcing"),
    ]
    for behavior in ("allow", "preapproved", "ask", "handoff"):
        candidate: RuleRecord = candidate_rule(behavior, "shell", r"\bgit\b", existing)
        for summary in ("Run: git status", "Run: git push --force", "Run: git push", "Run: ls"):
            tally: Tally = Tally()
            tally_steps(tally, existing, candidate, [_step(1, summary, None, False, False)], _NO_DEADLINE)
            info: ActionInfo = ActionInfo(
                tool="shell", kind="shell", risk="medium", summary=summary, target=summary, flags=()
            )
            decided: Decision = engine.decide_past_review(info, "task", False, "balanced", [*existing, candidate], None)
            assert (tally.would_decide == 1) == (decided.rule_id == CANDIDATE_ID), (behavior, summary)
            assert sum(tally.shadowed_by.values()) == (decided.rule_id in {"rul_1", "rul_2"}), (behavior, summary)


def test_the_examples_are_the_newest_twenty_and_a_slice_ends_at_its_deadline() -> None:
    steps: list[StepRow] = [_step(event_id, "Run: git push", None, False, False) for event_id in range(30, 0, -1)]
    preview: RulePreview = _tried("ask", [], steps)
    assert preview.fits == 30
    assert [example.event_id for example in preview.examples] == list(range(30, 10, -1))
    tally: Tally = Tally()
    candidate: RuleRecord = candidate_rule("ask", "shell", "", [])
    # A slice whose time is up counts one step, so that a try-out always goes on.
    assert tally_steps(tally, [], candidate, steps, time.monotonic() - 1.0) == 1
    assert tally.scanned == 1


def test_a_pattern_that_takes_too_long_stops_the_count_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "RULE_MATCH_TIMEOUT_S", 0.05)
    candidate: RuleRecord = candidate_rule("ask", "shell", r"(a|aa)+$", [])
    steps: list[StepRow] = [
        _step(2, "Run: " + "a" * 60 + "b", None, False, False),
        _step(1, "Run: aaa", None, False, False),
    ]
    tally: Tally = Tally()
    assert tally_steps(tally, [], candidate, steps, _NO_DEADLINE) == 1
    assert tally.problem is not None and "The daemon would refuse such a step" in tally.problem
    assert tally.scanned == 1 and tally.fits == 0
