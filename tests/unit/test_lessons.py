"""Lessons: which runs are asked for them, what went wrong along a run as the review model reads it, which proposed
lessons are kept, and what became of the lessons of a run as its events tell."""

import itertools
from collections.abc import Iterator

import pytest

from dotagent.agent.lessons import (
    LessonCase,
    LessonView,
    RunLessons,
    _says_it,
    contains_secret,
    kept_lessons,
    lesson_problem,
    lessons_of,
    may_learn,
    outside_problem,
    repeats,
    run_signals,
    vetted,
)
from dotagent.agent.review import LessonVerdict, LessonVetting
from dotagent.agent.verify import CallFact
from dotagent.config import LessonsMode
from dotagent.core.types import JsonObject
from dotagent.memory.service import MemoryHit
from dotagent.store.models import EventRecord, MemoryRecord, RunMode, RunStatus

MAX_CHARS: int = 300
GOAL: str = "Summarise the quarterly report in report.pdf"
LESSON: str = "Read PDF files with pdftotext -layout; read_file returns their raw bytes."
CSV_LESSON: str = "Large CSV files load faster with the csv module than with pandas here."


# Events are stored in order: each made here comes after the ones before it.
_IDS: Iterator[int] = itertools.count(1)


def _event(kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=next(_IDS), run_id="run-1", ts=0.0, kind=kind, data=data)


def _view(index: int, text: str, state: str, memory_id: str | None, by: str | None, reason: str | None) -> LessonView:
    return LessonView.model_validate(
        {"index": index, "text": text, "state": state, "memory_id": memory_id, "by": by, "reason": reason}
    )


