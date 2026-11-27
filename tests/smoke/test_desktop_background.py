"""The background driver on this Mac (item 2007): the real cua-driver drives the desktop fixture's window while
another fixture window is in front and the owner counts as at the Mac, against the same steps with the setting off.

Run by hand, with cua-driver 0.28.2 installed and ``DOT_DESKTOP_TESTS=1``; ``-s`` prints what was measured: per step,
whether it went, its seconds, and the application in front and the pointer before and after. Nothing moves the Mac's
own pointer or keyboard: off, the steps that would take them wait for the owner (item 1890); on, they go through the
driver. The two fixture windows are Dot's own (``tests/fixtures/dot_fixture_app.py``), no application of the owner's.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from dotagent.config import DesktopConfig, load_default_config
from dotagent.desktop import apps, cua, screen
from dotagent.desktop.actions import refuse_secure_field
from dotagent.desktop.presence import OwnerPresence
from dotagent.desktop.session import DesktopSession, StepReview, no_browser_chrome
from dotagent.desktop.types import ActOutcome, Point, SnapshotView, Step
from dotagent.desktop.views import ViewRequest

pytestmark = [
    pytest.mark.live,
    pytest.mark.desktop,
    pytest.mark.skipif(
        os.environ.get("DOT_DESKTOP_TESTS") != "1", reason="set DOT_DESKTOP_TESTS=1 to drive the fixture windows"
    ),
    pytest.mark.skipif(not Path(cua.DEFAULT_PATH).is_file(), reason=f"cua-driver is not at {cua.DEFAULT_PATH}"),
]

_FIXTURE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_fixture_app.py"
_MAX_CHARS: int = load_default_config().desktop.snapshot_max_chars
_VIEW: ViewRequest = ViewRequest(scope="window", ref=None, query=None, full=True, max_chars=_MAX_CHARS)
# A loaded Mac takes long to start an AppKit window.
_READY_S: float = 60.0


@dataclass(frozen=True)
class _Fixture:
    pid: int
    log: Path

    def events(self, kind: str) -> list[dict[str, object]]:
        lines: list[str] = self.log.read_text(encoding="utf-8").splitlines()
        return [event for event in map(json.loads, lines) if event.get("event") == kind]


def _launch(log: Path) -> tuple[subprocess.Popen[bytes], _Fixture]:
    process: subprocess.Popen[bytes] = subprocess.Popen([sys.executable, str(_FIXTURE), str(log)])
    deadline: float = time.monotonic() + _READY_S
    while time.monotonic() < deadline:
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                event: dict[str, object] = json.loads(line)
                if event.get("event") == "ready" and isinstance(event.get("pid"), int):
                    return process, _Fixture(pid=int(str(event["pid"])), log=log)
        time.sleep(0.1)
    process.terminate()
    raise AssertionError("the fixture app did not become ready")


@pytest.fixture(scope="module")
def windows(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[_Fixture, _Fixture]]:
    """The fixture the steps drive, then a second one, which takes the front as the owner's application would."""
    folder: Path = tmp_path_factory.mktemp("background")
    target_process, target = _launch(folder / "target.jsonl")
    try:
        time.sleep(0.5)
        front_process, front = _launch(folder / "front.jsonl")
        try:
            time.sleep(1.5)
            yield target, front
        finally:
            front_process.terminate()
            front_process.wait(timeout=10)
    finally:
        target_process.terminate()
        target_process.wait(timeout=10)


def _owner_here() -> OwnerPresence:
    """The owner touched the Mac half a second ago: a step that takes its pointer or keyboard waits."""
    return OwnerPresence(lambda: 0.5, time.monotonic, 600.0)


async def _allow(review: StepReview) -> None:
    return None


def _ref(body: str, label: str) -> str:
    for line in body.splitlines():
        if label in line and "[a" in line:
            return line[line.index("[a") + 1 : line.index("]", line.index("[a"))]
    raise AssertionError(f"no {label!r} in:\n{body}")


@dataclass(frozen=True)
class _Measured:
    step: str
    outcome: str
    seconds: float
    front_before: int | None
    front_after: int | None
    pointer_moved: bool


async def _measure(session: DesktopSession, target: _Fixture, steps: list[Step]) -> list[_Measured]:
    measured: list[_Measured] = []
    for step in steps:
        front: int | None = apps.frontmost_pid()
        pointer: Point = screen.pointer_location()
        began: float = time.monotonic()
        outcome: ActOutcome = await session.act(
            "background", str(target.pid), [step], _allow, refuse_secure_field, _MAX_CHARS
        )
        seconds: float = time.monotonic() - began
        measured.append(
            _Measured(
                step=f"{step.action} {step.ref or ''}{step.key or ''}{step.text or ''}".strip(),
                outcome=outcome.steps[0].outcome,
                seconds=round(seconds, 2),
                front_before=front,
                front_after=apps.frontmost_pid(),
                pointer_moved=screen.pointer_location() != pointer,
            )
        )
    return measured


async def _run(config: DesktopConfig, target: _Fixture, tmp_path: Path) -> list[_Measured]:
    session: DesktopSession = DesktopSession(
        config, tmp_path / "STOP", no_browser_chrome, None, _owner_here(), cua.starter_of(config)
    )
    try:
        view: SnapshotView = await session.snapshot("background", str(target.pid), _VIEW)
        steps: list[Step] = [
            Step(action="click", ref=_ref(view.body, '"Increment"')),
            Step(action="click", ref=_ref(view.body, 'textfield "Name"')),
            Step(action="type", ref=_ref(view.body, 'textfield "Name"'), text="cua"),
            Step(action="key", key="tab"),
            Step(action="scroll", ref=_ref(view.body, "scrollarea"), direction="down", amount=300),
        ]
        return await _measure(session, target, steps)
    finally:
        await session.close()


async def test_the_background_driver_drives_a_covered_window_while_the_owner_is_at_the_mac(
    windows: tuple[_Fixture, _Fixture], tmp_path: Path
) -> None:
    target, front = windows
    base: DesktopConfig = load_default_config().desktop.model_copy(update={"enabled": True, "show_pointer": False})
    off: list[_Measured] = await _run(base, target, tmp_path)
    texts_before: int = len(target.events("text"))
    wheels_before: int = len(target.events("wheel"))
    on: list[_Measured] = await _run(base.model_copy(update={"background_driver": "cua"}), target, tmp_path)
    for name, rows in (("off", off), ("on", on)):
        for row in rows:
            print(f"{name:3} {row}")
    # Off, the owner is at the Mac: the press goes, the steps that take the pointer or the keyboard wait (item 1890).
    assert [row.outcome for row in off] == ["done", "skipped", "skipped", "skipped", "skipped"]
    # On, every step goes, to the covered window, the front and the pointer left where they were.
    assert [row.outcome for row in on] == ["done"] * len(on), on
    assert all(row.front_before == row.front_after == front.pid for row in on), on
    assert not any(row.pointer_moved for row in on)
    typed: list[dict[str, object]] = target.events("text")[texts_before:]
    assert typed and str(typed[-1].get("value", "")).endswith("cua"), typed
    assert len(target.events("wheel")) > wheels_before
