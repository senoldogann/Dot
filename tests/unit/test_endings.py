"""How a stored run is told to have ended: the texts the harness writes, and the ones it does not."""

import pytest

from dotagent.store.endings import (
    GOAL_CHECK_MARK,
    PREFIX_EMERGENCY_STOP,
    PREFIX_INTERNAL_ERROR,
    PREFIX_LIMIT,
    RESULT_EMPTY_REPLIES,
    RESULT_RESTARTED,
    RESULT_SHUTDOWN,
    RESULT_STALLED,
    RESULT_STOPPED,
    Ending,
    classify_ending,
)
from dotagent.store.models import RunStatus


@pytest.mark.parametrize(
    ("status", "result", "ending"),
    [
        ("done", "all files are written", "finished"),
        ("done", RESULT_STOPPED, "finished"),
        ("failed", "the file does not exist, so nothing was written", "failed"),
        ("failed", RESULT_STALLED, "stalled"),
        ("failed", f"{PREFIX_LIMIT} (61.2 of 60 minutes) and did not finish when it was told to.", "limit"),
        ("failed", RESULT_EMPTY_REPLIES, "empty_replies"),
        ("failed", RESULT_RESTARTED, "daemon_restarted"),
        ("failed", f"{PREFIX_INTERNAL_ERROR}KeyError: 'x'", "internal_error"),
        ("failed", f"the answer{GOAL_CHECK_MARK}the goal is still not reached", "goal_check_refused"),
        ("cancelled", RESULT_STOPPED, "stopped"),
        ("cancelled", RESULT_SHUTDOWN, "daemon_shutdown"),
        ("cancelled", f"{PREFIX_EMERGENCY_STOP}the desktop was taken over", "emergency_stop"),
        ("cancelled", "", "stopped"),
        ("running", "", "active"),
        ("queued", "", "active"),
        ("blocked", "", "active"),
    ],
)
def test_a_run_is_classified_by_its_status_and_the_text_it_was_closed_with(
    status: RunStatus, result: str, ending: Ending
) -> None:
    assert classify_ending(status, result) == ending
