"""The pure rules of a campaign: the brief a session opens with, when the next session starts, and how many sessions
in a row brought no progress."""

from dotagent.agent.campaign import (
    Brief,
    BriefQuestion,
    LastSession,
    next_session_at,
    render_brief,
    session_number,
    sessions_without_progress,
)
from dotagent.store.models import CampaignEntry, CampaignEntryKind, RunRecord

GOAL: str = "Collect 50 leads into leads.csv."
NOW: float = 1_000_000.0
HOUR: float = 3600.0


def _entry(kind: CampaignEntryKind, text: str, numbers: dict[str, float], ago_s: float) -> CampaignEntry:
    return CampaignEntry(
        id=f"entry_{text[:4]}",
        campaign_id="cmp_1",
        run_id="run_1",
        ts=NOW - ago_s,
        kind=kind,
        text=text,
        numbers=numbers,
    )


def _brief(
    session: int,
    targets: str,
    entries: tuple[CampaignEntry, ...],
    questions: tuple[BriefQuestion, ...],
    last: LastSession | None,
    without_progress: int,
) -> Brief:
    strategy: CampaignEntry | None = next((entry for entry in reversed(entries) if entry.kind == "strategy"), None)
    progress: CampaignEntry | None = next((entry for entry in reversed(entries) if entry.kind == "progress"), None)
    return Brief(
        goal=GOAL,
        targets=targets,
        verification="wc -l leads.csv" if targets else "",
        files=("/Users/me/Dot/attachments/att_1/fair.csv",) if session > 1 else (),
        session=session,
        now=NOW,
        instructions=("Only companies in Berlin.",) if session > 1 else (),
        strategy=strategy,
        progress=progress,
        entries=entries,
        questions=questions,
        last=last,
        without_progress=without_progress,
        patience=3,
    )


def test_the_first_brief_has_the_goal_and_asks_for_the_targets() -> None:
    assert render_brief(_brief(1, "", (), (), None, 0)) == (
        f"This is session 1 of a campaign. Its goal:\n{GOAL}\n\n"
        "Targets: not set yet. Record them with campaign_targets before you finish.\n\n"
        "Ledger: empty."
    )


def test_a_later_brief_carries_the_ledger_the_questions_and_how_the_last_session_ended() -> None:
    entries: tuple[CampaignEntry, ...] = (
        _entry(
            "progress",
            "Wrote 12 leads from the fair list",
            {"leads": 12.0, "share": 0.25, "eur": 1234567.89},
            26 * HOUR,
        ),
        _entry("strategy", "Search the chamber of commerce next", {}, 3 * HOUR),
    )
    questions: tuple[BriefQuestion, ...] = (
        BriefQuestion(summary="Which CRM?", asked_at=NOW - 5 * HOUR, status="approved", answer="HubSpot"),
        BriefQuestion(summary="May I pay for a list?", asked_at=NOW - 4 * HOUR, status="denied", answer=""),
        BriefQuestion(summary="Your sender address?", asked_at=NOW - 30 * 60, status="left", answer=""),
        BriefQuestion(summary="Go ahead?", asked_at=NOW - 20 * 60, status="expired", answer=""),
    )
    last: LastSession = LastSession(
        number=2, started_at=NOW - 2 * HOUR, finished_at=NOW - HOUR, status="done", result="12 of 50.", cut_off=False
    )
    brief: str = render_brief(_brief(3, "50 leads in leads.csv", entries, questions, last, 1))
    assert brief.startswith(f"This is session 3 of a campaign. Its goal:\n{GOAL}\n\n")
    assert "Targets: 50 leads in leads.csv\nVerified by: wc -l leads.csv\n\n" in brief
    assert "read them at these paths when you need them):\n- /Users/me/Dot/attachments/att_1/fair.csv\n\n" in brief
    assert "(instructions):\n- Only companies in Berlin.\n\n" in brief
    assert "Strategy (the newest): Search the chamber of commerce next\n\n" in brief
    # The newest progress entry is among the entries listed, so it is not shown twice.
    assert "Progress (the newest" not in brief
    assert (
        "Ledger (the newest entries, oldest first):\n"
        "- [progress, 26 h ago] Wrote 12 leads from the fair list (leads 12, share 0.25, eur 1234567.89)\n"
        "- [strategy, 3 h ago] Search the chamber of commerce next\n\n"
    ) in brief
    assert (
        "Questions left for the owner:\n"
        "- “Which CRM?”: answered: HubSpot\n"
        "- “May I pay for a list?”: skipped by the owner\n"
        "- “Your sender address?”: unanswered since 30 min\n"
        "- “Go ahead?”: not answered while the session waited for it\n\n"
    ) in brief
    assert brief.endswith("Session 2 ended 60 min ago (done).\nIts answer: 12 of 50.")
    # Fewer sessions without progress than the patience: no call for a new strategy.
    assert "No session wrote a progress entry" not in brief


