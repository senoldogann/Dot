"""The transitions the recovery of a run, a queue, a plan step and an objective go through.

Each function is pure: it names the next state or refuses the move. The store writes a move only while the row is
still in the state the function left, so a second writer finds the move already made and does not make it again.
"""

from typing import Literal

from dotagent.core.errors import InvalidValueError
from dotagent.store.models import (
    OBJECTIVE_MOVES,
    OCCUPIED_STATUSES,
    TERMINAL_STATUSES,
    WORKING_STATUSES,
    ObjectiveStatus,
    RunStatus,
)

# Why a chat's queue is held. ``crash`` is released when the run that goes on from the cut-off ends done.
# ``owner`` waits until the owner resumes the queue.
HoldReason = Literal["owner", "crash"]
# How a resumed run's ledger and progress ladder stand: restored from the snapshot, started again because none was
# kept, or not mentioned (a note written before the snapshot existed).
LadderNote = Literal["restored", "reset", "unsaid"]
# What can happen to a run. ``begin_end`` is the decision to end, before the row that records the end is stored.
# ``finish`` is that row, and names which terminal state it is.
RunEvent = Literal["block", "unblock", "begin_end", "finish"]


def transition_run(status: RunStatus, event: RunEvent, terminal: RunStatus | None = None) -> RunStatus:
    """The status a run in ``status`` has after ``event``. A finish names its ``terminal`` status. A move the table
    does not have is refused, including a second end and a return from ``ending`` to working."""
    if event == "block" and status in ("queued", "running"):
        return "blocked"
    if event == "unblock" and status == "blocked":
        return "running"
    if event == "begin_end" and status in WORKING_STATUSES:
        return "ending"
    if event == "finish" and status in OCCUPIED_STATUSES and terminal in TERMINAL_STATUSES:
        return terminal
    raise InvalidValueError(
        "a run does not move like that",
        {"from": status, "event": event, "terminal": terminal},
    )


def shown_status(status: RunStatus, pending_end: bool) -> RunStatus:
    """The status a page shows. A run whose end the store refused is ``ending`` even while the row still says it
    works, so the page does not show a run that has already stopped as running."""
    if pending_end and status in WORKING_STATUSES:
        return "ending"
    return status


def hold_reason_for_cutoff(cut: str | None) -> HoldReason | None:
    """Whether the queue of a chat whose newest run ended as ``cut`` is held when the daemon starts, and why.

    ``None``: a clean shutdown. The run that goes on from it drains the queue when it ends done, so the queue is not
    held. ``crash``: the daemon or the command died. The queue stays held while that run is resumed, and opens when
    the resumed run ends done. ``owner``: the run failed, was stopped, or ended some other way. The owner resumes it.
    """
    if cut == "daemon_shutdown":
        return None
    if cut in ("daemon_restarted", "process_ended", "process_died"):
        return "crash"
    return "owner"


def transition_objective(current: ObjectiveStatus, status: ObjectiveStatus) -> ObjectiveStatus:
    """The status an objective in ``current`` has after a move to ``status``. The same status is a note, not a move."""
    if status == current:
        return current
    if status not in OBJECTIVE_MOVES[current]:
        raise InvalidValueError(
            "an objective does not move like that",
            {"from": current, "to": status},
        )
    return status
