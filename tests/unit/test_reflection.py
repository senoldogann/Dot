"""The pure parts of the nightly reflection (item 70): when it is due, which runs it reads, what a run's stored results
tell it, which lesson notes say the same or rest, and what it writes."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from dotagent.agent.reflection import (
    Reflection,
    _Result,
    call_facts,
    digest_of,
    duplicate_pairs,
    ending_signal,
    journal_span,
    journal_text,
    read_outside,
    reflection_cron,
    reflection_due,
    summary_line,
    to_retire,
    to_retire_by_score,
    wants_reflection,
)
from dotagent.agent.verify import CallFact
from dotagent.config import AppConfig, load_default_config
from dotagent.memory.service import OwnerSection, owner_lines_within, owner_prompt_lines
from dotagent.store.models import MemoryRecord, MemoryScore, RunRecord, RunStatus

ZONE: ZoneInfo = ZoneInfo("Europe/Helsinki")
DAY_S: float = 86400.0


def _at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp()


def _run(status: RunStatus, mode: str, parent: str | None, source: str) -> RunRecord:
    return RunRecord(
        id="run_1",
        thread_id="thread_1",
        goal="Count the files",
        mode=mode,  # type: ignore[arg-type]
        model="scripted:any",
        status=status,
        created_at=1.0,
        finished_at=2.0,
        result="",
        steps=1,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=parent,
        source=source,
        effort=None,
        approvals="ask",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
    )


def _note(
    identifier: str, content: str, created_at: float, last_used_at: float | None, tags: tuple[str, ...]
) -> MemoryRecord:
    return MemoryRecord(
        id=identifier,
        content=content,
        tags=tags,
        created_at=created_at,
        updated_at=created_at,
        source_run="run_0",
        agent_id=None,
        used_count=0 if last_used_at is None else 1,
        last_used_at=last_used_at,
    )


def _result(tool: str, ok: bool, text: str, target: str, kind: str, channel: str) -> _Result:
    return _Result(tool=tool, ok=ok, text=text, kind=kind, target=target, channel=channel)


def test_the_daily_time_is_a_cron_expression() -> None:
    assert reflection_cron("03:30") == "30 3 * * *"
    assert reflection_cron("00:05") == "5 0 * * *"


def test_the_reflection_is_due_once_its_time_has_passed_since_the_anchor_and_never_when_off() -> None:
    anchor: float = _at("2026-10-10T12:00")
    assert not reflection_due("03:30", anchor, _at("2026-10-11T03:29"), ZONE)
    assert reflection_due("03:30", anchor, _at("2026-10-11T03:30"), ZONE)
    # A night the daemon slept through is due at its next tick, however late.
    assert reflection_due("03:30", anchor, _at("2026-10-13T15:00"), ZONE)
    # Once it ran (the anchor moved), the next one waits for the next night.
    assert not reflection_due("03:30", _at("2026-10-11T03:30"), _at("2026-10-11T15:00"), ZONE)
    assert not reflection_due("off", anchor, _at("2026-10-13T15:00"), ZONE)


def test_only_a_task_or_research_run_of_its_own_that_ended_is_read() -> None:
    assert wants_reflection(_run("done", "task", None, "ui"))
    assert wants_reflection(_run("failed", "research", None, "schedule"))
    assert wants_reflection(_run("cancelled", "task", None, "cli"))
    assert not wants_reflection(_run("done", "chat", None, "ui"))
    assert not wants_reflection(_run("done", "task", "run_parent", "ui"))
    assert not wants_reflection(_run("done", "task", None, "eval"))
    assert not wants_reflection(_run("running", "task", None, "ui"))


def test_the_ending_of_a_run_that_did_not_reach_its_goal_is_a_signal() -> None:
    assert ending_signal("done", "All counted.") is None
    assert ending_signal("failed", "The  file was\nnot found.") == "The run ended failed: The file was not found."
    assert ending_signal("cancelled", "") == "The run was stopped before it finished."
    assert ending_signal("failed", "token sk-abcdefghijklmnopqrstuvwxyz0123456789") == "The run ended failed: token ***"


def test_the_stored_results_give_the_failed_calls_as_the_loop_keeps_them() -> None:
    results: tuple[_Result, ...] = (
        _result("read_file", False, "No such file: notes.txt\nmore", "/w/notes.txt", "file_read", "agent"),
        _result("write_file", True, "Wrote 6 bytes", "/w/notes.txt", "file_write", "agent"),
        _result("read_file", True, "hello", "/w/notes.txt", "file_read", "agent"),
        _result("shell", False, "exit 1", "ls /nope", "command", "command"),
    )
    failures, resolved = call_facts(results)
    assert failures == (CallFact(tool="shell", target="ls /nope", ok=False, result="exit 1"),)
    assert resolved == (CallFact(tool="read_file", target="/w/notes.txt", ok=False, result="No such file: notes.txt"),)
    assert not read_outside(results)
    assert read_outside((*results, _result("web_fetch", True, "<html>", "https://x.test", "network", "web")))
    assert read_outside((_result("browser_read", True, "page", "", "", "browser"),))


def test_the_digest_names_the_goal_each_result_and_the_ending() -> None:
    digest: str = digest_of(
        "Count the files",
        (_result("shell", False, "ls: cannot access", "ls /nope", "command", "command"),),
        "failed",
        "Could not count.",
    )
    assert digest.splitlines()[0] == "Goal: Count the files"
    assert "shell ls /nope: FAILED - ls: cannot access" in digest
    assert digest.splitlines()[-1] == "The run ended failed: Could not count."


def test_two_lesson_notes_that_say_the_same_make_a_pair_older_first_each_note_once() -> None:
    a: MemoryRecord = _note("a", "Create notes.txt with write_file before read_file reads it.", 10.0, None, ("lesson",))
    b: MemoryRecord = _note(
        "b", "Create notes.txt with write_file before read_file reads it back.", 20.0, None, ("lesson",)
    )
    c: MemoryRecord = _note("c", "pdftotext keeps the columns of a table only with -layout.", 30.0, None, ("lesson",))
    d: MemoryRecord = _note(
        "d", "Create notes.txt with write_file before read_file reads it again.", 40.0, None, ("lesson",)
    )
    assert duplicate_pairs([c, d, b, a], {}) == [(a, b)]
    # With embeddings the cosine decides: close vectors pair, far ones with the same words do not.
    close: dict[str, tuple[float, ...]] = {"a": (1.0, 0.0), "b": (0.0, 1.0), "c": (0.6, 0.8), "d": (0.8, 0.6)}
    assert duplicate_pairs([a, b, c, d], close) == [(c, d)]


def test_a_lesson_rests_after_sixty_days_without_a_run_being_handed_it() -> None:
    now: float = 100 * DAY_S
    fresh: MemoryRecord = _note("fresh", "x" * 30, now - DAY_S, None, ("lesson",))
    used: MemoryRecord = _note("used", "y" * 30, now - 90 * DAY_S, now - 10 * DAY_S, ("lesson",))
    stale: MemoryRecord = _note("stale", "z" * 30, now - 90 * DAY_S, now - 61 * DAY_S, ("lesson",))
    never: MemoryRecord = _note("never", "w" * 30, now - 61 * DAY_S, None, ("lesson",))
    resting: MemoryRecord = _note("resting", "v" * 30, now - 90 * DAY_S, None, ("lesson", "rest"))
    assert to_retire([fresh, used, stale, never, resting], now, 60) == [stale, never]


def test_the_summary_and_the_journal_say_what_happened() -> None:
    reflection: Reflection = Reflection(
        at=2.0, since=1.0, runs_read=3, lessons_written=1, merged=1, retired=2, journal=None, owner_updated=False
    )
    assert summary_line(reflection) == "Reflected on 3 runs: 1 lesson, 1 merged, 2 retired."
    runs: list[RunRecord] = [
        _run("done", "task", None, "ui"),
        _run("failed", "task", None, "ui"),
        _run("failed", "task", None, "ui"),
    ]
    text: str = journal_text("On 2026-10-11", runs, ["Use -layout with pdftotext."], 1, 2, 600)
    assert text == (
        "On 2026-10-11 I worked on 3 runs (1 done, 2 failed). Learned: Use -layout with pdftotext. "
        "Tidied the lessons: 1 merged, 2 retired."
    )
    assert (
        journal_text("On 2026-10-11", [], [], 0, 1, 600)
        == "On 2026-10-11 I worked on 0 runs (no run ended). Tidied the lessons: 0 merged, 1 retired."
    )
    assert len(journal_text("On 2026-10-11", runs, ["x" * 700], 0, 0, 100)) == 100


def test_the_journal_names_the_days_it_tells() -> None:
    """Item 125: the reflection at 03:30 tells the day before it; a longer time than a day and a quarter, its days."""
    night: float = _at("2026-10-12T03:30:00")
    assert journal_span(night - DAY_S, night, ZONE) == "On 2026-10-11"
    # Late, or by hand in the afternoon, a reflection names the day it read.
    assert journal_span(_at("2026-10-11T03:30:00"), _at("2026-10-12T09:00:00"), ZONE) == "On 2026-10-11"
    assert journal_span(night, _at("2026-10-12T16:00:00"), ZONE) == "On 2026-10-12"
    # The daemon was off for days.
    assert journal_span(_at("2026-10-08T03:30:00"), night, ZONE) == "From 2026-10-08 to 2026-10-12"


def test_the_reflection_time_is_a_time_of_day_or_off() -> None:
    config: AppConfig = load_default_config()
    assert (config.agent.reflection_time, config.agent.lesson_rest_days) == ("03:30", 60)
    assert config.agent.model_copy(update={"reflection_time": "off"}).reflection_time == "off"
    for bad in ("3:30", "24:00", "night", ""):
        with pytest.raises(ValidationError, match="time of day"):
            config.agent.model_validate({**config.agent.model_dump(), "reflection_time": bad})


def test_a_lesson_whose_score_is_negative_over_enough_runs_rests_unless_the_owner_saved_it() -> None:
    now: float = 100 * DAY_S
    hurt: MemoryRecord = _note("hurt", "x" * 30, now - DAY_S, now, ("lesson",))
    helped: MemoryRecord = _note("helped", "y" * 30, now - DAY_S, now, ("lesson",))
    few: MemoryRecord = _note("few", "z" * 30, now - DAY_S, now, ("lesson",))
    kept: MemoryRecord = _note("kept", "w" * 30, now - DAY_S, now, ("lesson",))
    resting: MemoryRecord = _note("resting", "v" * 30, now - DAY_S, now, ("lesson", "rest"))
    scores: dict[str, MemoryScore] = {
        "hurt": MemoryScore(handed=3, done=1, failed=2),
        "helped": MemoryScore(handed=3, done=3, failed=0),
        "few": MemoryScore(handed=2, done=0, failed=2),
        "kept": MemoryScore(handed=5, done=0, failed=5),
        "resting": MemoryScore(handed=5, done=0, failed=5),
    }
    assert to_retire_by_score([hurt, helped, few, kept, resting], scores, 3, {"kept"}) == [(hurt, scores["hurt"])]


def test_the_owner_lines_are_flattened_capped_in_count_and_cut_at_the_budget() -> None:
    lines: list[str] = ["  Reads  Turkish. ", "", "Works late.", "x" * 40]
    assert owner_lines_within(lines, 1500) == ("Reads Turkish.", "Works late.", "x" * 40)
    assert owner_lines_within(lines, 30) == ("Reads Turkish.", "Works late.")
    assert len(owner_lines_within([f"line {n}" for n in range(20)], 1500)) == 12
    section: OwnerSection = OwnerSection(dot_lines=("Works late.",), owner_lines=("I read Turkish.",), updated_at=1.0)
    assert owner_prompt_lines(section) == ("(you said) I read Turkish.", "Works late.")