def test_a_brief_after_a_cut_off_session_and_too_many_rounds_without_progress_asks_for_a_new_strategy() -> None:
    last: LastSession = LastSession(
        number=4, started_at=NOW - 3 * HOUR, finished_at=NOW - 3 * HOUR, status="failed", result="x", cut_off=True
    )
    brief: str = render_brief(_brief(5, "50 leads in leads.csv", (), (), last, 3))
    assert "Session 4 was cut off by a stop of the daemon 3 h ago, before it ended" in brief
    assert "Its answer" not in brief
    assert brief.endswith(
        "No session wrote a progress entry for 3 sessions. Before anything else, write a new strategy entry with "
        "campaign_note (kind strategy) that tries another way, and follow it."
    )


def test_a_brief_shows_the_newest_progress_also_when_newer_entries_push_it_out_of_the_ledger_it_lists() -> None:
    progress: CampaignEntry = _entry("progress", "Wrote 30 leads", {"leads": 30.0}, 50 * HOUR)
    notes: tuple[CampaignEntry, ...] = (_entry("note", "Checked the fair list again", {}, HOUR),)
    brief: str = render_brief(
        _brief(4, "50 leads in leads.csv", notes, (), None, 0).model_copy(update={"progress": progress})
    )
    assert (
        "Progress (the newest, before the entries below):\n- [progress, 2 days ago] Wrote 30 leads (leads 30)\n\n"
        "Ledger (the newest entries, oldest first):\n- [note, 60 min ago] Checked the fair list again"
    ) in brief


def test_the_next_session_follows_the_plan_else_waits_for_an_answer_else_comes_after_the_gap() -> None:
    planned: float = NOW + 5 * HOUR
    assert next_session_at(NOW, planned, "Your sender address.", ["left"], 60, 24) == planned
    # The session ended needing the owner, and no question it left is answered yet.
    assert next_session_at(NOW, None, "Your sender address.", ["left", "left"], 60, 24) == NOW + 24 * HOUR
    # One is answered, it needs nothing, or it left no question: the usual gap.
    assert next_session_at(NOW, None, "Your sender address.", ["left", "approved"], 60, 24) == NOW + HOUR
    assert next_session_at(NOW, None, "", ["left"], 60, 24) == NOW + HOUR
    assert next_session_at(NOW, None, "Your sender address.", [], 60, 24) == NOW + HOUR


def test_the_sessions_without_progress_are_the_ones_after_the_newest_progress_entry() -> None:
    started: list[float] = [NOW - 3 * HOUR, NOW - 2 * HOUR, NOW - HOUR]
    assert sessions_without_progress(started, None) == 3
    assert sessions_without_progress(started, NOW - 2.5 * HOUR) == 2
    assert sessions_without_progress(started, NOW) == 0


def test_a_session_number_counts_the_sessions_of_the_campaign_from_one() -> None:
    sessions: list[RunRecord] = [
        RunRecord(
            id=run_id,
            thread_id=f"thread_{run_id}",
            goal=GOAL,
            mode="task",
            model="scripted:test",
            status="done",
            created_at=NOW,
            finished_at=NOW,
            result="",
            steps=1,
            input_tokens=0,
            output_tokens=0,
            parent_run_id=None,
            source="schedule",
            effort=None,
            approvals="yolo",
            goal_mode=False,
            campaign_id="cmp_1",
        )
        for run_id in ("run_a", "run_b", "run_c")
    ]
    assert [session_number(sessions, run_id) for run_id in ("run_a", "run_b", "run_c")] == [1, 2, 3]
