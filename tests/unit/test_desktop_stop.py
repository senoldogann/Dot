"""The emergency stop ends what the desktop is doing (item 683): a long text typed key by key, a scroll and a drag look
at it between their events, so that what is left is not typed into whatever has the keyboard after the stop; and a
step whose pointer was moved by someone else since the previous one stops the batch, as docs/safety.md says. No event
reaches the Mac: the posting is a stand-in."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError, FailSafeTriggered
from dotagent.desktop import actions, screen
from dotagent.desktop import input as hid
from dotagent.desktop.actions import StepEnv, execute_step
from dotagent.desktop.types import Point, Step


def test_posting_stops_between_events_once_the_stop_is_engaged(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[object] = []
    monkeypatch.setattr(hid.Quartz, "CGEventPost", lambda tap, event: posted.append(event))
    monkeypatch.setattr(hid.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(hid, "presses", lambda event: 0)
    events: list[hid.TimedEvent] = [hid.TimedEvent(object(), 0.0) for _ in range(100)]
    checked: list[int] = []

    def check() -> None:
        checked.append(len(posted))
        if len(posted) >= 20:
            raise FailSafeTriggered("the pointer is in the top-left corner of the screen", {})

    with pytest.raises(FailSafeTriggered):
        hid.post(events, check)
    assert len(posted) == 20 and checked[0] == 0
    posted.clear()
    hid.post(events)
    assert len(posted) == 100


def test_a_step_stops_when_someone_else_moved_the_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen, "pointer_location", lambda: Point(x=600.0, y=400.0))
    monkeypatch.setattr(actions.screen, "pointer_location", lambda: Point(x=600.0, y=400.0))
    env: StepEnv = cast(
        StepEnv,
        SimpleNamespace(
            config=load_default_config().desktop,
            failsafe_file=tmp_path / "STOP",
            expected_pointer=Point(x=100.0, y=100.0),
        ),
    )
    with pytest.raises(DesktopError, match="no longer where the previous step left it"):
        execute_step(env, Step(action="type", text="hello"), cast(actions.Inspection, None))


def test_the_stop_never_comes_between_a_press_and_its_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """The code review of item 683: a drag stopped after its button went down left it down, and the owner's next move
    went on with the drag; the stop comes once what was pressed is let up."""
    posted: list[int] = []
    monkeypatch.setattr(hid.Quartz, "CGEventPost", lambda tap, event: posted.append(event))
    monkeypatch.setattr(hid.time, "sleep", lambda seconds: None)
    # A move, the button down, twelve drags and the button up, then a move: as hid.drag_events builds them.
    kinds: list[int] = [0, 1, *([0] * 12), -1, 0]
    monkeypatch.setattr(hid, "presses", lambda event: event)
    checked_at: list[int] = []

    def check() -> None:
        checked_at.append(len(posted))
        if len(posted) > 1:
            raise FailSafeTriggered("the pointer is in the top-left corner of the screen", {})

    with pytest.raises(FailSafeTriggered):
        hid.post([hid.TimedEvent(kind, 0.0) for kind in kinds], check)
    assert posted == kinds[:15] and checked_at == [0, 15]
