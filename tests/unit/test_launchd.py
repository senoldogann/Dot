"""The launchd job of the daemon: the property list it is made of, the way its state is read, and how a new job
takes the place of the one loaded (against a stand-in for ``launchctl``, so that the job of this Mac is left
alone)."""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent import launchd
from dotagent.core.errors import ServiceError
from dotagent.launchd import (
    LABEL,
    SERVICE_PATH,
    JobState,
    LaunchAgent,
    agent_plist,
    build_launch_agent,
    install_job,
    launch_agent_path,
    parse_job,
    render_launch_agent,
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

WAITING_AFTER_A_CRASH: str = "gui/501/dev.example.job = {\n\tstate = not running\n\truns = 2\n\tlast exit code = 1\n\n\tprogram = /usr/bin/python3\n}\n"


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
        "bootstrap) grep -q refused-by-launchd \"$3\" && { echo 'Bootstrap failed: 5: Input/output error' >&2; exit 5; }; "
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