def _note(content: str) -> MemoryRecord:
    return MemoryRecord(
        id="note-1",
        content=content,
        tags=("lesson",),
        created_at=1.0,
        updated_at=1.0,
        source_run="run-0",
        agent_id=None,
        used_count=0,
        last_used_at=None,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


def _case(failures: tuple[CallFact, ...], resolved: tuple[CallFact, ...]) -> LessonCase:
    return LessonCase(
        run_id="run-1",
        thread_id="thread-1",
        model="ollama:scripted",
        goal=GOAL,
        digest="",
        failures=failures,
        resolved=resolved,
        answer="Done.",
        tainted=False,
        reached_goal=True,
    )


@pytest.mark.parametrize(
    ("setting", "mode", "parent", "status", "expected"),
    [
        ("propose", "task", None, "done", True),
        ("auto", "research", None, "done", True),
        ("off", "task", None, "done", False),
        ("propose", "chat", None, "done", False),
        ("propose", "task", "run-0", "done", False),
        ("propose", "task", None, "failed", False),
        ("auto", "research", None, "cancelled", False),
    ],
)
def test_only_a_task_or_research_run_of_its_own_that_ended_done_may_learn(
    setting: LessonsMode, mode: RunMode, parent: str | None, status: RunStatus, expected: bool
) -> None:
    assert may_learn(setting, mode, parent, status) is expected


def test_a_run_along_which_nothing_went_wrong_has_no_signals() -> None:
    assert run_signals(_case((), ()), [], [], []) == ()


def test_a_failed_call_says_whether_a_later_call_of_the_same_tool_and_target_worked() -> None:
    resolved: CallFact = CallFact(tool="read_file", target="/tmp/w/report.pdf", ok=False, result="binary file")
    failed: CallFact = CallFact(tool="shell", target="make test", ok=False, result="[exit 2 in 0.1s]")
    assert run_signals(_case((failed,), (resolved,)), [], [], []) == (
        "read_file /tmp/w/report.pdf failed (binary file); a later call of the same tool and target worked.",
        "shell make test failed ([exit 2 in 0.1s]); no later call of the same tool and target worked.",
    )


def test_an_alert_of_the_progress_watchdog_is_a_signal() -> None:
    alert: EventRecord = _event(
        "stall",
        {
            "level": 2,
            "action": "replan",
            "reason": "failures",
            "detail": "The last 4 steps all failed: every call in each of them returned an error.",
        },
    )
    bare: EventRecord = _event("stall", {"warnings": 1})
    assert run_signals(_case((), ()), [alert, bare], [], []) == (
        "The progress watchdog raised an alert: The last 4 steps all failed: every call in each of them returned an "
        "error. Its response: replan.",
        "The progress watchdog raised an alert: the run was not getting anywhere.",
    )


def test_only_a_goal_check_that_sent_the_run_back_is_a_signal() -> None:
    checks: list[EventRecord] = [
        _event("goal_check", {"decision": "continue", "reason": "The summary misses the totals.", "final": False}),
        _event("goal_check", {"decision": "continue", "reason": "Still no totals.", "final": True}),
        _event("goal_check", {"decision": "achieved", "reason": "The totals are there.", "final": True}),
    ]
    assert run_signals(_case((), ()), [], checks, []) == (
        "The goal check sent the run back: The summary misses the totals.",
    )


def test_paths_the_answer_named_that_did_not_exist_are_a_signal() -> None:
    paths: EventRecord = _event("answer_paths", {"missing": ["/tmp/w/summary.md", "notes.txt"]})
    assert run_signals(_case((), ()), [], [], [paths]) == (
        "The final answer named paths that did not exist: /tmp/w/summary.md, notes.txt.",
    )


def test_a_signal_is_one_short_line_without_secrets() -> None:
    leaked: CallFact = CallFact(
        tool="web_fetch",
        target="https://api.example.com/v1",
        ok=False,
        result="401\nUnauthorized for key sk-abcdefghijklmnopqrstuvwx",
    )
    long: CallFact = CallFact(tool="shell", target="x" * 400, ok=False, result="[exit 1]")
    signals: tuple[str, ...] = run_signals(_case((leaked, long), ()), [], [], [])
    assert signals[0] == (
        "web_fetch https://api.example.com/v1 failed (401 Unauthorized for key ***); no later call of the same tool "
        "and target worked."
    )
    assert len(signals[1]) == 300
    assert signals[1].endswith("…")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Read PDF files with pdftotext -layout.\nThen summarise them.", "more than one line"),
        ("Use pdftotext.", "too short"),
        ("x" * 301, "too long"),
        ("Call the API with the key sk-abcdefghijklmnopqrstuvwx in the header.", "a secret"),
        ("Send the weekly summary to someone@example.com every Monday.", "an e-mail address"),
        ("The reports live under /Users/alice/Documents/reports.", "a path in a home folder"),
        ("The reports live under /home/bob/reports on the server.", "a path in a home folder"),
        ("The reports live under /users/alice/reports, as macOS also finds them.", "a path in a home folder"),
        ("Shared reports live under /Users/Shared/reports for every account.", None),
        ("The daemon's log is ~/Library/Logs/Dot/dotx.log, not the workspace.", None),
        (LESSON, None),
    ],
)
def test_a_lesson_is_one_line_without_secrets_addresses_or_home_paths(text: str, problem: str | None) -> None:
    assert lesson_problem(text, MAX_CHARS, ()) == problem


def test_a_text_repeats_another_with_the_same_words_or_mostly_the_same_search_terms() -> None:
    assert repeats("Read PDF files with pdftotext -layout.", "read pdf files WITH  pdftotext -layout.")
    assert repeats("Dosyaları önce yedekle, sonra düzenle.", "Dosyalari once yedekle, sonra duzenle.")
    assert repeats(
        "Read PDF files with pdftotext -layout before summarising them.",
        "Before summarising PDF files, read them with pdftotext -layout.",
    )
    assert not repeats(LESSON, "Run the tests with pytest -q before committing.")


