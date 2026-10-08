"""How a run ended, told from the status and the result text it was closed with.

The texts below are what the run manager, the agent loop and the store write when a run ends for a reason of the
harness; :func:`classify_ending` is their reader. Anything else a failed run holds is the agent's own report of failure
or the description of an error.
"""

from typing import Literal

from dotagent.store.models import RunStatus

RESULT_STOPPED: str = "The run was stopped."
RESULT_SHUTDOWN: str = "The daemon was shut down while this run was active."
RESULT_RESTARTED: str = "daemon stopped while the run was active"
RESULT_STALLED: str = "Stopped: the agent kept repeating the same failing actions."
RESULT_EMPTY_REPLIES: str = "The model returned empty replies and did not finish."
PREFIX_EMERGENCY_STOP: str = "Emergency stop: "
PREFIX_INTERNAL_ERROR: str = "Internal error: "
# Between the answer of a run and what the goal check made of it, when the check does not accept the end.
GOAL_CHECK_MARK: str = "\n\n---\nGoal check: "

type Ending = Literal[
    "active",
    "finished",
    "failed",
    "goal_check_refused",
    "stalled",
    "empty_replies",
    "internal_error",
    "daemon_restarted",
    "stopped",
    "daemon_shutdown",
    "emergency_stop",
]

# What each ending is called to a person.
ENDING_LABELS: dict[Ending, str] = {
    "active": "still active",
    "finished": "finished",
    "failed": "failed (the agent's own report, or an error of the model service)",
    "goal_check_refused": "refused by the goal check",
    "stalled": "stopped by the stall guard",
    "empty_replies": "empty replies from the model",
    "internal_error": "internal error",
    "daemon_restarted": "cut off when the daemon died",
    "stopped": "stopped (by the user; older runs may be restarts, which were not told apart)",
    "daemon_shutdown": "cut off when the daemon shut down",
    "emergency_stop": "emergency stop",
}


def _failed_ending(result: str) -> Ending:
    if result == RESULT_STALLED:
        return "stalled"
    if result == RESULT_EMPTY_REPLIES:
        return "empty_replies"
    if result == RESULT_RESTARTED:
        return "daemon_restarted"
    if result.startswith(PREFIX_INTERNAL_ERROR):
        return "internal_error"
    if GOAL_CHECK_MARK in result:
        return "goal_check_refused"
    return "failed"


def _cancelled_ending(result: str) -> Ending:
    if result == RESULT_SHUTDOWN:
        return "daemon_shutdown"
    if result.startswith(PREFIX_EMERGENCY_STOP):
        return "emergency_stop"
    return "stopped"


def classify_ending(status: RunStatus, result: str) -> Ending:
    """The reason a run ended, from its stored status and result text."""
    match status:
        case "done":
            return "finished"
        case "failed":
            return _failed_ending(result)
        case "cancelled":
            return _cancelled_ending(result)
        case "queued" | "running" | "blocked":
            return "active"
