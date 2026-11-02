"""The watchdog (item 925): which runs it sees as stuck, from a real store's file read through a read-only connection,
and what a look does about the daemon and about a stuck run, with launchd, the health check, the restart and the
daemon's answer stood in for: no test loads a job, restarts a daemon or shows a notification."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from dotagent import watchdog
from dotagent.config import load_default_config
from dotagent.core.errors import ServiceError
from dotagent.launchd import JobState
from dotagent.server.views import HealthView
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.watchdog import MISSES_BEFORE_RESTART, StuckRun, WatchdogState, read_state, stuck_after_s, stuck_runs

pytestmark = pytest.mark.asyncio

_HOUR_S: float = 3600.0


async def _run(store: Store, goal: str) -> RunRecord:
    thread = await store.create_thread(goal, "ui")
    return await store.create_run(
        thread.id, goal, "task", "ollama:none", None, "ui", "medium", "standard", None, "yolo", False, None, None, None
    )


async def test_a_run_that_works_with_no_event_for_long_is_stuck_and_no_other_is(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        quiet: RunRecord = await _run(store, "Build the site and deploy it")
        busy: RunRecord = await _run(store, "Read the inbox")
        waiting: RunRecord = await _run(store, "Send the offer")
        ended: RunRecord = await _run(store, "Sort the receipts")
        for run in (quiet, busy, waiting):
            await store.set_run_status(run.id, "running")
            await store.add_event(run.id, "step_started", {"step": 1})
        await store.set_run_status(waiting.id, "blocked")
        await store.finish_run(ended.id, "done", "Sorted.")
    finally:
        await store.close()
    now: float = time.time()
    # The quiet run's events, and the ended and the waiting ones', an hour old; the busy run's are fresh.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE events SET ts = ? WHERE run_id IN (?, ?, ?)", (now - _HOUR_S, quiet.id, waiting.id, ended.id)
        )
        connection.execute("UPDATE runs SET created_at = ?", (now - 2 * _HOUR_S,))
        connection.commit()
    found: list[StuckRun] = stuck_runs(database, now, 30 * 60.0)
    assert [run.run_id for run in found] == [quiet.id]
    assert found[0].goal == "Build the site and deploy it" and found[0].quiet_s >= _HOUR_S


async def test_a_run_is_stuck_only_past_the_longest_a_step_may_take() -> None:
    config = load_default_config()
    longest: float = max(float(config.agent.model_call_timeout_s), float(config.shell.max_timeout_s))
    assert stuck_after_s(config) >= 30 * 60.0 and stuck_after_s(config) > longest


class _Seen:
    def __init__(self) -> None:
        self.restarts: int = 0
        self.notified: list[str] = []
        self.told: list[str] = []


@pytest.fixture
def seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Seen:
    """A daemon's job with a process, no restart waiting, and stand-ins for the restart, the notification, the token
    and the daemon's answer to a stuck run."""
    record: _Seen = _Seen()
    monkeypatch.setattr(watchdog, "dot_home", lambda: tmp_path)

    async def job(uid: int) -> JobState:
        return JobState(state="running", pid=4242, runs=1, last_exit=None)

    async def no_restart(uid: int) -> None:
        return None

    async def restart(plist: Path, uid: int, url: str) -> HealthView:
        record.restarts += 1
        return HealthView(ok=True, version="0.0.0-test")

    async def notify(title: str, message: str) -> None:
        record.notified.append(title)

    async def tell(base: str, token: str, run: StuckRun) -> None:
        record.told.append(run.run_id)

    monkeypatch.setattr(watchdog, "inspect_job", job)
    monkeypatch.setattr(watchdog, "inspect_restart_job", no_restart)
    monkeypatch.setattr(watchdog, "restart_service", restart)
    monkeypatch.setattr(watchdog, "show_notification", notify)
    monkeypatch.setattr(watchdog, "tell_stuck", tell)
    monkeypatch.setattr(watchdog, "read_token", lambda path: "token")
    return record


