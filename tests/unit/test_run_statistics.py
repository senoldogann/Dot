"""The statistics of the runs that happened: counted from a real database file that holds real runs and events."""

import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.cli_stats import render_learning, render_run_costs, render_stats
from dotagent.core.errors import StoreError
from dotagent.core.types import JsonObject
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.types import INVALID_ARGUMENTS_KEY
from dotagent.store.db import Store
from dotagent.store.endings import RESULT_STALLED, RESULT_STOPPED
from dotagent.store.models import MemoryScore, RunRecord, RunStatus
from dotagent.store.stats import (
    ActStats,
    LearningStats,
    ModelStats,
    NoteScore,
    PlaybookComparison,
    RunCosts,
    RunStatistics,
    TimeStats,
    ToolStats,
    WebFailures,
    collect_run_costs,
    collect_statistics,
    failure_cause,
    nearest_rank,
)

DAY_S: float = 86400.0
NO_LEARNING: LearningStats = LearningStats(
    lessons_proposed=0,
    lessons_saved=0,
    lessons_dismissed=0,
    runs_handed_notes=0,
    notes=(),
    playbooks_written=0,
    playbooks_retired=0,
    runs_reading_playbooks=0,
    comparisons=(),
)


async def _run(
    store: Store, thread_id: str, model: str, status: RunStatus, result: str, steps: int, tokens: int
) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread_id, "goal", "task", model, None, "test", "medium", "ask", False, None, None, None
    )
    await store.set_run_steps(run.id, steps)
    await store.add_run_tokens(run.id, tokens, tokens // 10)
    await store.finish_run(run.id, status, result)
    return run


async def _events(store: Store, run_id: str, events: list[tuple[str, JsonObject]]) -> None:
    for kind, data in events:
        await store.add_event(run_id, kind, data)


def _call(args: JsonObject) -> tuple[str, JsonObject]:
    return "tool_call", {"call_id": "c", "tool": "shell", "args": args}


def _result(ok: bool) -> tuple[str, JsonObject]:
    """A shell result as it was recorded before the wait at the gate was measured."""
    return "tool_result", {"call_id": "c", "tool": "shell", "ok": ok, "text": "", "images": [], "seconds": 0.0}


def _timed_result(tool: str, ok: bool, seconds: float, waited_s: float) -> tuple[str, JsonObject]:
    return "tool_result", {
        "call_id": "c",
        "tool": tool,
        "ok": ok,
        "text": "",
        "images": [],
        "seconds": seconds,
        "waited_s": waited_s,
    }


def _failed_result(tool: str, text: str, seconds: float) -> tuple[str, JsonObject]:
    """A failed result with the text that says why."""
    return "tool_result", {
        "call_id": "c",
        "tool": tool,
        "ok": False,
        "text": text,
        "images": [],
        "seconds": seconds,
        "waited_s": 0.0,
    }


def _act_result(tool: str, site: str, steps: int, ok: bool, seconds: float) -> tuple[str, JsonObject]:
    """An act result as it is recorded since the steps of a batch and the site were counted."""
    return "tool_result", {
        "call_id": "c",
        "tool": tool,
        "ok": ok,
        "text": "",
        "images": [],
        "seconds": seconds,
        "waited_s": 0.0,
        "site": site,
        "steps": steps,
    }


def _model_call(purpose: str, seconds: float, cached_tokens: int) -> tuple[str, JsonObject]:
    return CALL_EVENT, {
        "purpose": purpose,
        "model": "ollama:a",
        "seconds": seconds,
        "input_tokens": 100,
        "output_tokens": 1,
        "cached_tokens": cached_tokens,
        "error": None,
    }


def _cut_step(seconds: float) -> tuple[str, JsonObject]:
    """A step whose call a stop cut off: no reply, no tokens."""
    return CALL_EVENT, {
        "purpose": "step",
        "model": "ollama:a",
        "seconds": seconds,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "error": "cancelled",
    }


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Path]:
    path: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(path, tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    finished: RunRecord = await _run(store, thread.id, "ollama:a", "done", "answer", 3, 3000)
    await _events(
        store,
        finished.id,
        [
            _call({"command": "ls"}),
            _call({INVALID_ARGUMENTS_KEY: "{"}),
            _call({"command": "pwd"}),
            _result(True),
            _result(False),
            _result(True),
            ("stall", {"warnings": 1}),
            ("compacted", {"compaction": 1}),
            # Recorded before a step's seconds were: it is left out of the slowdown curve.
            ("assistant", {"step": 1}),
            ("assistant", {"step": 2, "seconds": 1.5}),
            ("assistant", {"step": 7, "seconds": 4.0}),
            # The newest step recorded the input tokens it sent as well; the older ones did not.
            ("assistant", {"step": 12, "seconds": 9.0, "input_tokens": 12000}),
            _model_call("step", 1.0, 100),
            _model_call("step", 3.0, 0),
            _model_call("step", 2.0, 0),
            _model_call("step", 10.0, 50),
            _cut_step(300.0),
            _model_call("compaction", 4.0, 0),
            _model_call("goal_check", 0.5, 0),
            *[_call({"step": number}) for number in range(4)],
            _timed_result("browser_act", True, 2.0, 0.0),
            _timed_result("browser_act", False, 6.0, 1.5),
            _timed_result("browser_act", True, 1.0, 0.0),
            _timed_result("read_file", True, 0.25, 0.0),
            # The three acts above were recorded before their steps were counted; these three were not.
            _act_result("browser_act", "x.com", 1, True, 2.0),
            _act_result("browser_act", "x.com", 4, True, 3.0),
            _act_result("desktop_act", "Safari", 1, False, 1.0),
            # The web failures by cause, the calls refused for their arguments, and a delegated run.
            *[_call({"url": f"https://x.test/{number}"}) for number in range(8)],
            _failed_result("web_fetch", "web_fetch got HTTP 404 from https://x.test/careers/. <web_content>", 0.1),
            _failed_result("web_fetch", "the host name could not be resolved | context={}", 0.1),
            _failed_result("web_search", "DuckDuckGo is limiting the searches of this machine (its bot check).", 0.1),
            _failed_result("web_fetch", "web_fetch got HTTP 403 from https://x.test/search. <web_content>", 0.1),
            _failed_result("web_fetch", "the request failed in transit | context={}", 0.1),
            _failed_result("web_search", "Invalid arguments for web_search: max_results: Input should be <= 10.", 0.1),
            _failed_result("shell", "Invalid arguments for shell: command: Field required.", 0.1),
            _timed_result("delegate", True, 30.0, 0.0),
        ],
    )
    # Made before the calls were timed: it recorded no model call.
    await _run(store, thread.id, "ollama:a", "cancelled", RESULT_STOPPED, 1, 1000)
    stalled: RunRecord = await _run(store, thread.id, "ollama:b", "failed", RESULT_STALLED, 4, 4000)
    await _events(
        store,
        stalled.id,
        [
            _call({"command": "false"}),
            _result(False),
            _model_call("step", 7.0, 0),
            _call({"command": "sleep 1"}),
            _timed_result("shell", False, 9.0, 8.0),
        ],
    )
    old: RunRecord = await _run(store, thread.id, "ollama:a", "done", "long ago", 50, 50000)
    await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (time.time() - 90 * DAY_S, old.id))
    yield path


