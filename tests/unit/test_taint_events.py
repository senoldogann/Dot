"""Whether a run was tainted after its events (item 243): the newest of its ``run_queued``, ``tool_result`` and
``steer`` events says so, a steer of the owner clearing the taint and one that vouches for nothing keeping it (item
482); an event stored before events held it counts as untainted."""

import pytest

from dotagent.agent.gate import tainted_after
from dotagent.core.types import JsonObject
from dotagent.store.models import EventRecord


def _event(number: int, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=number, run_id="run-1", ts=float(number), kind=kind, data=data)


@pytest.mark.parametrize(
    ("events", "tainted"),
    [
        ([], False),
        ([_event(1, "run_queued", {"tainted": True})], True),
        ([_event(1, "run_queued", {"tainted": False}), _event(4, "tool_result", {"tainted": True})], True),
        ([_event(4, "tool_result", {"tainted": True}), _event(9, "steer", {"text": "Go on."})], False),
        ([_event(12, "tool_result", {"tainted": True}), _event(9, "steer", {"text": "Go on."})], True),
        (
            [
                _event(1, "run_queued", {"tainted": True}),
                _event(5, "steer", {}),
                _event(8, "tool_result", {"tainted": False}),
            ],
            False,
        ),
        ([_event(1, "run_queued", {}), _event(4, "tool_result", {"ok": True})], False),
        # Dot's words that the owner did a step kept for them vouch for nothing: the run is as it was (item 482).
        ([_event(4, "tool_result", {"tainted": True}), _event(9, "steer", {"vouches": False, "tainted": True})], True),
        (
            [_event(4, "tool_result", {"tainted": False}), _event(9, "steer", {"vouches": False, "tainted": False})],
            False,
        ),
    ],
)
def test_the_newest_event_says_whether_the_run_was_tainted(events: list[EventRecord], tainted: bool) -> None:
    assert tainted_after(events) is tainted
