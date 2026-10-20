"""The restart that waits for the runs to end (item 441): the one-shot job's property list (it runs once and is never
started again by launchd), the check that no run works (against a database a real store made, read while the store
holds it open), how the job is loaded, replaced and unloaded (against a stand-in for ``launchctl``, so that the
launchd of this Mac is left alone), and the command line."""

import asyncio
import importlib.util
import plistlib
import subprocess
from collections.abc import MutableMapping
from pathlib import Path
from typing import get_args

import pytest
from structlog.testing import capture_logs

from dotagent import cli_daemon, launchd, restart_when_idle
from dotagent.cli import build_parser, parse_command
from dotagent.cli_commands import Command, ServiceInstallCommand, ServiceRestartCommand
from dotagent.cli_daemon import _duration, _restart_line, dispatch
from dotagent.core.errors import ServiceError
from dotagent.core.paths import db_path, dot_home, ensure_layout, screenshots_dir
from dotagent.core.runenv import RUN_SHELL_MARKER, started_by_a_run
from dotagent.launchd import (
    LABEL,
    RESTART_LABEL,
    RESTART_MODULE,
    SERVICE_PATH,
    JobState,
    RestartJob,
    build_restart_job,
    cancel_restart_job,
    inspect_restart_job,
    install_restart_job,
    launch_agent_path,
    release_restart_job,
    render_restart_job,
    restart_job,
    restart_job_path,
)
from dotagent.restart_when_idle import count_working_runs, wait_until_idle
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, RunRecord, RunStatus, ThreadRecord
from dotagent.tools.shell import child_env

PLUTIL: str = "/usr/bin/plutil"
UID: int = 501
HOME: Path = Path("/h/Dot")
LOGS: Path = Path("/h/Logs")


def _job(environ: dict[str, str]) -> RestartJob:
    return build_restart_job("/opt/dot/bin/python", HOME, LOGS, environ)


def test_the_job_runs_once_from_dots_home_and_launchd_never_starts_it_again() -> None:
    parsed: dict[str, object] = plistlib.loads(render_restart_job(_job({})))
    assert parsed["Label"] == RESTART_LABEL
    assert RESTART_LABEL != LABEL
    assert parsed["ProgramArguments"] == ["/opt/dot/bin/python", "-P", "-m", RESTART_MODULE]
    assert parsed["RunAtLoad"] is True
    assert parsed["AbandonProcessGroup"] is True
    # A job with a KeepAlive is started again each time it exits: the improvised restart ran every 30 s.
    assert "KeepAlive" not in parsed
    assert parsed["WorkingDirectory"] == str(HOME)
    assert parsed["StandardOutPath"] == "/h/Logs/restart-when-idle.out.log"
    assert parsed["StandardErrorPath"] == "/h/Logs/restart-when-idle.err.log"
    assert parsed["EnvironmentVariables"] == {"PATH": SERVICE_PATH}


def test_the_job_is_handed_the_home_and_the_log_directory_only_when_the_environment_sets_them() -> None:
    assert _job({"HOME": "/Users/someone"}).environment == {"PATH": SERVICE_PATH}
    relocated: dict[str, str] = _job({"DOT_HOME": "/x", "DOT_LOG_DIR": "/y"}).environment
    assert (relocated["DOT_HOME"], relocated["DOT_LOG_DIR"]) == (str(HOME), str(LOGS))


def test_the_property_list_is_in_dots_home_and_names_a_module_that_exists() -> None:
    assert restart_job_path(HOME) == Path("/h/Dot/dev.dotagent.restart-when-idle.plist")
    assert importlib.util.find_spec(RESTART_MODULE) is not None