def test_a_lesson_never_repeats_the_one_that_says_the_opposite() -> None:
    """Item 154: "do", "don" and "not" are no search terms, so a correction has the same terms as what it corrects."""
    assert not repeats("Use --force when git push is refused.", "Do not use --force when git push is refused.")
    assert not repeats("A login is required for the upload.", "A login isn't required for the upload.")
    assert not repeats("Bu sitede giriş gerekli.", "Bu sitede giriş gerekli değil.")
    # Said the same way round, they still repeat each other.
    assert repeats("Do not use --force on main.", "Don't use --force on main.")
    # Close in meaning is no repeat either when one of them says not to.
    hit: MemoryHit = MemoryHit(record=_note("Use --force when git push is refused."), similarity=0.99)
    assert not _says_it("Do not use --force when git push is refused.", hit, 0.9)
    assert _says_it("Use --force whenever git push is refused.", hit, 0.9)


def test_kept_lessons_drop_what_is_not_a_lesson_and_every_repeat() -> None:
    proposed: list[str] = [
        f"  {LESSON}  ",
        "read pdf files with PDFTOTEXT -layout; read_file returns their raw bytes.",
        "Too short.",
        GOAL,
        "Run the tests with pytest -q before every commit.",
        "The workspace keeps its files under /Users/alice/dot.",
        CSV_LESSON,
    ]
    known: tuple[str, ...] = (GOAL, "Before every commit, run the tests with pytest -q.")
    assert kept_lessons(proposed, known, MAX_CHARS, ()) == (LESSON, CSV_LESSON)


# A secret Dot holds, as :func:`dotagent.secrets.known_secrets` gives it.
HELD: str = "MARKER-7f3c9a1e2b"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Log in with password: hunter2 on the staging site.", True),
        (f"The staging site takes the key {HELD} in its header.", True),
        ("Call the API with the key sk-abcdefghijklmnopqrstuvwx in the header.", True),
        ("export API_KEY='abcdef1234567890xyz' before running the importer.", True),
        ("Plan the week on the task-management-2024 board before Monday.", False),
        ("The shop's risk-free-returns-for-thirty-days page lists the window.", False),
        ("CSS mask-image-set-for-the-header-logo needs a PNG fallback.", False),
    ],
)
def test_a_secret_is_found_by_its_key_its_value_or_its_form_and_no_word_with_sk_in_it_is_one(
    text: str, found: bool
) -> None:
    assert contains_secret(text, (HELD,)) is found


def test_a_lesson_on_the_task_management_board_is_kept_and_one_with_a_value_dot_holds_is_not() -> None:
    board: str = "Plan the week on the task-management-2024 board before Monday."
    held: str = f"The staging site takes the key {HELD} in its header."
    assert kept_lessons([board, held], (), MAX_CHARS, (HELD,)) == (board,)
    assert lesson_problem(held, MAX_CHARS, (HELD,)) == "a secret"
    # Only its value tells this one: no key names a secret in it and it has no known form.
    assert lesson_problem(held, MAX_CHARS, ()) is None


def test_no_lessons_were_drawn_from_a_run_without_lesson_events() -> None:
    assert lessons_of(None, [], [], None) is None


def test_a_run_whose_lessons_could_not_be_drawn_shows_why() -> None:
    failed: EventRecord = _event("lessons_failed", {"error": "The lessons of this run could not be drawn: timed out"})
    assert lessons_of(None, [], [], failed) == RunLessons(
        lessons=[], tainted=False, error="The lessons of this run could not be drawn: timed out"
    )


