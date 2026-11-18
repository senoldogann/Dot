"""The desktop fixture app (``tests/fixtures/dot_fixture_app.py``) for the tests that drive it with a live model: start
it, wait until its window is up, and read the events it saw, so that a check is what the application saw and not what
the model says about it. Also how long this Mac has had no input, so that a test that moves the real pointer and
keyboard starts only while nobody is at the Mac (item 1221)."""

import json
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

FIXTURE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_fixture_app.py"
READY_TIMEOUT_S: float = 15.0
_IDLE: re.Pattern[str] = re.compile(r'"HIDIdleTime" = (\d+)')


class FixtureEvent(BaseModel):
    """One line of the fixture's event log; only the fields the checks read."""

    model_config = ConfigDict(extra="ignore")

    event: str
    field: str | None = None
    value: str | None = None
    count: int | None = None
    on: bool | None = None
    pid: int | None = None


@dataclass(frozen=True)
class FixtureApp:
    pid: int
    log: Path
    process: subprocess.Popen[bytes]


def events(log: Path) -> list[FixtureEvent]:
    # The app may be in the middle of writing its last line; only lines that end in a newline are complete.
    complete: list[str] = log.read_text(encoding="utf-8").split("\n")[:-1]
    return [FixtureEvent.model_validate(json.loads(line)) for line in complete if line.strip()]


def _wait_until_ready(process: subprocess.Popen[bytes], log: Path) -> int:
    deadline: float = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        assert process.poll() is None, f"the fixture app exited with code {process.returncode}"
        if log.exists():
            for event in events(log):
                if event.event == "ready" and event.pid is not None:
                    return event.pid
        time.sleep(0.1)
    raise AssertionError("the fixture app did not become ready")


@contextmanager
def fixture_app(log: Path) -> Iterator[FixtureApp]:
    """The fixture app, writing its events to ``log``, for the length of the block."""
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, str(FIXTURE), str(log)])
    try:
        yield FixtureApp(pid=_wait_until_ready(process, log), log=log, process=process)
    finally:
        process.terminate()
        process.wait(timeout=10)


def idle_seconds(ioreg_output: str) -> float:
    """The seconds since this Mac's last keyboard, pointer or trackpad input, from ``ioreg -c IOHIDSystem``."""
    found: re.Match[str] | None = _IDLE.search(ioreg_output)
    if found is None:
        raise AssertionError("ioreg did not tell the time since the last input (HIDIdleTime)")
    return int(found.group(1)) / 1_000_000_000


def mac_idle_seconds() -> float:
    """How long nobody has touched this Mac's keyboard, pointer or trackpad."""
    said: subprocess.CompletedProcess[str] = subprocess.run(
        ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, check=True, timeout=10
    )
    return idle_seconds(said.stdout)