def test_macos_accepts_the_property_list(tmp_path: Path) -> None:
    plist: Path = tmp_path / f"{RESTART_LABEL}.plist"
    plist.write_bytes(render_restart_job(build_restart_job("/usr/bin/python3", tmp_path, tmp_path / "logs", {})))
    linted: subprocess.CompletedProcess[str] = subprocess.run(
        [PLUTIL, "-lint", str(plist)], capture_output=True, text=True, check=False
    )
    assert linted.returncode == 0, linted.stdout + linted.stderr


def test_the_command_line_asks_for_the_restart_now_or_when_no_run_works() -> None:
    def parsed(arguments: list[str]) -> Command:
        return parse_command(build_parser(), ["service", "restart", *arguments])

    assert parsed([]) == ServiceRestartCommand(name="service-restart", when_idle=False)
    assert parsed(["--when-idle"]) == ServiceRestartCommand(name="service-restart", when_idle=True)


def test_status_says_whether_a_restart_waits() -> None:
    assert "no restart waits" in _restart_line(None)
    waiting: str = _restart_line(JobState(state="running", pid=77, runs=1, last_exit="(never exited)"))
    assert "pid 77" in waiting
    assert "gives up after 2 hours" in waiting
    assert (_duration(3600), _duration(1800)) == ("1 hour", "30 minutes")


async def _run_in(store: Store, status: RunStatus, number: int) -> RunRecord:
    """A run in ``status``, in a chat of its own (a chat has one run at work at a time)."""
    thread: ThreadRecord = await store.create_thread(f"chat {number}", "test")
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:a", None, "test", "medium", "standard", None, "ask", False, None, None, "o"
    )
    if status != "queued":
        await store.set_run_status(run.id, status)
    return run


@pytest.mark.parametrize("status", get_args(RunStatus))
async def test_a_run_counts_as_working_in_the_statuses_the_store_says_it_works_in(
    tmp_path: Path, status: RunStatus
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "images")
    try:
        await _run_in(store, status, 1)
        assert count_working_runs(tmp_path / "dot.sqlite3") == (1 if status in WORKING_STATUSES else 0)
    finally:
        await store.close()