def test_each_proposed_lesson_shows_its_last_decision() -> None:
    proposed: EventRecord = _event("lessons_proposed", {"lessons": [LESSON, CSV_LESSON, GOAL], "tainted": True})
    saved: list[EventRecord] = [_event("lesson_saved", {"index": 0, "memory_id": "mem-1", "by": "owner"})]
    dismissed: list[EventRecord] = [_event("lesson_dismissed", {"index": 1})]
    failed: EventRecord = _event("lessons_failed", {"error": "A lesson could not be saved: the embedder is down"})
    assert lessons_of(proposed, saved, dismissed, failed) == RunLessons(
        lessons=[
            _view(0, LESSON, "saved", "mem-1", "owner", None),
            _view(1, CSV_LESSON, "dismissed", None, "owner", None),
            _view(2, GOAL, "proposed", None, None, None),
        ],
        tainted=True,
        error="A lesson could not be saved: the embedder is down",
    )
    # Undo after a save is a dismissal after it, and Save anyway after auto left a lesson out is a save after it.
    undone: EventRecord = _event("lesson_dismissed", {"index": 0, "by": "owner", "reason": None})
    left_out: EventRecord = _event("lesson_dismissed", {"index": 2, "by": "auto", "reason": "a link"})
    kept: EventRecord = _event("lesson_saved", {"index": 2, "memory_id": "mem-3", "by": "owner", "created": True})
    assert [
        (lesson.state, lesson.by, lesson.reason)
        for lesson in lessons_of(proposed, [*saved, kept], [*dismissed, undone, left_out], None).lessons
    ] == [("dismissed", "owner", None), ("dismissed", "owner", None), ("saved", "owner", None)]
    assert [
        (lesson.state, lesson.by, lesson.reason)
        for lesson in lessons_of(proposed, saved, [*dismissed, left_out], None).lessons
    ] == [("saved", "owner", None), ("dismissed", "owner", None), ("dismissed", "auto", "a link")]


def test_the_error_of_a_lesson_auto_could_not_save_goes_once_every_lesson_is_decided() -> None:
    proposed: EventRecord = _event("lessons_proposed", {"lessons": [LESSON, CSV_LESSON], "tainted": False})
    saved: list[EventRecord] = [
        _event("lesson_saved", {"index": 0, "memory_id": "mem-1", "by": "auto"}),
        _event("lesson_saved", {"index": 1, "memory_id": "mem-2", "by": "owner"}),
    ]
    failed: EventRecord = _event("lessons_failed", {"error": "A lesson could not be saved: the embedder is down"})
    assert lessons_of(proposed, saved, [], failed) == RunLessons(
        lessons=[
            _view(0, LESSON, "saved", "mem-1", "auto", None),
            _view(1, CSV_LESSON, "saved", "mem-2", "owner", None),
        ],
        tainted=False,
        error=None,
    )


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Open https://example.com/start before the sign-in page loads.", "a link"),
        ("Load www.example.com first to get the cookie banner out of the way.", "a link"),
        ("Follow @dot_helper on X before replying, or replies are hidden.", "an account name"),
        ("Send the weekly summary to someone@example.com every Monday.", None),
        ("On x.com, browser_wait_text never sees the view counts; read them with browser_links.", None),
        (LESSON, None),
    ],
)
def test_a_link_or_an_account_name_keeps_a_lesson_of_an_outside_run_from_saving_itself(
    text: str, problem: str | None
) -> None:
    found: str | None = outside_problem(text)
    assert (found if found is None else found.split(",")[0]) == problem


def test_the_second_look_keeps_only_what_it_was_asked_about_and_kept() -> None:
    lessons: tuple[str, ...] = (LESSON, "Open https://example.com/start first.", CSV_LESSON, GOAL)
    checked: tuple[str | None, ...] = (None, "a link", None, None)
    vetting: LessonVetting = LessonVetting(
        verdicts=[
            LessonVerdict(lesson=1, keep=True, reason="know-how about a tool"),
            LessonVerdict(lesson=2, keep=False, reason="tells the agent where to go"),
        ]
    )
    # The third lesson asked about got no verdict: it is left out too.
    assert vetted(lessons, checked, vetting) == (
        None,
        "a link",
        "tells the agent where to go",
        "no verdict was given on it",
    )
    # Item 350: a verdict goes to the lesson it names, not to the next one in turn; before, one left out gave the
    # verdict of the third lesson asked about to the second.
    skipped: LessonVetting = LessonVetting(
        verdicts=[
            LessonVerdict(lesson=1, keep=True, reason="know-how about a tool"),
            LessonVerdict(lesson=3, keep=True, reason="know-how about a file format"),
        ]
    )
    assert vetted(lessons, checked, skipped) == (None, "a link", "no verdict was given on it", None)
