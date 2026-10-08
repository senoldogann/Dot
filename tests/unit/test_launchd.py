"""The launchd job of the daemon: the property list it is made of and the way its state is read."""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent.core.errors import ServiceError
from dotagent.launchd import (
    LABEL,
    SERVICE_PATH,
    JobState,
    LaunchAgent,
    build_launch_agent,
    launch_agent_path,
    parse_job,
    render_launch_agent,
    service_target,
)

PLUTIL: str = "/usr/bin/plutil"

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
    agent: LaunchAgent = build_launch_agent("/opt/dot/bin/python", Path("/h/Dot"), Path("/h/Logs"), {})
    parsed: dict[str, object] = plistlib.loads(render_launch_agent(agent))
    assert parsed["Label"] == LABEL
    assert parsed["ProgramArguments"] == ["/opt/dot/bin/python", "-m", "dotagent", "daemon"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["LimitLoadToSessionType"] == "Aqua"
    assert parsed["WorkingDirectory"] == "/h/Dot"
    assert parsed["StandardOutPath"] == "/h/Logs/daemon.out.log"
    assert parsed["StandardErrorPath"] == "/h/Logs/daemon.err.log"
    assert parsed["EnvironmentVariables"] == {"PATH": SERVICE_PATH}


def test_the_home_and_the_log_directory_are_handed_on_only_when_the_environment_sets_them() -> None:
    plain: LaunchAgent = build_launch_agent("/p", Path("/h"), Path("/l"), {"HOME": "/Users/someone"})
    relocated: LaunchAgent = build_launch_agent("/p", Path("/h"), Path("/l"), {"DOT_HOME": "/x", "DOT_LOG_DIR": "/y"})
    assert set(plain.environment) == {"PATH"}
    assert (relocated.environment["DOT_HOME"], relocated.environment["DOT_LOG_DIR"]) == ("/h", "/l")


def test_the_names_launchd_knows_the_job_by() -> None:
    assert launch_agent_path(Path("/Users/someone")) == Path(f"/Users/someone/Library/LaunchAgents/{LABEL}.plist")
    assert service_target(501) == f"gui/501/{LABEL}"


def test_macos_accepts_the_property_list(tmp_path: Path) -> None:
    plist: Path = tmp_path / f"{LABEL}.plist"
    plist.write_bytes(render_launch_agent(build_launch_agent(sys.executable, tmp_path, tmp_path / "logs", {})))
    linted: subprocess.CompletedProcess[str] = subprocess.run(
        [PLUTIL, "-lint", str(plist)], capture_output=True, text=True, check=False
    )
    assert linted.returncode == 0, linted.stdout + linted.stderr