async def test_the_check_counts_every_run_at_work_while_the_store_holds_the_database_open(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        runs: list[RunRecord] = [
            await _run_in(store, status, number) for number, status in enumerate(get_args(RunStatus))
        ]
        assert count_working_runs(database) == len(WORKING_STATUSES)
        for run in runs:
            await store.set_run_status(run.id, "done")
        assert count_working_runs(database) == 0
    finally:
        await store.close()


def test_a_database_that_is_not_there_is_an_error_not_an_idle_daemon(tmp_path: Path) -> None:
    with pytest.raises(ServiceError, match="no database of runs"):
        count_working_runs(tmp_path / "missing.sqlite3")


async def test_the_wait_ends_when_the_last_run_ends_or_when_the_time_is_up(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        run: RunRecord = await _run_in(store, "running", 1)
        assert await wait_until_idle(database, 0.01, 0.05) == 1
        waiting: asyncio.Task[int] = asyncio.create_task(wait_until_idle(database, 0.01, 30.0))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await store.set_run_status(run.id, "done")
        assert await asyncio.wait_for(waiting, 10.0) == 0
    finally:
        await store.close()


@pytest.fixture
def launchctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for launchctl, which the launchd module runs in its place: it writes each call into ``calls`` of the
    returned directory and keeps each job it loads in a file of its own, ``loaded-gui_501_<label>``, which holds the
    property list the job was loaded from (a job that was never loaded is not described: exit code 113). Every job
    runs as the pid of the last load, 77 at first and one more at each load."""
    state: Path = tmp_path / "launchd"
    state.mkdir()
    (state / "printed").write_text("gui/501/job = {\n\tstate = running\n\tpid = 77\n}\n", encoding="utf-8")
    (state / "pid").write_text("77\n", encoding="utf-8")
    script: Path = state / "launchctl"
    script.write_text(
        "#!/bin/sh\n"
        f"cd '{state}' || exit 70\n"
        'echo "$@" >> calls\n'
        'key() { echo "loaded-$1" | tr / _; }\n'
        'case "$1" in\n'
        'print) [ -f "$(key "$2")" ] || exit 113; cat printed ;;\n'
        'bootout) rm -f "$(key "$2")" ;;\n'
        'bootstrap) cp "$3" "$(key "$2/$(basename "$3" .plist)")" || exit 5; n=$(( $(cat pid) + 1 )); echo "$n" > pid;'
        " printf 'gui/501/job = {\\n\\tstate = running\\n\\tpid = %s\\n}\\n' \"$n\" > printed ;;\n"
        "*) exit 64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(launchd, "_LAUNCHCTL", str(script))
    return state


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _loaded(state: Path, label: str) -> bytes | None:
    """The property list the stand-in loaded the job ``label`` from, ``None`` when it is not loaded."""
    return _read(state / f"loaded-gui_{UID}_{label}")


def _daemon_plist(folder: Path) -> Path:
    """The property list of the daemon's job, written into ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    plist: Path = folder / f"{LABEL}.plist"
    plist.write_bytes(b"<plist>the daemon</plist>\n")
    return plist


def _load_the_daemons_job(state: Path) -> None:
    (state / f"loaded-gui_{UID}_{LABEL}").write_bytes(b"<plist>the daemon</plist>\n")


def _calls(state: Path) -> list[str]:
    return (state / "calls").read_text(encoding="utf-8").splitlines()


async def test_asking_again_replaces_the_restart_that_waits_so_that_there_is_one(
    tmp_path: Path, launchctl: Path
) -> None:
    plist: Path = tmp_path / "Dot" / f"{RESTART_LABEL}.plist"
    target: str = f"gui/{UID}/{RESTART_LABEL}"
    assert await inspect_restart_job(UID) is None
    assert await install_restart_job(plist, b"<plist>first</plist>\n", UID) is False
    assert _loaded(launchctl, RESTART_LABEL) == b"<plist>first</plist>\n"

    assert await install_restart_job(plist, b"<plist>second</plist>\n", UID) is True
    assert _loaded(launchctl, RESTART_LABEL) == b"<plist>second</plist>\n"
    assert _loaded(launchctl, LABEL) is None
    assert _read(plist) == b"<plist>second</plist>\n"
    # The job that waits is told to go before the new one is loaded, and both are the one-shot's, not the daemon's.
    assert _calls(launchctl)[-4:] == [
        f"print {target}",
        f"bootout {target}",
        f"print {target}",
        f"bootstrap gui/{UID} {plist}",
    ]
    waiting: JobState | None = await inspect_restart_job(UID)
    assert waiting is not None and waiting.state == "running"


async def test_the_restart_unloads_its_own_job_and_deletes_its_property_list(tmp_path: Path, launchctl: Path) -> None:
    plist: Path = tmp_path / "Dot" / f"{RESTART_LABEL}.plist"
    await install_restart_job(plist, b"<plist/>\n", UID)
    await release_restart_job(plist, UID)
    assert _read(plist) is None
    assert _calls(launchctl)[-1] == f"bootout gui/{UID}/{RESTART_LABEL}"
    assert await inspect_restart_job(UID) is None


async def test_the_restart_now_unloads_the_job_of_the_daemon_and_loads_it_again(
    tmp_path: Path, launchctl: Path
) -> None:
    """A restart is a stop and a start (bootout sends SIGTERM), not a kill, so the daemon ends as at a stop."""
    plist: Path = _daemon_plist(tmp_path)
    _load_the_daemons_job(launchctl)
    await restart_job(plist, UID)
    assert _calls(launchctl)[0] == f"bootout gui/{UID}/{LABEL}"
    assert _calls(launchctl)[-1] == f"bootstrap gui/{UID} {plist}"
    assert _loaded(launchctl, LABEL) == _read(plist)


async def _health(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """The health check of a daemon that answers."""
    await reader.readuntil(b"\r\n\r\n")
    body: bytes = b'{"ok": true, "version": "test"}'
    writer.write(
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
        + str(len(body)).encode()
        + b"\r\nconnection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()


async def _scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: int) -> Store:
    """Dot's home and logs in ``tmp_path``, with a store whose runs the program looks at, a configuration that sends
    its health check to ``port``, the daemon's property list in a user home of ``tmp_path`` (the restart loads it
    again), and a program that looks every hundredth of a second."""
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    _daemon_plist(launch_agent_path(tmp_path / "user").parent)
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    ensure_layout()
    (dot_home() / "config.toml").write_text(f"[server]\nport = {port}\n", encoding="utf-8")
    monkeypatch.setattr(restart_when_idle, "RESTART_POLL_S", 0.01)
    return await Store.open(db_path(), screenshots_dir())


def _events(logs: list[MutableMapping[str, object]]) -> list[object]:
    return [entry["event"] for entry in logs]


async def test_the_program_waits_for_the_runs_restarts_the_daemon_once_and_unloads_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path
) -> None:
    server: asyncio.Server = await asyncio.start_server(_health, "127.0.0.1", 0)
    store: Store = await _scratch_home(tmp_path, monkeypatch, server.sockets[0].getsockname()[1])
    try:
        run: RunRecord = await _run_in(store, "running", 1)
        _load_the_daemons_job(launchctl)
        plist: Path = restart_job_path(dot_home())
        await install_restart_job(plist, b"<plist/>\n", UID)
        with capture_logs() as logs:
            program: asyncio.Task[int] = asyncio.create_task(restart_when_idle.run(UID))
            await asyncio.sleep(0.2)
            # The run that asked still works: the daemon is left alone.
            assert not program.done()
            assert not any(call.startswith(f"bootout gui/{UID}/{LABEL}") for call in _calls(launchctl))
            await store.set_run_status(run.id, "done")
            assert await asyncio.wait_for(program, 30.0) == 0
        assert _events(logs) == [
            "restart_when_idle_waiting",
            "restart_when_idle_idle",
            "restart_when_idle_restarted",
            "restart_when_idle_unloading",
        ]
        calls: list[str] = _calls(launchctl)
        assert calls.count(f"bootout gui/{UID}/{LABEL}") == 1
        assert calls[-1] == f"bootout gui/{UID}/{RESTART_LABEL}"
        assert _read(plist) is None
        assert _loaded(launchctl, RESTART_LABEL) is None
    finally:
        await store.close()
        server.close()
        await server.wait_closed()


async def test_the_program_gives_up_without_restarting_when_a_run_still_works_at_the_end_of_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path
) -> None:
    store: Store = await _scratch_home(tmp_path, monkeypatch, 9)
    monkeypatch.setattr(restart_when_idle, "RESTART_WAIT_LIMIT_S", 0.05)
    try:
        await _run_in(store, "blocked", 1)
        _load_the_daemons_job(launchctl)
        plist: Path = restart_job_path(dot_home())
        await install_restart_job(plist, b"<plist/>\n", UID)
        with capture_logs() as logs:
            assert await asyncio.wait_for(restart_when_idle.run(UID), 30.0) == 1
        assert _events(logs) == [
            "restart_when_idle_waiting",
            "restart_when_idle_gave_up",
            "restart_when_idle_unloading",
        ]
        assert not any(call.startswith(f"bootout gui/{UID}/{LABEL}") for call in _calls(launchctl))
        assert _calls(launchctl)[-1] == f"bootout gui/{UID}/{RESTART_LABEL}"
        assert _read(plist) is None
    finally:
        await store.close()


async def test_the_program_that_cannot_restart_the_daemon_says_so_and_still_unloads_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path
) -> None:
    store: Store = await _scratch_home(tmp_path, monkeypatch, 9)
    try:
        plist: Path = restart_job_path(dot_home())
        await install_restart_job(plist, b"<plist/>\n", UID)
        with capture_logs() as logs:
            assert await asyncio.wait_for(restart_when_idle.run(UID), 30.0) == 2
        failed: list[MutableMapping[str, object]] = [
            entry for entry in logs if entry["event"] == "restart_when_idle_failed"
        ]
        assert len(failed) == 1
        assert "not installed" in str(failed[0]["error"])
        assert not any(call.startswith(f"bootout gui/{UID}/{LABEL}") for call in _calls(launchctl))
        assert _read(plist) is None
    finally:
        await store.close()


