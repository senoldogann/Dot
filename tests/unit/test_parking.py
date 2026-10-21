"""Steps kept for the owner (items 482, 483), the pure part: which runs keep a hand-off nobody answers, which questions
may wait for the owner's yes, the fingerprint that keeps the same step once in a chat, and what the run is told."""

from pathlib import Path
from typing import Literal

import pytest

from dotagent.agent.parking import (
    Parking,
    allowed_text,
    handed_back_text,
    kept_ask_text,
    kept_digest,
    kept_text,
    parking_for,
    permit_parkable,
    skipped_text,
)
from dotagent.config import PolicyConfig, load_default_config
from dotagent.policy.types import ActionInfo, Amount, ApprovalMode, Decision, DecisionSource, Verdict
from dotagent.tools.shell import ShellArgs, _classify_shell

_PAY: ActionInfo = ActionInfo(
    tool="shell",
    kind="shell",
    risk="high",
    summary="pay the invoice of October",
    target="./pay.sh --invoice 1042",
    flags=("financial", "external_send"),
)


def _policy(unanswered: Literal["refuse", "park"]) -> PolicyConfig:
    return load_default_config().policy.model_copy(update={"unanswered": unanswered, "parked_hours": 2.0})


@pytest.mark.parametrize("approvals", ["supervised", "ask", "auto"])
def test_a_run_that_asks_keeps_its_hand_offs_where_park_is_set(approvals: ApprovalMode) -> None:
    assert parking_for(_policy("park"), approvals, False, "thr-1", None, "sch-1") == Parking(
        thread_id="thr-1", campaign_id=None, schedule_id="sch-1", keep_s=7200.0
    )
    # A session of a campaign keeps them in the campaign's conversation, shared by its sessions (item 484).
    assert parking_for(_policy("park"), approvals, False, "thr-c", "cmp-1", None) == Parking(
        thread_id="thr-c", campaign_id="cmp-1", schedule_id=None, keep_s=7200.0
    )
    assert parking_for(_policy("refuse"), approvals, False, "thr-1", None, "sch-1") is None


@pytest.mark.parametrize(("approvals", "evaluated"), [("yolo", False), ("ask", True)])
def test_yolo_and_an_eval_keep_nothing(approvals: ApprovalMode, evaluated: bool) -> None:
    """A delegated run keeps where the run that delegated it does (item 485), which the manager passes down."""
    assert parking_for(_policy("park"), approvals, evaluated, "thr-1", None, None) is None


def test_the_fingerprint_is_the_step_as_its_card_showed_it() -> None:
    assert kept_digest(_PAY) == kept_digest(_PAY.model_copy(update={"flags": ("external_send", "financial")}))
    for changed in (
        {"tool": "terminal"},
        {"summary": "pay the invoice of November"},
        {"target": "./pay.sh --invoice 1043"},
        {"flags": ("financial",)},
        {"amount": Amount(value=120.0, currency="EUR")},
        {"changes": ("/Users/me/invoices/1042.txt",)},
        {"bundle": "/Applications/Bank.app"},
    ):
        assert kept_digest(_PAY.model_copy(update=changed)) != kept_digest(_PAY), changed


@pytest.mark.parametrize(
    "changed",
    [{"kind": "write"}, {"summary": "pay the invoice of Octo…"}, {"target": "pay.sh --card ***"}],
)
def test_a_step_whose_card_shows_less_than_all_of_it_has_no_fingerprint(changed: dict[str, str]) -> None:
    assert kept_digest(_PAY.model_copy(update=changed)) is None


def test_what_the_run_is_told_of_a_kept_step_and_of_the_owner_doing_it() -> None:
    assert "(apr-1)" in kept_text("apr-1", True)
    assert "waits for the user already (apr-1)" in kept_text("apr-1", False)
    said: str = "The user did the step that waited for them (apr-1) themselves; go on from there."
    assert handed_back_text("apr-1", "  ") == said
    assert handed_back_text("apr-1", " Paid by card. ") == f"{said} They wrote: Paid by card."


def _asked(verdict: Verdict, source: DecisionSource) -> Decision:
    return Decision(verdict=verdict, source=source, reason="asked", rule_id=None)


_REPORT: ActionInfo = ActionInfo(
    tool="shell",
    kind="shell",
    risk="medium",
    summary="Run: ./report.sh (writes a file)",
    target="./report.sh",
    flags=(),
)


@pytest.mark.parametrize("source", ["rule", "preset", "supervised", "mode"])
def test_a_question_whose_card_shows_all_of_a_step_that_reaches_nobody_may_wait_for_a_yes(
    source: DecisionSource,
) -> None:
    assert permit_parkable(_REPORT, _asked("ask", source))


@pytest.mark.parametrize(
    ("changed", "decision"),
    [
        # A hand-off is the owner's to do, a refusal is no question; the run's untrusted reading and Dot's own files.
        ({}, _asked("handoff", "rule")),
        ({}, _asked("deny", "hard_stop")),
        ({}, _asked("ask", "taint")),
        ({}, _asked("ask", "own_files")),
        # A page, a message, an MCP call; a step that reaches other people; a card that shows less than all of it.
        ({"kind": "browser_click"}, _asked("ask", "rule")),
        ({"kind": "message"}, _asked("ask", "rule")),
        ({"kind": "mcp"}, _asked("ask", "rule")),
        ({"flags": ("external_send",)}, _asked("ask", "rule")),
        ({"flags": ("upload",)}, _asked("ask", "rule")),
        ({"summary": "Run: ./report.sh --to …"}, _asked("ask", "rule")),
    ],
)
def test_no_other_question_waits_for_a_yes(changed: dict[str, object], decision: Decision) -> None:
    assert not permit_parkable(_REPORT.model_copy(update=changed), decision)


def test_what_the_run_is_told_of_a_question_kept_allowed_or_skipped() -> None:
    assert "kept for the user to allow (apr-2)" in kept_ask_text("apr-2", True)
    assert "waits for the user's yes already (apr-2)" in kept_ask_text("apr-2", False)
    assert allowed_text("apr-2", "") == (
        "The user allowed the step that waited for their yes (apr-2): do it now, exactly as it was, once; it is not "
        "allowed again."
    )
    assert allowed_text("apr-2", " Go ahead. ").endswith(" They wrote: Go ahead.")
    assert "skipped this exact step when it was kept for them (apr-2)" in skipped_text("apr-2")


def test_a_command_run_somewhere_else_is_another_step(tmp_path: Path) -> None:
    """Where a command runs, when the call names it, is on its card and in its fingerprint (item 483): a yes to it in
    one folder is no yes to the same line in another."""
    here, there = tmp_path / "here", tmp_path / "there"
    steps: list[ActionInfo] = [
        _classify_shell(ShellArgs(command="./cleanup.sh", cwd=cwd), tmp_path, (), ())
        for cwd in (None, str(here), str(there))
    ]
    assert steps[0].summary.startswith("Run: ./cleanup.sh (")
    assert f"Run: ./cleanup.sh in {here} (" in steps[1].summary
    assert len({kept_digest(step) for step in steps}) == 3
