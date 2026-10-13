"""How a run ended, told from the status and the result text it was closed with.

The texts below are what the run manager, the agent loop and the store write when a run ends for a reason of the
harness; :func:`classify_ending` is their reader. Anything else a failed run holds is the agent's own report of failure
or the description of an error.
"""

from typing import Literal

from dotagent.store.models import RunStatus

RESULT_STOPPED: str = "The run was stopped."
RESULT_SHUTDOWN: str = "The daemon was shut down while this run was active."
# A run of ``dotx run`` (``resume``, ``chat``) whose terminal was closed, or whose process got SIGTERM.
RESULT_PROCESS_ENDED: str = "The dotx command that drove this run was told to end while the run was active."
# A run of ``dotx run`` whose process ended without recording the run's end (it was killed, or it crashed), as the
# daemon finds while it runs (:meth:`dotagent.agent.manager.RunManager.close_runs_of_dead_commands`).
RESULT_COMMAND_DIED: str = "The dotx command that drove this run ended without recording the run's end."
RESULT_RESTARTED: str = "daemon stopped while the run was active"
# A run whose start failed after it was stored, before its task was registered (:meth:`dotagent.agent.manager.RunManager._end_unstarted`).
RESULT_NOT_STARTED: str = "The run could not be started."
# A stop for lack of progress: the bare text of older runs, and the start of the text that says what was tried.
RESULT_STALLED: str = "Stopped: the agent kept repeating the same failing actions."
PREFIX_STALLED: str = "Stopped: the agent was not making progress"
# A run that reached a limit of [agent] run_max_minutes or run_max_tokens and did not finish when it was told to.
PREFIX_LIMIT: str = "Stopped: the run reached its limit"
RESULT_EMPTY_REPLIES: str = "The model returned empty replies and did not finish."
# A run whose replies the model service kept ending before they were complete (its output limit, or a reason such
# as a content filter), followed by the reason the last one ended with.
PREFIX_CUT_SHORT: str = "The model's replies were cut short"
PREFIX_EMERGENCY_STOP: str = "Emergency stop: "
# The result of a run the emergency stop of the web UI or the API ended (``RunManager.emergency_stop``).
RESULT_EMERGENCY_STOPPED: str = f"{PREFIX_EMERGENCY_STOP}every active run was stopped."
PREFIX_INTERNAL_ERROR: str = "Internal error: "
# Between the answer of a run and what the goal check made of it, when the check does not accept the end.
GOAL_CHECK_MARK: str = "\n\n---\nGoal check: "

type Ending = Literal[
    "active",
    "finished",
    "failed",
    "goal_check_refused",
    "stalled",
    "limit",
    "empty_replies",
    "cut_short",
    "internal_error",
    "daemon_restarted",
    "stopped",
    "daemon_shutdown",
    "process_ended",
    "process_died",
    "emergency_stop",
]

# What each ending is called to a person.
ENDING_LABELS: dict[Ending, str] = {
    "active": "still active",
    "finished": "finished",
    "failed": "failed (the agent's own report, or an error of the model service)",
    "goal_check_refused": "refused by the goal check",
    "stalled": "stopped by the progress watchdog",
    "limit": "stopped at its limit of minutes or tokens",
    "empty_replies": "empty replies from the model",
    "cut_short": "replies from the model cut short",
    "internal_error": "internal error",
    "daemon_restarted": "cut off when the daemon died",
    "stopped": "stopped (by the user; older runs may be restarts, which were not told apart)",
    "daemon_shutdown": "cut off when the daemon shut down",
    "process_ended": "cut off when its dotx command was told to end (a closed terminal, SIGTERM)",
    "process_died": "cut off when its dotx command died (it was killed, or it crashed)",
    "emergency_stop": "emergency stop",
}


def _failed_ending(result: str) -> Ending:
    if result == RESULT_STALLED or result.startswith(PREFIX_STALLED):
        return "stalled"
    if result.startswith(PREFIX_LIMIT):
        return "limit"
    if result == RESULT_EMPTY_REPLIES:
        return "empty_replies"
    if result.startswith(PREFIX_CUT_SHORT):
        return "cut_short"
    if result == RESULT_RESTARTED:
        return "daemon_restarted"
    if result == RESULT_COMMAND_DIED:
        return "process_died"
    if result.startswith(PREFIX_INTERNAL_ERROR):
        return "internal_error"
    if GOAL_CHECK_MARK in result:
        return "goal_check_refused"
    return "failed"


def _cancelled_ending(result: str) -> Ending:
    if result == RESULT_SHUTDOWN:
        return "daemon_shutdown"
    if result == RESULT_PROCESS_ENDED:
        return "process_ended"
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