async def test_a_daemon_that_does_not_answer_three_looks_in_a_row_is_restarted_once(
    seen: _Seen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs(url: str) -> HealthView | None:
        raise ServiceError("the health check of the daemon failed on the way", {"url": url})

    monkeypatch.setattr(watchdog, "probe_daemon", hangs)
    for look in range(1, MISSES_BEFORE_RESTART):
        assert await watchdog.run(501) == 0
        assert read_state(tmp_path / "watchdog.json").misses == look and seen.restarts == 0
    assert await watchdog.run(501) == 0
    assert seen.restarts == 1 and seen.notified == ["Dot was restarted"]
    assert read_state(tmp_path / "watchdog.json").misses == 0


async def test_a_stuck_run_is_told_once_and_a_daemon_that_answers_is_left_alone(
    seen: _Seen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def answers(url: str) -> HealthView:
        return HealthView(ok=True, version="0.0.0-test")

    def stuck(database: Path, now: float, quiet_s: float) -> list[StuckRun]:
        return [StuckRun(run_id="run_mvz1a2b3_0a1b2c", goal="Build the site", quiet_s=2400.0)]

    monkeypatch.setattr(watchdog, "probe_daemon", answers)
    monkeypatch.setattr(watchdog, "stuck_runs", stuck)
    assert await watchdog.run(501) == 0
    assert await watchdog.run(501) == 0
    assert seen.told == ["run_mvz1a2b3_0a1b2c"] and seen.restarts == 0
    assert read_state(tmp_path / "watchdog.json") == WatchdogState(misses=0, told=("run_mvz1a2b3_0a1b2c",))


async def test_a_run_inside_a_call_is_stuck_only_past_that_calls_own_limit(tmp_path: Path) -> None:
    """The pre-push review of item 925: a run waiting forty minutes on its helpers (a fan-out, a ``delegate``: four
    hours) writes no event meanwhile and was told as stuck. Its open call's event carries the call's limit, and the run
    counts as busy until that has passed; a call that has its result counts for nothing."""
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        helping: RunRecord = await _run(store, "Count the lines of both files")
        answered: RunRecord = await _run(store, "Read the inbox")
        for run in (helping, answered):
            await store.set_run_status(run.id, "running")
            await store.add_event(
                run.id, "tool_call", {"call_id": "c1", "tool": "update_plan", "args": {}, "timeout_s": 4 * _HOUR_S}
            )
        await store.add_event(answered.id, "tool_result", {"call_id": "c1", "tool": "update_plan", "ok": True})
    finally:
        await store.close()
    now: float = time.time()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE events SET ts = ?", (now - 40 * 60.0,))
        connection.execute("UPDATE runs SET created_at = ?", (now - _HOUR_S,))
        connection.commit()
    assert [run.run_id for run in stuck_runs(database, now, 30 * 60.0)] == [answered.id]
    # Five hours on, past the call's own four and the margin, the run inside it is stuck too.
    later: float = now + 5 * _HOUR_S
    assert sorted(run.run_id for run in stuck_runs(database, later, 30 * 60.0)) == sorted([helping.id, answered.id])


async def test_a_tell_that_fails_keeps_the_runs_told_before_it(
    seen: _Seen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def answers(url: str) -> HealthView:
        return HealthView(ok=True, version="0.0.0-test")

    def stuck(database: Path, now: float, quiet_s: float) -> list[StuckRun]:
        return [
            StuckRun(run_id="run_mvz1a2b3_0a1b2c", goal="Build the site", quiet_s=2400.0),
            StuckRun(run_id="run_mvz1a2b3_3d4e5f", goal="Sort the receipts", quiet_s=2400.0),
        ]

    async def tells_one(base: str, token: str, run: StuckRun) -> None:
        if run.run_id.endswith("3d4e5f"):
            raise ServiceError("the daemon did not take the stuck run", {"run_id": run.run_id, "status": 500})
        seen.told.append(run.run_id)

    monkeypatch.setattr(watchdog, "probe_daemon", answers)
    monkeypatch.setattr(watchdog, "stuck_runs", stuck)
    monkeypatch.setattr(watchdog, "tell_stuck", tells_one)
    assert await watchdog.run(501) == 2
    assert read_state(tmp_path / "watchdog.json").told == ("run_mvz1a2b3_0a1b2c",)
    assert await watchdog.run(501) == 2
    assert seen.told == ["run_mvz1a2b3_0a1b2c"]


async def test_a_restart_that_fails_is_told_and_tried_again_three_looks_on(
    seen: _Seen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs(url: str) -> HealthView | None:
        raise ServiceError("the health check of the daemon failed on the way", {"url": url})

    async def fails(plist: Path, uid: int, url: str) -> HealthView:
        seen.restarts += 1
        raise ServiceError("launchctl kickstart failed", {"status": 5})

    monkeypatch.setattr(watchdog, "probe_daemon", hangs)
    monkeypatch.setattr(watchdog, "restart_service", fails)
    for _ in range(MISSES_BEFORE_RESTART - 1):
        assert await watchdog.run(501) == 0
    assert await watchdog.run(501) == 2
    assert seen.restarts == 1 and seen.notified == ["Dot does not answer"]
    assert read_state(tmp_path / "watchdog.json").misses == 0
    assert await watchdog.run(501) == 0 and seen.restarts == 1
