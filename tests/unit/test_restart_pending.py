"""The restart that waits for Dot to be idle keeps a flag in the store, tells the owner which run holds it, goes on
waiting after the two hours and restarts once when Dot is idle; the launchd restart retries a refused load and falls
back to ``kickstart -k``, and the watchdog loads a job that is not loaded (item 1310). Real SQLite for the runs and the
flag; stand-ins for launchctl, the banner, the restart and the clock."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent import launchd, restart_when_idle, watchdog
from dotagent.cli_daemon import _restart_line
from dotagent.core.errors import ServiceError
from dotagent.core.runenv import RESTART_ASKER_MARKER, RUN_ID_MARKER, RUN_SHELL_MARKER, asking_run
from dotagent.core.types import JsonObject
from dotagent.launchd import JobState, LaunchctlResult, build_restart_job, load_job, restart_job
from dotagent.restart_flag import (
    NOTICE_AFTER_S,
    TOLD_KEY,
    RestartFlag,
    clear_flag,
    parse_flag,
    pending_for,
    read_flag,
    write_flag,
)
from dotagent.restart_notice import NOTICE_TITLE, tell_if_due
from dotagent.server.routes_system import restart_pending_of
from dotagent.server.views import HealthView
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, RunStatus
from dotagent.watchdog import MISSES_BEFORE_RESTART, WatchdogState

UID: int = 501
ASKER: str = "run_mwr0asker_000000"


async def _run_in(store: Store, status: RunStatus, number: int, goal: str = "Rebuild the site") -> RunRecord:
    thread = await store.create_thread(f"chat {number}", "test")
    run: RunRecord = await store.create_run(
        thread.id, goal, "task", "ollama:a", None, "test", "medium", "standard", None, "ask", False, None, None, "o"
    )
    if status != "queued":
        await store.set_run_status(run.id, status)
    return run


# ------------------------------------------------------------------------------------------------- the flag


def test_the_flag_is_written_read_and_removed_and_a_value_that_is_no_flag_is_none(tmp_path: Path) -> None:
    import sqlite3

    database: Path = tmp_path / "dot.db"
    assert read_flag(database) is None, "no database yet"
    sqlite3.connect(database).executescript("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);").close()
    assert read_flag(database) is None
    flag: RestartFlag = RestartFlag(since=100.0, asked_by=ASKER, waiting_for="run_x", gave_up=False)
    write_flag(database, flag)
    write_flag(database, flag.model_copy(update={"gave_up": True}))
    assert read_flag(database) == flag.model_copy(update={"gave_up": True})
    clear_flag(database)
    clear_flag(database)
    assert read_flag(database) is None
    assert parse_flag("not json") is None and parse_flag('{"since": "x"}') is None and parse_flag(None) is None


def test_a_flag_older_than_the_daemon_that_reads_it_is_no_pending_restart() -> None:
    flag: RestartFlag = RestartFlag(since=100.0, asked_by="", waiting_for="", gave_up=False)
    assert pending_for(flag, 99.0) == flag and pending_for(flag, 100.5) is None and pending_for(None, 0.0) is None


# ------------------------------------------------------------------------------------- the program waits


class _Seen:
    """What the stand-ins saw: the banners, the restarts, and the order of the two."""

    def __init__(self) -> None:
        self.banners: list[tuple[str, str]] = []
        self.restarts: int = 0


async def _flag_naming(database: Path, run_id: str) -> RestartFlag | None:
    """The restart flag once the waiter wrote ``run_id`` into it, up to 10 s; the flag as it stands after that (or
    None, gone) when it never did, for the caller's assertion to name."""
    for _ in range(1000):
        flag: RestartFlag | None = read_flag(database)
        if flag is not None and flag.waiting_for == run_id:
            return flag
        await asyncio.sleep(0.01)
    return read_flag(database)