async def test_uninstall_cancels_the_restart_that_waits(tmp_path: Path, launchctl: Path) -> None:
    """A restart that waited after the service was removed would find no job to restart: it goes with the service."""
    plist: Path = tmp_path / "Dot" / f"{RESTART_LABEL}.plist"
    await install_restart_job(plist, b"<plist/>\n", UID)
    assert await cancel_restart_job(plist, UID) is True
    assert await inspect_restart_job(UID) is None
    assert _read(plist) is None
    assert await cancel_restart_job(plist, UID) is False


def test_a_command_a_run_starts_carries_the_mark_of_a_run() -> None:
    assert started_by_a_run(child_env({"PATH": "/usr/bin"}, []))
    assert not started_by_a_run({"PATH": "/usr/bin"})


@pytest.mark.parametrize(
    "command",
    [ServiceRestartCommand(name="service-restart", when_idle=False), ServiceInstallCommand(name="service-install")],
)
async def test_a_run_cannot_restart_the_daemon_at_once(
    command: Command, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path
) -> None:
    """The restart would stop the daemon under the run, and the run's end would kill the command before it loads the
    daemon again: a run asks for the restart that waits."""
    monkeypatch.setenv(RUN_SHELL_MARKER, "1")
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    _load_the_daemons_job(launchctl)
    with pytest.raises(ServiceError, match="a run cannot"):
        await dispatch(command)
    assert not (launchctl / "calls").exists()


