"""A helper's final check ends a reached goal as done, ``improve`` too (item 1946, the pre-push review of its merge with
main): the check cannot send the helper back, and a better result in reach is no failure."""

from dotagent.agent.helper_check import checked_end
from dotagent.agent.loop import RunOutcome
from dotagent.store.endings import GOAL_CHECK_MARK

_DONE: RunOutcome = RunOutcome(status="done", result="The cheapest flight is 120 EUR.", needs="")


def test_a_reached_goal_stands_and_one_not_reached_fails_with_the_answer_kept() -> None:
    assert checked_end("achieved", "It is there.", _DONE) == _DONE
    assert checked_end("improve", "A later flight is 10 EUR cheaper.", _DONE) == _DONE
    failed: RunOutcome = checked_end("continue", "No price was read.", _DONE)
    assert (failed.status, failed.result) == ("failed", f"{_DONE.result}{GOAL_CHECK_MARK}No price was read.")
