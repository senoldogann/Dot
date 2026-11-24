"""How a stored run is told to have ended: the texts the harness writes, and the ones it does not."""

from typing import get_args

import pytest

from dotagent.store.endings import (
    ENDING_LABELS,
    GOAL_CHECK_MARK,
    GOAL_CHECK_UNAVAILABLE,
    PREFIX_CUT_SHORT,
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
        ("failed", f"{PREFIX_CUT_SHORT} and the run did not finish (the last one ended with 'length').", "cut_short"),
        ("failed", RESULT_RESTARTED, "daemon_restarted"),
        ("failed", f"{PREFIX_INTERNAL_ERROR}KeyError: 'x'", "internal_error"),
        ("failed", f"the answer{GOAL_CHECK_MARK}the goal is still not reached", "goal_check_refused"),
        # A refusal that happens to say a step "could not run" is the check's own word, not its absence (item 1402).
        (
            "failed",
            f"the answer{GOAL_CHECK_MARK}the step could not run, so the goal is not reached",
            "goal_check_refused",
        ),
        (
            "failed",
            f"the answer{GOAL_CHECK_MARK}{GOAL_CHECK_UNAVAILABLE} (HTTP 429); this answer is not verified.",
            "goal_check_unavailable",
        ),
        ("cancelled", RESULT_STOPPED, "stopped"),
        ("cancelled", RESULT_SHUTDOWN, "daemon_shutdown"),
        ("cancelled", f"{PREFIX_EMERGENCY_STOP}the desktop was taken over", "emergency_stop"),
        ("cancelled", "", "stopped"),
        ("running", "", "active"),
        ("queued", "", "active"),
        ("blocked", "", "active"),
        ("ending", "", "active"),
    ],
)
def test_a_run_is_classified_by_its_status_and_the_text_it_was_closed_with(
    status: RunStatus, result: str, ending: Ending
) -> None:
    assert classify_ending(status, result) == ending


def test_every_ending_has_its_words() -> None:
    assert set(get_args(Ending.__value__)) == set(ENDING_LABELS)
    assert ENDING_LABELS["goal_check_refused"] == "refused by the goal check"
    assert "could not run" in ENDING_LABELS["goal_check_unavailable"]


def test_a_run_stored_as_refused_before_the_check_that_could_not_run_had_an_ending_of_its_own_is_read_again() -> None:
    """Item 1402: until then both were stored as ``goal_check_refused``, so five runs of the owner's were never judged
    and said they were refused. The note under the mark says which; a stored refusal that is one stays one, and so does
    every other stored ending."""
    unavailable: str = (
        f"the work{GOAL_CHECK_MARK}{GOAL_CHECK_UNAVAILABLE} (missing API key); this answer is not verified."
    )
    refused: str = f"the work{GOAL_CHECK_MARK}the goal is not reached."
    assert classify_ending("failed", unavailable, "goal_check_refused") == "goal_check_unavailable"
    assert classify_ending("failed", unavailable, "goal_check_unavailable") == "goal_check_unavailable"
    assert classify_ending("failed", refused, "goal_check_refused") == "goal_check_refused"
    assert classify_ending("failed", "no mark at all", "goal_check_refused") == "goal_check_refused"
    assert classify_ending("failed", unavailable, "failed") == "failed"
