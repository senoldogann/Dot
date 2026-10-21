"""The pure part of "while you were away" (item 466): the moment kept within the week, the answer's first line with its
secrets masked before it is cut, what names a run, the counts of schedules' runs apart from campaigns' sessions, and the
runs with the most decisions made for the owner."""

from dotagent.server.away import AWAY_WINDOW_S, answer_line, away_view, clamp_since, decision_runs, title_of
from dotagent.server.views import AwayDecisionRun, AwayDecisions
from dotagent.store.models import AutoDecisionCount, RunRecord, UnattendedCount, UnattendedRun

_NOW: float = 1_760_000_000.0
_TOKEN: str = "ghp_" + "a1B2c3D4e5" * 4


def _run(run_id: str, goal: str, result: str, campaign_id: str | None, created_at: float) -> RunRecord:
    return RunRecord(
        id=run_id,
        thread_id=f"thr-{run_id}",
        goal=goal,
        mode="task",
        model="anthropic:claude-opus-5-5",
        status="done",
        created_at=created_at,
        finished_at=created_at + 60.0,
        result=result,
        steps=4,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=None,
        source="schedule",
        effort=None,
        speed="standard",
        once=None,
        approvals="yolo",
        goal_mode=False,
        campaign_id=campaign_id,
        agent_id=None,
        rewindable=False,
    )


def _unattended(run: RunRecord, schedule_name: str | None, campaign_title: str | None) -> UnattendedRun:
    return UnattendedRun(run=run, schedule_name=schedule_name, campaign_title=campaign_title)


def test_the_moment_is_kept_within_the_week_and_never_after_now() -> None:
    assert clamp_since(0.0, _NOW) == _NOW - AWAY_WINDOW_S
    assert clamp_since(_NOW + 60.0, _NOW) == _NOW
    assert clamp_since(_NOW - 3600.0, _NOW) == _NOW - 3600.0


def test_the_answer_is_its_first_line_with_its_secrets_masked_before_it_is_cut() -> None:
    assert answer_line("\n\nRead the **news**.\nThen more.") == "Read the news."
    masked: str = answer_line(f"Pushed with {_TOKEN} to the repository.")
    assert _TOKEN not in masked and "Pushed with" in masked
    # A secret where the cut falls is masked whole before the line is cut, so no part of it is left.
    assert "ghp_a1B2" not in answer_line("x" * 190 + " " + _TOKEN)
    assert len(answer_line("y" * 500)) == 200


def test_a_run_is_named_by_its_campaign_else_its_schedule_else_its_goal() -> None:
    run: RunRecord = _run("r1", "Check the build\nand tell me", "", None, _NOW)
    assert title_of(_unattended(run, "nightly build", "Ship 2.0")) == "Ship 2.0"
    assert title_of(_unattended(run, "nightly build", None)) == "nightly build"
    assert title_of(_unattended(run, None, None)) == "Check the build and tell me"


def test_the_counts_keep_the_campaigns_sessions_apart_and_the_decisions_are_summed() -> None:
    counts: list[UnattendedCount] = [
        UnattendedCount(status="done", campaign=False, count=5),
        UnattendedCount(status="failed", campaign=False, count=1),
        UnattendedCount(status="running", campaign=False, count=1),
        UnattendedCount(status="done", campaign=True, count=2),
    ]
    busy: UnattendedRun = _unattended(_run("busy", "Mail", "", None, _NOW - 600), "mail", None)
    calm: UnattendedRun = _unattended(_run("calm", "News", "", None, _NOW - 300), "news", None)
    decisions: list[AutoDecisionCount] = [
        AutoDecisionCount(run=busy, decided_by="yolo", status="approved", count=8),
        AutoDecisionCount(run=busy, decided_by="yolo", status="denied", count=1),
        AutoDecisionCount(run=calm, decided_by="reviewer", status="approved", count=2),
    ]
    view = away_view(_NOW - 3600, _NOW, [busy, calm], counts, decisions, 3)
    assert (view.done, view.failed, view.cancelled, view.working, view.campaign_sessions, view.drafts) == (
        5,
        1,
        0,
        1,
        2,
        3,
    )
    assert view.decisions == AwayDecisions(reviewer_approved=2, reviewer_denied=0, yolo_approved=8, yolo_denied=1)
    assert decision_runs(decisions) == [
        AwayDecisionRun(run_id="busy", title="mail", reviewer=0, yolo=9, denied=1),
        AwayDecisionRun(run_id="calm", title="news", reviewer=2, yolo=0, denied=0),
    ]