async def test_asking_while_a_restart_is_on_its_way_leaves_it_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replacing it could stop it between unloading the daemon's job and loading it again."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    ensure_layout()
    _load_the_daemons_job(launchctl)
    plist: Path = restart_job_path(dot_home())
    await install_restart_job(plist, b"<plist>waiting</plist>\n", UID)
    before: list[str] = _calls(launchctl)
    assert await dispatch(ServiceRestartCommand(name="service-restart", when_idle=True)) == 0
    assert "on its way already" in capsys.readouterr().out
    assert not any(
        call.startswith("bootout") or call.startswith("bootstrap") for call in _calls(launchctl)[len(before) :]
    )
    assert _loaded(launchctl, RESTART_LABEL) == b"<plist>waiting</plist>\n"


def test_status_names_a_restart_whose_program_ended_without_restarting() -> None:
    line: str = _restart_line(JobState(state="not running", pid=None, runs=1, last_exit="2"))
    assert "has ended without restarting" in line and "asks for a new one" in line


async def test_a_restart_left_loaded_without_a_process_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launchctl: Path
) -> None:
    """A job whose program failed before it unloaded it waits for nothing: the next ask loads a new one."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    ensure_layout()
    _load_the_daemons_job(launchctl)
    plist: Path = restart_job_path(dot_home())
    await install_restart_job(plist, b"<plist>dead</plist>\n", UID)

    async def ended(uid: int) -> JobState:
        return JobState(state="not running", pid=None, runs=1, last_exit="2")

    monkeypatch.setattr(cli_daemon, "inspect_restart_job", ended)
    assert await dispatch(ServiceRestartCommand(name="service-restart", when_idle=True)) == 0
    assert _loaded(launchctl, RESTART_LABEL) not in (None, b"<plist>dead</plist>\n")
