"""The launchd job of the daemon: the property list it is made of, the way its state is read, and how a new job
takes the place of the one loaded (against a stand-in for ``launchctl``, so that the job of this Mac is left
alone)."""

import asyncio
import os
import plistlib
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from dotagent import launchd
from dotagent.core.errors import ServiceError
from dotagent.launchd import (
    LABEL,
    SERVICE_PATH,
    TOOLS_ENV,
    JobState,
    LaunchAgent,
    agent_plist,
    build_launch_agent,
    install_job,
    install_restart_job,
    launch_agent_path,
    parse_job,
    render_launch_agent,
    restart_job_path,
    service_path,
    service_runs,
    service_target,
)

PLUTIL: str = "/usr/bin/plutil"
LAUNCHER: str = "/h/Dot/Dot.app/Contents/MacOS/Dot"

# The layout of ``launchctl print gui/<uid>/<label>`` on macOS 26: the properties of the job stand one tab deep, those
# of what it contains stand deeper (and may carry a ``state`` or a ``pid`` of their own).
RUNNING: str = (
    "gui/501/dev.example.job = {\n"
    "\tactive count = 1\n"
    "\tpath = /Users/someone/Library/LaunchAgents/dev.example.job.plist\n"
    "\ttype = LaunchAgent\n"
    "\tstate = running\n"
    "\n"
    "\tprogram = /usr/bin/python3\n"
    "\tchecked allocations = 0 (queried = 1)\n"
    "\truns = 3\n"
    "\tpid = 616\n"
    "\tlast exit code = (never exited)\n"
    "\n"
    "\tendpoints = {\n"
    '\t\t"dev.example.port" = {\n'
    "\t\t\tstate = inactive\n"
    "\t\t\tpid = 7\n"
    "\t\t}\n"
    "\t}\n"
    "}\n"
)

WAITING_AFTER_A_CRASH: str = (
    "gui/501/dev.example.job = {\n\tstate = not running\n\truns = 2\n\tlast exit code = 1\n\n"
    "\tprogram = /usr/bin/python3\n}\n"
)


def test_a_running_job_is_read_from_the_properties_of_the_job_itself() -> None:
    assert parse_job(RUNNING) == JobState(state="running", pid=616, runs=3, last_exit="(never exited)")


def test_a_job_that_waits_for_its_next_start_has_no_pid() -> None:
    assert parse_job(WAITING_AFTER_A_CRASH) == JobState(state="not running", pid=None, runs=2, last_exit="1")


def test_text_that_does_not_describe_a_job_is_an_error() -> None:
    with pytest.raises(ServiceError) as raised:
        parse_job('Could not find service "dev.example.job" in domain for user gui: 501')
    assert "output" in raised.value.context