@pytest.fixture
def seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Seen:
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    record: _Seen = _Seen()

    async def banner(title: str, message: str) -> None:
        record.banners.append((title, message))

    async def restart(plist: Path, uid: int, url: str) -> HealthView:
        record.restarts += 1
        return HealthView(ok=True, version="0.0.0-test", notifications="off")

    async def no_night() -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(restart_when_idle, "show_notification", banner)
    monkeypatch.setattr(restart_when_idle, "restart_service", restart)
    monkeypatch.setattr(restart_when_idle, "night_look", lambda config: no_night)
    monkeypatch.setattr(restart_when_idle, "RESTART_POLL_S", 0.01)
    monkeypatch.setattr(restart_when_idle, "RESTART_WAIT_LIMIT_S", 0.1)
    monkeypatch.setattr(restart_when_idle, "RESTART_NIGHT_LIMIT_S", 0.1)
    monkeypatch.setattr(restart_when_idle, "RESTART_SECOND_CHANCE_S", 30.0)
    return record


async def test_a_restart_that_gave_up_keeps_its_flag_and_restarts_once_when_dot_is_idle(
    tmp_path: Path, seen: _Seen
) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        # The run that holds it is blocked on the owner: that is work, not idleness.
        holder: RunRecord = await _run_in(store, "blocked", 1)
        program: asyncio.Task[int] = asyncio.create_task(restart_when_idle.wait_and_restart(database, UID, ASKER))
        waiting: RestartFlag | None = await _flag_naming(database, holder.id)
        assert waiting is not None and waiting.asked_by == ASKER and waiting.waiting_for == holder.id
        assert not waiting.gave_up and seen.banners == []
        for _ in range(1000):
            if seen.banners:
                break
            await asyncio.sleep(0.01)
        # The two hours (here a tenth of a second) are over: one banner, the flag says so, nothing was restarted.
        assert not program.done() and seen.restarts == 0
        assert [title for title, _ in seen.banners] == ["Dot was not restarted yet"]
        gave_up: RestartFlag | None = read_flag(database)
        assert gave_up is not None and gave_up.gave_up and gave_up.since == waiting.since
        assert gave_up.waiting_for == holder.id
        # The run is not stopped and a new one may start: the restart waits while any works.
        other: RunRecord = await _run_in(store, "running", 2)
        await store.set_run_status(holder.id, "done")
        # The waiter's next look names the other run; waited for, not timed, as a loaded runner looks late.
        moved: RestartFlag | None = await _flag_naming(database, other.id)
        assert not program.done() and seen.restarts == 0
        assert moved is not None and moved.gave_up and moved.waiting_for == other.id
        await store.set_run_status(other.id, "done")
        assert await asyncio.wait_for(program, 10.0) == 0
        assert seen.restarts == 1 and len(seen.banners) == 1
        assert read_flag(database) is None
    finally:
        await store.close()


async def test_a_restart_with_no_run_at_work_when_it_is_asked_restarts_at_once_and_says_nothing(
    tmp_path: Path, seen: _Seen
) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        ended: RunRecord = await _run_in(store, "running", 1)
        await store.set_run_status(ended.id, "done")
        assert await asyncio.wait_for(restart_when_idle.wait_and_restart(database, UID, ASKER), 10.0) == 0
        assert seen.restarts == 1 and seen.banners == [] and read_flag(database) is None
    finally:
        await store.close()


async def test_the_run_that_asked_is_waited_for_like_any_other_and_its_end_lets_the_restart_go(
    tmp_path: Path, seen: _Seen, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_when_idle, "RESTART_WAIT_LIMIT_S", 30.0)
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        asker: RunRecord = await _run_in(store, "running", 1)
        program: asyncio.Task[int] = asyncio.create_task(restart_when_idle.wait_and_restart(database, UID, asker.id))
        await asyncio.sleep(0.2)
        flag: RestartFlag | None = read_flag(database)
        assert flag is not None and flag.asked_by == flag.waiting_for == asker.id
        assert not program.done() and seen.restarts == 0, "the daemon is not restarted under the run that asked"
        await store.set_run_status(asker.id, "done")
        assert await asyncio.wait_for(program, 10.0) == 0 and seen.restarts == 1
    finally:
        await store.close()


async def test_a_second_chance_that_ends_without_idleness_takes_the_flag_away(
    tmp_path: Path, seen: _Seen, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_when_idle, "RESTART_SECOND_CHANCE_S", 0.1)
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        await _run_in(store, "running", 1)
        assert await asyncio.wait_for(restart_when_idle.wait_and_restart(database, UID, ""), 10.0) == 1
        assert seen.restarts == 0 and len(seen.banners) == 1 and read_flag(database) is None
    finally:
        await store.close()