def _by_model(stats: RunStatistics) -> dict[str, ModelStats]:
    return {entry.model: entry for entry in stats.models}


def test_the_runs_and_their_events_are_counted_per_model(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    assert [entry.model for entry in stats.models] == ["ollama:a", "ollama:b"]
    first: ModelStats = _by_model(stats)["ollama:a"]
    assert first.runs == 2
    assert [(count.ending, count.runs) for count in first.endings] == [("finished", 1), ("stopped", 1)]
    assert (first.steps, first.input_tokens, first.output_tokens) == (4, 4000, 400)
    assert (first.tool_calls, first.tool_errors, first.invalid_arguments) == (15, 10, 1)
    # Two calls were refused for arguments that did not fit, whatever the tool.
    assert first.rejected_arguments == 2
    assert (first.stalls, first.compaction.compactions) == (1, 1)
    # One compaction over the four steps of the model's runs, 4 of the 320.5 model seconds.
    assert first.compaction.per_100_steps == 25.0
    assert first.compaction.share == pytest.approx(4.0 / 320.5)
    assert first.web == WebFailures(not_found=2, throttled=1, blocked=1, transit=1, other=0)
    assert (first.delegation.runs, first.delegation.seconds) == (1, 30.0)
    second: ModelStats = _by_model(stats)["ollama:b"]
    assert [(count.ending, count.runs) for count in second.endings] == [("stalled", 1)]
    assert (second.tool_calls, second.tool_errors, second.invalid_arguments) == (2, 2, 0)
    assert second.web == WebFailures(not_found=0, throttled=0, blocked=0, transit=0, other=0)
    assert second.compaction.per_100_steps == 0.0


def test_a_longer_window_takes_in_the_older_runs(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 365 * DAY_S)
    assert _by_model(stats)["ollama:a"].runs == 3


def test_the_time_of_a_model_comes_from_the_calls_its_timed_runs_recorded(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    first: TimeStats = _by_model(stats)["ollama:a"].time
    # Of its two runs, the one stopped by hand recorded no model call and is left out.
    assert first.timed_runs == 1
    # The step a stop cut off after 300 s counts in the seconds, not in how fast a step is answered.
    assert first.model_seconds == pytest.approx(320.5)
    assert (first.step_seconds_median, first.step_seconds_p90) == (2.5, 10.0)
    assert first.compaction_seconds == pytest.approx(4.0)
    assert first.side_call_seconds == pytest.approx(4.5)
    assert first.cached_tokens == 150
    # 150 cached of the 600 input tokens of the six calls that answered (the cut step served none).
    assert first.cached_share == pytest.approx(0.25)
    second: TimeStats = _by_model(stats)["ollama:b"].time
    assert (second.timed_runs, second.step_seconds_median, second.step_seconds_p90) == (1, 7.0, 7.0)


def test_the_acts_the_slowdown_curve_and_the_sites_come_from_the_counted_events(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    first: ModelStats = _by_model(stats)["ollama:a"]
    # The three act results recorded without their steps are left out; of the three counted, two carried one step.
    assert first.acts == ActStats(acts=3, single=2, failed=1, seconds_median=2.0)
    assert [(band.first, band.last, band.steps, band.seconds_median, band.tokens_median) for band in first.bands] == [
        (1, 5, 1, 1.5, None),
        (6, 10, 1, 4.0, None),
        (11, 20, 1, 9.0, 12000),
    ]
    assert _by_model(stats)["ollama:b"].acts == ActStats(acts=0, single=0, failed=0, seconds_median=None)
    assert _by_model(stats)["ollama:b"].bands == ()
    assert [(entry.site, entry.acts.acts, entry.acts.single, entry.acts.failed) for entry in stats.sites] == [
        ("x.com", 2, 1, 0),
        ("Safari", 1, 1, 1),
    ]


def test_the_tools_are_listed_slowest_first_with_their_wait_at_the_gate(database: Path) -> None:
    tools: tuple[ToolStats, ...] = collect_statistics(database, time.time() - 30 * DAY_S).tools
    assert [(entry.tool, entry.calls, entry.failed) for entry in tools] == [
        ("delegate", 1, 0),
        ("browser_act", 5, 1),
        ("shell", 6, 4),
        ("desktop_act", 1, 1),
        ("web_fetch", 4, 4),
        ("read_file", 1, 0),
        ("web_search", 2, 2),
    ]
    browser: ToolStats = tools[1]
    assert (browser.seconds_total, browser.seconds_median, browser.waited_s) == (14.0, 2.0, 1.5)
    assert tools[2].waited_s == 8.0


def test_the_cause_of_a_failure_is_read_from_its_first_line() -> None:
    assert failure_cause("web_fetch", "web_fetch got HTTP 404 from https://x.test/a.\nmore") == "not_found"
    assert failure_cause("web_fetch", "the host name could not be resolved | context={}") == "not_found"
    assert failure_cause("web_fetch", "web_fetch got HTTP 429 from https://x.test/a.") == "throttled"
    assert (
        failure_cause("web_search", "DuckDuckGo answered with a page that has no results and no empty-result notice")
        == "throttled"
    )
    assert failure_cause("web_fetch", "web_fetch got HTTP 401 from https://x.test/a.") == "blocked"
    assert failure_cause("web_crawl", "the request failed in transit | context={}") == "transit"
    assert failure_cause("web_fetch", "web_fetch got HTTP 500 from https://x.test/a.") == "other"
    # The cause of a refused call is its arguments, whatever the tool; a shell failure is no web failure.
    assert failure_cause("shell", "Invalid arguments for shell: command: Field required.") == "rejected_arguments"
    assert failure_cause("shell", "web_fetch got HTTP 404 (quoted by a command)") == "other"


def test_the_costs_of_one_run_are_read_the_same_way(database: Path) -> None:
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        run_id, created_at = connection.execute(
            "SELECT id, created_at FROM runs WHERE model = 'ollama:a' AND steps = 3"
        ).fetchone()
    costs: RunCosts = collect_run_costs(database, run_id, created_at + 600)
    assert (costs.run_id, costs.status, costs.stats.model, costs.stats.runs, costs.stats.steps) == (
        run_id,
        "done",
        "ollama:a",
        1,
        3,
    )
    # A finished run's minutes run to its end, which the fixture set right after its creation.
    assert costs.minutes < 1.0
    assert costs.stats.compaction.compactions == 1 and costs.stats.web.not_found == 2
    assert costs.tool_seconds == pytest.approx(45.95) and costs.waited_s == pytest.approx(1.5)
    assert costs.tools[0].tool == "delegate"
    with pytest.raises(StoreError):
        collect_run_costs(database, "run_none", created_at)
    lines: list[str] = render_run_costs(costs)
    assert lines[0].startswith(f"Run {run_id} (done) on ollama:a: ") and "3 steps" in lines[0]
    assert f"  tool calls took {45.95:.1f} s, of which 1.5 s at the gate" in lines


def test_the_nearest_rank_is_a_value_that_occurred() -> None:
    assert nearest_rank([], 0.9) is None
    assert nearest_rank([4.0], 0.9) == 4.0
    assert nearest_rank([float(value) for value in range(1, 11)], 0.9) == 9.0
    assert nearest_rank([5.0, 1.0, 3.0], 0.5) == 3.0


def test_reading_the_statistics_changes_nothing_in_the_database(database: Path) -> None:
    before: bytes = database.read_bytes()
    collect_statistics(database, 0.0)
    assert database.read_bytes() == before


def test_a_database_that_is_not_there_is_reported_not_created(tmp_path: Path) -> None:
    missing: Path = tmp_path / "none.sqlite3"
    with pytest.raises(StoreError):
        collect_statistics(missing, 0.0)
    assert not missing.exists()


def test_the_statistics_are_rendered_per_model_in_plain_words(database: Path) -> None:
    stats: RunStatistics = collect_statistics(database, time.time() - 30 * DAY_S)
    lines: list[str] = render_stats(stats, 30, "all")
    text: str = "\n".join(lines)
    assert lines[0] == "3 runs in the last 30 days."
    assert "ollama:a: 2 runs" in text
    assert "finished 1, stopped" in text
    assert (
        "15 calls, 10 failed (67 %), 1 with arguments that were not valid JSON, 2 refused for arguments that did not "
        "fit (a turn lost each)"
    ) in text
    assert "stalls 1, compactions 1 (25.0 per 100 steps, 4.0 s, 1 % of the model time)" in text
    assert "web failures: not found 2, throttled 1, blocked 1, in transit 1, other 0" in text
    assert "delegated runs 1, waited 30.0 s for them" in text
    assert "acts: 3 calls, 67 % with one step, 33 % failed, median 2.0 s" in text
    assert "by step number: 1-5 1.5 s (1), 6-10 4.0 s (1), 11-20 9.0 s / 12.0 k tokens (1)" in text
    assert "acts: none counted" in text
    time_row: str = next(line for line in lines if line.strip().startswith("ollama:a ") and "of 2" in line)
    assert time_row.split() == ["ollama:a", "1", "of", "2", "320.5", "2.5", "10.0", "4.0", "4.5", "150", "25", "%"]
    assert "1 runs recorded no model calls" in text
    tool_row: str = next(line for line in lines if line.strip().startswith("browser_act"))
    assert tool_row.split() == ["browser_act", "5", "1", "14.0", "2.0", "1.5"]
    site_row: str = next(line for line in lines if line.strip().startswith("x.com"))
    assert site_row.split() == ["x.com", "2", "50", "%", "0", "%", "2.5"]
    assert render_stats(stats, 30, "site")[2].startswith("Sites, from the browser and desktop act calls")
    assert not any(line.startswith("Sites") for line in render_stats(stats, 30, "model"))
    assert render_stats(RunStatistics(models=(), tools=(), sites=(), channels=(), learning=NO_LEARNING), 7, "all") == [
        "No runs in the last 7 days."
    ]


def _act(site: str) -> tuple[str, JsonObject]:
    return "tool_result", {
        "call_id": "a",
        "tool": "browser_act",
        "ok": True,
        "text": "",
        "images": [],
        "seconds": 1.0,
        "site": site,
        "steps": 1,
    }


async def test_the_learning_section_scores_the_notes_handed_to_runs_and_compares_the_playbook_runs(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    note = await store.add_memory("Log in before the search.", ["lesson"], "run_earlier", None, None)
    reader: RunRecord = await _run(store, thread.id, "ollama:a", "done", "answer", 3, 300)
    await store.mark_memories_used(reader.id, [note.id], time.time(), "start")
    await store.close_memory_uses(reader.id, "done")
    await _events(
        store,
        reader.id,
        [
            ("tool_call", {"call_id": "s", "tool": "skill", "args": {"name": "playbook-x-com"}}),
            _act("x.com"),
            _act("x.com"),
            ("lessons_proposed", {"lessons": ["a", "b"], "tainted": False}),
            ("lesson_saved", {"index": 0, "by": "owner", "memory_id": note.id}),
            ("playbook_written", {"name": "playbook-x-com", "host": "x.com", "path": "p"}),
        ],
    )
    other: RunRecord = await _run(store, thread.id, "ollama:a", "failed", "no", 5, 500)
    await store.mark_memories_used(other.id, [note.id], time.time(), "search")
    await store.close_memory_uses(other.id, "failed")
    await _events(store, other.id, [_act("x.com"), _act("x.com"), _act("x.com"), _act("x.com"), _act("Safari")])
    await store.close()
    stats: RunStatistics = collect_statistics(tmp_path / "dot.sqlite3", time.time() - DAY_S)
    learning: LearningStats = stats.learning
    assert (learning.lessons_proposed, learning.lessons_saved, learning.lessons_dismissed) == (2, 1, 0)
    assert learning.runs_handed_notes == 2
    assert learning.notes == (NoteScore(memory_id=note.id, handed=2, done=1),)
    assert (learning.playbooks_written, learning.playbooks_retired, learning.runs_reading_playbooks) == (1, 0, 1)
    assert len(learning.comparisons) == 1
    comparison: PlaybookComparison = learning.comparisons[0]
    assert comparison.host == "x.com"
    assert comparison.read.runs == 1 and comparison.read.acts_per_run == 2.0
    assert comparison.unread.runs == 1 and comparison.unread.acts_per_run == 4.0
    lines: list[str] = render_learning(learning)
    assert lines[0] == "Learning"
    assert lines[1] == "  lessons: 2 proposed, 1 saved, 0 dismissed"
    assert lines[2] == f"  notes handed to 2 runs; {note.id} to 2 (1 done)"
    assert lines[3] == "  playbooks: 1 written, 0 retired, read by 1 runs"
    assert lines[4].startswith("  x.com: with its playbook 1 runs, 2.0 acts and ")


async def test_a_note_is_scored_by_how_the_runs_handed_it_ended(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    note = await store.add_memory("A note.", [], "run_earlier", None, None)
    assert await store.memory_scores([note.id], 0.0) == {}
    done: RunRecord = await _run(store, thread.id, "ollama:a", "done", "answer", 1, 10)
    await store.mark_memories_used(done.id, [note.id], time.time(), "start")
    await store.mark_memories_used(done.id, [note.id], time.time(), "live")
    await store.close_memory_uses(done.id, "done")
    working: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:a", None, "test", "medium", "ask", False, None, None, None
    )
    await store.mark_memories_used(working.id, [note.id], time.time(), "search")
    assert await store.memory_scores([note.id], 0.0) == {note.id: MemoryScore(handed=2, done=1, failed=0)}
    assert (await store.get_memory(note.id)).used_count == 3
    await store.close()


async def test_the_scores_of_the_notes_a_run_was_handed_are_kept_with_its_end_or_not_at_all(tmp_path: Path) -> None:
    """Item 183: the scores of a run's notes are part of the one write of its end (item 179): an end the database
    refuses scores nothing, and the end stored later scores them."""
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "screenshots")
    thread = await store.create_thread("t", "test")
    note = await store.add_memory("A note.", [], "run_earlier", None, None)
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:a", None, "test", "medium", "ask", False, None, None, None
    )
    await store.mark_memories_used(run.id, [note.id], time.time(), "start")
    connection: sqlite3.Connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TRIGGER refuse_ends BEFORE INSERT ON events WHEN NEW.kind = 'run_finished'"
        " BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
    )
    connection.commit()
    with pytest.raises(StoreError, match="the database refused the write"):
        await store.end_run(run.id, "done", "answer", {"status": "done", "result": "answer"})
    assert (await store.get_run(run.id)).status == "queued"
    assert await store.memory_scores([note.id], 0.0) == {note.id: MemoryScore(handed=1, done=0, failed=0)}
    connection.execute("DROP TRIGGER refuse_ends")
    connection.commit()
    connection.close()
    await store.end_run(run.id, "done", "answer", {"status": "done", "result": "answer"})
    assert await store.memory_scores([note.id], 0.0) == {note.id: MemoryScore(handed=1, done=1, failed=0)}
    await store.close()