def test_the_property_list_starts_the_daemon_at_login_and_after_a_crash_only() -> None:
    agent: LaunchAgent = build_launch_agent(LAUNCHER, "/opt/dot/bin/python", Path("/h/Dot"), Path("/h/Logs"), {})
    parsed: dict[str, object] = plistlib.loads(render_launch_agent(agent))
    assert parsed["Label"] == LABEL
    # Dot.app's program starts the daemon's Python (item 288).
    assert parsed["ProgramArguments"] == [LAUNCHER, "/opt/dot/bin/python", "-P", "-m", "dotagent", "daemon"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["LimitLoadToSessionType"] == "Aqua"
    assert parsed["WorkingDirectory"] == "/h/Dot"
    assert parsed["StandardOutPath"] == "/h/Logs/daemon.out.log"
    assert parsed["StandardErrorPath"] == "/h/Logs/daemon.err.log"
    assert parsed["EnvironmentVariables"] == {"PATH": SERVICE_PATH}


def test_the_home_and_the_log_directory_are_handed_on_only_when_the_environment_sets_them() -> None:
    plain: LaunchAgent = build_launch_agent(LAUNCHER, "/p", Path("/h"), Path("/l"), {"HOME": "/Users/someone"})
    relocated: LaunchAgent = build_launch_agent(
        LAUNCHER, "/p", Path("/h"), Path("/l"), {"DOT_HOME": "/x", "DOT_LOG_DIR": "/y"}
    )
    assert set(plain.environment) == {"PATH"}
    assert (relocated.environment["DOT_HOME"], relocated.environment["DOT_LOG_DIR"]) == ("/h", "/l")


def test_the_tools_of_the_desktop_app_come_before_the_standard_path_in_the_jobs_path() -> None:
    """Item 575: the app carries ripgrep and uv; the job's PATH names their folder first, so that Dot's search finds
    ripgrep on a Mac with no Homebrew, and an environment that does not name one leaves the path as it was."""
    tools: str = "/h/Dot/runtime/app-tools"
    agent: LaunchAgent = build_launch_agent(LAUNCHER, "/p", Path("/h"), Path("/l"), {TOOLS_ENV: tools})
    assert agent.environment == {"PATH": f"{tools}:{SERVICE_PATH}"}
    assert plistlib.loads(render_launch_agent(agent))["EnvironmentVariables"] == {"PATH": f"{tools}:{SERVICE_PATH}"}
    assert service_path({}) == service_path({TOOLS_ENV: ""}) == SERVICE_PATH


def test_the_jobs_path_has_the_tools_folder_of_dots_home_when_no_variable_names_one(tmp_path: Path) -> None:
    """Item 575's review: only the desktop app's own steps set ``DOT_APP_TOOLS``. A ``dotx service install`` typed in a
    terminal on an install of the app, as the README tells, found the job without ripgrep on its path: the folder the
    app keeps in Dot's home is used when it is there, and the variable, when set, still wins."""
    home: Path = tmp_path / "Dot"
    tools: Path = home / "runtime" / "app-tools"
    assert service_path({}, home) == SERVICE_PATH
    tools.mkdir(parents=True)
    assert service_path({}, home) == f"{tools}:{SERVICE_PATH}"
    assert service_path({TOOLS_ENV: ""}, home) == f"{tools}:{SERVICE_PATH}"
    agent: LaunchAgent = build_launch_agent(LAUNCHER, "/p", home, tmp_path / "logs", {})
    assert agent.environment == {"PATH": f"{tools}:{SERVICE_PATH}"}
    named: str = "/named/by/the/app/app-tools"
    assert service_path({TOOLS_ENV: named}, home) == f"{named}:{SERVICE_PATH}"
    assert service_path({}, tmp_path / "another-home") == SERVICE_PATH


def test_a_tools_folder_of_dots_home_that_cannot_be_one_entry_of_a_path_is_left_out(tmp_path: Path) -> None:
    """A colon would make it two folders; the job's path is not made wrong by a home that has one in its name."""
    home: Path = tmp_path / "a:b"
    (home / "runtime" / "app-tools").mkdir(parents=True)
    assert service_path({}, home) == SERVICE_PATH


@pytest.mark.parametrize("named", ["tools", "./tools", "/one:/two", "/a\n/b"])
def test_a_tools_folder_that_is_not_one_full_path_is_refused(named: str) -> None:
    """A relative path would follow the daemon's working directory; a colon would add a second folder to the PATH."""
    with pytest.raises(ServiceError) as raised:
        service_path({TOOLS_ENV: named})
    assert TOOLS_ENV in raised.value.message


def test_the_names_launchd_knows_the_job_by() -> None:
    assert launch_agent_path(Path("/Users/someone")) == Path(f"/Users/someone/Library/LaunchAgents/{LABEL}.plist")
    assert service_target(501) == f"gui/501/{LABEL}"


def test_the_job_runs_the_dot_that_is_installed_not_a_package_left_in_its_working_directory(tmp_path: Path) -> None:
    """Item 146: the job's working directory is Dot's home, where a run's commands may make files; a package named
    ``dotagent`` left there is not what the job's Python imports."""
    planted: Path = tmp_path / "dotagent"
    planted.mkdir()
    (planted / "__init__.py").write_text("", encoding="utf-8")
    (planted / "__main__.py").write_text("print('planted')\n", encoding="utf-8")
    words: list[str] = agent_plist(build_launch_agent(LAUNCHER, sys.executable, tmp_path, tmp_path / "logs", {}))[
        "ProgramArguments"
    ]
    # The words Dot.app's program starts the daemon with, with the help of the command line in place of the daemon.
    started: subprocess.CompletedProcess[str] = subprocess.run(
        [*words[1:-1], "--help"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert started.returncode == 0, started.stderr
    assert "planted" not in started.stdout
    assert "daemon" in started.stdout


def test_macos_accepts_the_property_list(tmp_path: Path) -> None:
    plist: Path = tmp_path / f"{LABEL}.plist"
    plist.write_bytes(
        render_launch_agent(build_launch_agent(LAUNCHER, sys.executable, tmp_path, tmp_path / "logs", {}))
    )
    linted: subprocess.CompletedProcess[str] = subprocess.run(
        [PLUTIL, "-lint", str(plist)], capture_output=True, text=True, check=False
    )
    assert linted.returncode == 0, linted.stdout + linted.stderr


# The property lists of the jobs the stand-in launchctl loads: launchd refuses one that says ``refused-by-launchd``.
JOB_OF_BEFORE: bytes = b"<plist>the job of before</plist>\n"
NEXT_JOB: bytes = b"<plist>the next job</plist>\n"
REFUSED_JOB: bytes = b"<plist>refused-by-launchd</plist>\n"
UID: int = 501


def _job_and_file(plist: Path, state: Path) -> tuple[bytes | None, bytes | None]:
    """The property list the job the stand-in launchctl keeps was loaded from, and the one in the job's file; ``None``
    for none."""
    loaded: Path = state / "loaded"
    return (loaded.read_bytes() if loaded.exists() else None, plist.read_bytes() if plist.exists() else None)


@pytest.fixture
def launchctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for launchctl, which the launchd module runs in its place: the job is loaded while ``loaded`` is in
    the returned directory, holding the property list it was loaded from; launchd refuses a list that says
    ``refused-by-launchd`` and cannot unload the job while ``stuck`` is there."""
    state: Path = tmp_path / "launchd"
    state.mkdir()
    (state / "printed").write_text(WAITING_AFTER_A_CRASH, encoding="utf-8")
    script: Path = state / "launchctl"
    script.write_text(
        "#!/bin/sh\n"
        f"cd '{state}' || exit 70\n"
        'case "$1" in\n'
        "print) [ -f loaded ] || exit 113; cat printed ;;\n"
        "bootout) [ -f stuck ] && { echo 'Boot-out failed: 5: Input/output error' >&2; exit 5; }; rm -f loaded ;;\n"
        "bootstrap) grep -q refused-by-launchd \"$3\" && { echo 'Bootstrap failed: 5: Input/output error' >&2; exit "
        "5; }; "
        'cp "$3" loaded ;;\n'
        "*) exit 64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(launchd, "_LAUNCHCTL", str(script))
    return state


async def test_a_new_job_launchd_refuses_leaves_the_job_of_before_and_its_file(tmp_path: Path, launchctl: Path) -> None:
    """Item 187: when the new job does not load, the property list of before is put back and its job loaded again;
    without one before, the refused list is removed."""
    plist: Path = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    with pytest.raises(ServiceError, match="launchctl cannot load the job"):
        await install_job(plist, REFUSED_JOB, UID)
    assert _job_and_file(plist, launchctl) == (None, None)

    assert await install_job(plist, JOB_OF_BEFORE, UID) is False
    with pytest.raises(ServiceError, match="launchctl cannot load the job"):
        await install_job(plist, REFUSED_JOB, UID)
    assert _job_and_file(plist, launchctl) == (JOB_OF_BEFORE, JOB_OF_BEFORE)

    assert await install_job(plist, NEXT_JOB, UID) is True
    assert _job_and_file(plist, launchctl) == (NEXT_JOB, NEXT_JOB)


async def test_a_job_launchd_cannot_unload_keeps_its_property_list(tmp_path: Path, launchctl: Path) -> None:
    """Item 187: the new property list is written only once the job of before is unloaded, so a job that cannot be
    unloaded keeps the list it was loaded from."""
    plist: Path = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    await install_job(plist, JOB_OF_BEFORE, UID)
    (launchctl / "stuck").touch()
    with pytest.raises(ServiceError, match="launchctl cannot unload the job that is loaded"):
        await install_job(plist, NEXT_JOB, UID)
    assert _job_and_file(plist, launchctl) == (JOB_OF_BEFORE, JOB_OF_BEFORE)


# ----------------------------------------------------- the property lists are written where a command may leave a link
#
# Found by the re-check of item 575, whose upgrade runs ``dotx service restart --when-idle`` at every update (item 441's
# restart job): its property list is in Dot's home, where the sandbox lets a command of a run make links and FIFOs.

SECRET: str = "OPENAI_API_KEY=sk-secret-0123456789\n"


def test_a_link_left_at_the_staging_name_is_not_written_through(tmp_path: Path, launchctl: Path) -> None:
    """The staging name was a fixed one, and a link left at it was written through: the file it led to took the new
    list's text and mode. The new list is staged in a file of a name that is not there."""
    home: Path = tmp_path / "home"
    home.mkdir()
    victim: Path = tmp_path / "victim.txt"
    victim.write_text("precious\n", encoding="utf-8")
    victim.chmod(0o600)
    plist: Path = restart_job_path(home)
    old_staging: Path = plist.with_name(f".{plist.name}.tmp")
    old_staging.symlink_to(victim)
    assert asyncio.run(install_restart_job(plist, NEXT_JOB, UID)) is False
    assert victim.read_text(encoding="utf-8") == "precious\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    assert not plist.is_symlink() and plist.read_bytes() == NEXT_JOB
    assert stat.S_IMODE(plist.stat().st_mode) == 0o644
    # The planted link was never used, and nothing is left beside the list.
    assert old_staging.is_symlink() and old_staging.resolve() == victim.resolve()
    assert sorted(entry.name for entry in home.iterdir()) == sorted([plist.name, old_staging.name])


def test_a_link_where_the_property_list_belongs_is_neither_read_nor_written_through(
    tmp_path: Path, launchctl: Path
) -> None:
    """The old list was read through a link at its name, and put back as a plain file of Dot's home (mode 0644) when
    the new job did not load: a file the sandbox seals, which the link led to, was copied out of its seal."""
    home: Path = tmp_path / "home"
    home.mkdir()
    secret: Path = tmp_path / "sealed.env"
    secret.write_text(SECRET, encoding="utf-8")
    secret.chmod(0o600)
    plist: Path = restart_job_path(home)
    plist.symlink_to(secret)
    with pytest.raises(ServiceError, match="launchctl cannot load the job"):
        asyncio.run(install_restart_job(plist, REFUSED_JOB, UID))
    # The refused list is removed, as it is where there was none before; nothing of the secret is in a plain file.
    assert not plist.exists() and not plist.is_symlink()
    assert secret.read_text(encoding="utf-8") == SECRET and stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert not [entry for entry in home.iterdir() if "sk-secret" in entry.read_text(encoding="utf-8", errors="replace")]
    # A list that loads takes the link's place, and the link's target is as it was.
    assert asyncio.run(install_restart_job(plist, NEXT_JOB, UID)) is False
    assert not plist.is_symlink() and plist.read_bytes() == NEXT_JOB
    assert secret.read_text(encoding="utf-8") == SECRET


def test_a_fifo_where_the_property_list_belongs_is_not_waited_on(tmp_path: Path) -> None:
    """A FIFO there would hold the read of the old list for ever. The read runs on a thread of its own with a time to
    end in, so that a read that waits fails the test and does not hold the run."""
    fifo: Path = tmp_path / f"{LABEL}.plist"
    os.mkfifo(fifo)
    outcome: list[bytes | None] = []
    reader: threading.Thread = threading.Thread(target=lambda: outcome.append(launchd._read_file(fifo)), daemon=True)
    reader.start()
    reader.join(15)
    assert not reader.is_alive(), "reading the property list waited for a FIFO"
    assert outcome == [None]


def test_the_old_property_list_is_read_when_it_is_a_plain_file_of_a_size_a_list_has(tmp_path: Path) -> None:
    plain: Path = tmp_path / "plain.plist"
    plain.write_bytes(NEXT_JOB)
    assert launchd._read_file(plain) == NEXT_JOB
    assert launchd._read_file(tmp_path / "absent.plist") is None
    limit: int = launchd._PLIST_READ_LIMIT
    at_the_limit: Path = tmp_path / "at-the-limit.plist"
    at_the_limit.write_bytes(b"x" * limit)
    assert launchd._read_file(at_the_limit) == b"x" * limit
    too_long: Path = tmp_path / "too-long.plist"
    too_long.write_bytes(b"x" * (limit + 1))
    assert launchd._read_file(too_long) is None
    folder: Path = tmp_path / "folder.plist"
    folder.mkdir()
    assert launchd._read_file(folder) is None


def test_the_property_list_gets_the_mode_launchd_wants_whatever_the_umask_and_no_staging_file_is_left(
    tmp_path: Path,
) -> None:
    plist: Path = tmp_path / f"{LABEL}.plist"
    previous_umask: int = os.umask(0o077)
    try:
        launchd._write_plist(plist, NEXT_JOB)
    finally:
        os.umask(previous_umask)
    assert plist.read_bytes() == NEXT_JOB and stat.S_IMODE(plist.stat().st_mode) == 0o644
    assert [entry.name for entry in tmp_path.iterdir()] == [plist.name]
    # A write that cannot end (a folder where the list belongs) leaves no staging file behind and says so.
    blocked: Path = tmp_path / "blocked.plist"
    blocked.mkdir()
    (blocked / "held").write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        launchd._write_plist(blocked, NEXT_JOB)
    assert [entry.name for entry in tmp_path.iterdir() if entry.name.endswith(".tmp")] == []


def test_the_service_runs_the_daemon_when_launchd_s_pid_is_its_own_or_its_launcher_s() -> None:
    """Item 409: the job runs Dot.app's launcher, the daemon's parent; a job of another pid, or none, is not it."""
    job: JobState = JobState(state="running", pid=4242, runs=1, last_exit=None)
    assert service_runs(job, (9000, 4242)) is True
    assert service_runs(job, (4242, 1)) is True
    assert service_runs(job, (9000, 9001)) is False
    assert service_runs(JobState(state="waiting", pid=None, runs=0, last_exit="75"), (9000, 4242)) is False
    assert service_runs(None, (4242,)) is False