# -------------------------------------------------------------------------- the daemon tells the owner


class _Bus:
    def __init__(self) -> None:
        self.told: list[JsonObject] = []

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> None:
        self.told.append(data)


async def test_the_owner_is_told_once_at_thirty_minutes_which_run_holds_the_restart(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        holder: RunRecord = await _run_in(store, "running", 1, "Rebuild the site for the Oulu client")
        bus: _Bus = _Bus()
        runtime = SimpleNamespace(store=store, bus=bus)
        since: float = 10_000.0
        flag: RestartFlag = RestartFlag(since=since, asked_by="", waiting_for=holder.id, gave_up=False)
        write_flag(database, flag)
        started: float = since - 50.0
        assert not await tell_if_due(runtime, since + NOTICE_AFTER_S - 1, started)  # type: ignore[arg-type]
        assert bus.told == []
        assert await tell_if_due(runtime, since + NOTICE_AFTER_S + 5, started)  # type: ignore[arg-type]
        assert not await tell_if_due(runtime, since + NOTICE_AFTER_S + 60, started)  # type: ignore[arg-type]
        assert len(bus.told) == 1 and bus.told[0]["title"] == NOTICE_TITLE
        assert holder.id in str(bus.told[0]["text"]) and "Rebuild the site" in str(bus.told[0]["text"])
        assert bus.told[0]["url"] == f"#/runs/{holder.id}"
        assert await store.kv_get(TOLD_KEY) == repr(since)
        # A restart asked for again is told again; one that waits for the night's learning says so.
        write_flag(database, RestartFlag(since=since + 9000, asked_by="", waiting_for="", gave_up=False))
        assert await tell_if_due(runtime, since + 9000 + NOTICE_AFTER_S, started)  # type: ignore[arg-type]
        assert "night's learning" in str(bus.told[1]["text"])
        # A restart a run asked for is not said to be the owner's: the words are neutral and name the run.
        write_flag(
            database, RestartFlag(since=since + 20_000, asked_by=holder.id, waiting_for=holder.id, gave_up=False)
        )
        assert await tell_if_due(runtime, since + 20_000 + NOTICE_AFTER_S, started)  # type: ignore[arg-type]
        assert "you asked" not in str(bus.told[2]["text"]) and "the task that asked" in str(bus.told[2]["text"])
        # The one the daemon's own start answered is not told.
        write_flag(database, RestartFlag(since=5.0, asked_by="", waiting_for=holder.id, gave_up=True))
        assert not await tell_if_due(runtime, 99_999.0, started)  # type: ignore[arg-type]
    finally:
        await store.close()


async def test_the_status_carries_the_pending_restart_and_none_when_none_waits(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        runtime = SimpleNamespace(store=store)
        assert await restart_pending_of(runtime, 0.0) is None  # type: ignore[arg-type]
        write_flag(database, RestartFlag(since=time.time(), asked_by=ASKER, waiting_for="run_y", gave_up=True))
        pending = await restart_pending_of(runtime, 0.0)  # type: ignore[arg-type]
        assert pending is not None and pending.waiting_for == "run_y" and pending.asked_by == ASKER and pending.gave_up
        assert await restart_pending_of(runtime, time.time() + 60) is None  # type: ignore[arg-type]
    finally:
        await store.close()


def test_the_service_status_line_names_the_run_it_waits_for_and_says_when_the_time_ran_out() -> None:
    job: JobState = JobState(state="running", pid=77, runs=1, last_exit="(never exited)")
    flag: RestartFlag = RestartFlag(since=1000.0, asked_by="", waiting_for="run_abc", gave_up=False)
    waiting: str = _restart_line(job, flag, 1000.0 + 5400)
    assert "pending for 1.5 hours" in waiting and "waits for the run run_abc" in waiting
    spent: str = _restart_line(job, flag.model_copy(update={"gave_up": True}), 1000.0 + 8000)
    assert "next time no run works" in spent and "are over" in spent and "run_abc" in spent
    assert "no restart waits" in _restart_line(None, flag, 5.0)
    assert "pending" not in _restart_line(job)


def test_the_run_that_asks_is_named_in_the_job_it_loads() -> None:
    environ: dict[str, str] = {RUN_SHELL_MARKER: "1", RUN_ID_MARKER: ASKER}
    assert asking_run(environ) == ASKER
    assert asking_run({RUN_ID_MARKER: ASKER}) is None, "a command a person started names no run"
    assert asking_run({RUN_SHELL_MARKER: "1"}) is None
    job = build_restart_job("/p/python", Path("/h"), Path("/l"), {}, ASKER)
    assert job.environment[RESTART_ASKER_MARKER] == ASKER
    assert RESTART_ASKER_MARKER not in build_restart_job("/p/python", Path("/h"), Path("/l"), {}).environment


# ------------------------------------------------------------------------------------- launchd and watchdog


class _Launchctl:
    """A stand-in for ``run_launchctl``: ``fail`` lists the commands (by their first word, in order) that fail once each
    time they appear in it; the rest succeed."""

    def __init__(self, *failing: str) -> None:
        self.failing: list[str] = list(failing)
        self.calls: list[str] = []

    async def __call__(self, arguments: list[str]) -> LaunchctlResult:
        self.calls.append(arguments[0])
        if arguments[0] in self.failing:
            self.failing.remove(arguments[0])
            return LaunchctlResult(arguments=arguments, returncode=5, stdout="", stderr="Bootstrap failed: 5")
        return LaunchctlResult(arguments=arguments, returncode=0, stdout="", stderr="")


@pytest.fixture
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unloaded(target: str) -> None:
        return None

    monkeypatch.setattr(launchd, "_wait_until_unloaded", unloaded)
    monkeypatch.setattr(launchd, "_BOOTSTRAP_RETRIES_S", (0.0, 0.0, 0.0))


async def test_a_restart_loads_the_job_with_one_bootstrap_when_launchd_takes_it(
    monkeypatch: pytest.MonkeyPatch, no_waits: None
) -> None:
    ctl: _Launchctl = _Launchctl()
    monkeypatch.setattr(launchd, "run_launchctl", ctl)
    await restart_job(Path("/p/dot.plist"), UID)
    assert ctl.calls == ["bootout", "bootstrap"]


async def test_a_refused_load_is_tried_again_with_waits_that_grow_and_then_kickstarted(
    monkeypatch: pytest.MonkeyPatch, no_waits: None
) -> None:
    """Items 1310 and 1941: launchd's busy label can last seconds after the unload, so the load is tried four times
    (after 2, 5 and 10 s) before the job is kickstarted, and a restart leaves the daemon down only when all fail."""
    once: _Launchctl = _Launchctl("bootstrap")
    monkeypatch.setattr(launchd, "run_launchctl", once)
    await restart_job(Path("/p/dot.plist"), UID)
    assert once.calls == ["bootout", "bootstrap", "bootstrap"]
    thrice: _Launchctl = _Launchctl("bootstrap", "bootstrap", "bootstrap")
    monkeypatch.setattr(launchd, "run_launchctl", thrice)
    await restart_job(Path("/p/dot.plist"), UID)
    assert thrice.calls == ["bootout", *["bootstrap"] * 4]
    always: _Launchctl = _Launchctl(*["bootstrap"] * 4)
    monkeypatch.setattr(launchd, "run_launchctl", always)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(launchd, "_BOOTSTRAP_RETRIES_S", (2.0, 5.0, 10.0))
    await load_job(Path("/p/dot.plist"), UID, sleep)
    assert always.calls == [*["bootstrap"] * 4, "kickstart"] and waits == [2.0, 5.0, 10.0]


async def test_a_load_that_nothing_starts_raises_what_each_step_said(
    monkeypatch: pytest.MonkeyPatch, no_waits: None
) -> None:
    ctl: _Launchctl = _Launchctl(*["bootstrap"] * 4, "kickstart")
    monkeypatch.setattr(launchd, "run_launchctl", ctl)
    with pytest.raises(ServiceError, match="kickstart") as caught:
        await load_job(Path("/p/dot.plist"), UID)
    assert set(caught.value.context) == {"bootstrap", "kickstart"}
    assert ctl.calls == [*["bootstrap"] * 4, "kickstart"]


class _Watched:
    def __init__(self) -> None:
        self.loads: int = 0
        self.notified: list[str] = []
        self.answering: bool = False
        self.restart_waits: bool = False


@pytest.fixture
def watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Watched:
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    record: _Watched = _Watched()

    async def loaded(uid: int) -> JobState | None:
        return None

    async def waiting(uid: int) -> JobState | None:
        return JobState(state="running", pid=9, runs=1, last_exit=None) if record.restart_waits else None

    async def load(plist: Path, uid: int) -> None:
        record.loads += 1

    async def notify(title: str, message: str) -> None:
        record.notified.append(title)

    async def answers(url: str) -> bool:
        return record.answering

    monkeypatch.setattr(watchdog, "inspect_job", loaded)
    monkeypatch.setattr(watchdog, "inspect_restart_job", waiting)
    monkeypatch.setattr(watchdog, "load_job", load)
    monkeypatch.setattr(watchdog, "show_notification", notify)
    monkeypatch.setattr(watchdog, "_answers", answers)
    return record


def _plist(tmp_path: Path) -> None:
    folder: Path = tmp_path / "user" / "Library" / "LaunchAgents"
    folder.mkdir(parents=True)
    (folder / f"{launchd.LABEL}.plist").write_bytes(b"<plist/>")


async def test_a_daemon_job_that_is_not_loaded_though_its_file_is_there_is_loaded_after_three_looks(
    watched: _Watched, tmp_path: Path
) -> None:
    _plist(tmp_path)
    for look in range(1, MISSES_BEFORE_RESTART):
        assert await watchdog.run(UID) == 0
        assert watchdog.read_state(tmp_path / "home" / "watchdog.json").misses == look and watched.loads == 0
    assert await watchdog.run(UID) == 0
    assert watched.loads == 1 and watched.notified == ["Dot was started"]
    assert watchdog.read_state(tmp_path / "home" / "watchdog.json") == WatchdogState(misses=0, told=())


async def test_a_load_that_keeps_failing_is_told_to_the_owner_once_and_tried_again_three_looks_on(
    watched: _Watched, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The banner came back every three minutes for as long as the load failed; the tries go on, the banner is once, and
    a load that goes through says so and forgets the mark, so a later failure is told again."""
    attempts: list[int] = []
    refusing: list[bool] = [True]

    async def load(plist: Path, uid: int) -> None:
        attempts.append(1)
        if refusing[0]:
            raise ServiceError("launchd refused the job", {})

    monkeypatch.setattr(watchdog, "load_job", load)
    _plist(tmp_path)
    state_file: Path = tmp_path / "home" / "watchdog.json"
    for round_ in range(1, 4):
        for _ in range(MISSES_BEFORE_RESTART):
            await watchdog.run(UID)
        assert len(attempts) == round_, "the load is tried every three looks"
        assert watched.notified == ["Dot is not running"], "told at the first failure only"
        assert watchdog.read_state(state_file).misses == 0
    refusing[0] = False
    for _ in range(MISSES_BEFORE_RESTART):
        assert await watchdog.run(UID) == 0
    assert watched.notified == ["Dot is not running", "Dot was started"]
    assert watchdog.read_state(state_file) == WatchdogState(misses=0, told=())
    refusing[0] = True
    for _ in range(MISSES_BEFORE_RESTART):
        await watchdog.run(UID)
    assert watched.notified[-1] == "Dot is not running" and len(watched.notified) == 3


async def test_the_watchdog_loads_nothing_when_the_service_is_uninstalled_a_restart_waits_or_the_daemon_answers(
    watched: _Watched, tmp_path: Path
) -> None:
    async def looks(count: int) -> None:
        for _ in range(count):
            assert await watchdog.run(UID) == 0

    await looks(MISSES_BEFORE_RESTART + 1)  # no property list: not installed
    assert watched.loads == 0
    _plist(tmp_path)
    watched.restart_waits = True
    await looks(MISSES_BEFORE_RESTART + 1)
    assert watched.loads == 0, "a restart that waits loads the job itself"
    watched.restart_waits = False
    watched.answering = True
    await looks(MISSES_BEFORE_RESTART + 1)
    assert watched.loads == 0 and watchdog.read_state(tmp_path / "home" / "watchdog.json").misses == 0
