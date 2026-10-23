"""Which runs of Dot may have written a row that the owner's own conversation holds (item 572).

Dot runs with the owner's Apple ID session in Messages, so a run can write a row flagged as the owner's into the
conversation the iMessage channel reads as the owner's commands: the ``apple_script`` tool, ``osascript`` in a command,
keystrokes in Messages, an MCP tool that sends. The guard sees each of those as a step that raises ``message_send`` and
asks, but who answers depends on the mode (the owner, the review model in ``auto``, nobody in ``yolo``), and ``chat.db``
cannot tell a row this Mac wrote from one the owner's iPhone did. What must hold is that a message the channel runs as
the owner's was not written by a run: a row dated in the window of such a step of any run is not run."""

from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord

# What a step that may send a message is recorded with in its flags.
SEND_FLAG: str = "message_send"
# A row is not run when its date lies from this long before such a step began to the moment it ended...
LEAD_S: float = 300.0
# ...and this long after: Messages may date a row a little after the script that sent it has returned.
TAIL_S: float = 10.0
# The longest a step can go on after it was answered, a little over the longest a tool is given (the shell's 1,860 s):
# for a step with no end recorded yet, which is still going on or was cut off with its run.
LONGEST_STEP_S: float = 2000.0
# What ends a step that was answered: its result, or the end of its run (a run cut off with its daemon is closed, at its
# last sign of life, when the daemon starts again).
_ENDS: tuple[str, ...] = ("tool_result", "run_finished")


async def runs_sending_at(store: Store, moment: float, now: float) -> list[str]:
    """The runs, each once, with a step that raised ``message_send`` whose window holds ``moment``, read at ``now``.
    The window of a step that ended is from :data:`LEAD_S` before it began (the wait for its answer is part of it) to
    :data:`TAIL_S` after its end, which its ``tool_result`` event records. The window of a step that was answered and
    has no end yet is from :data:`LEAD_S` before the answer to :data:`LONGEST_STEP_S` after it, or to the end of its
    run: its approval is stored before it goes ahead, so that a step that sends and then goes on is known while it
    goes on."""
    runs: set[str] = set()
    for event in await store.flagged_events("tool_result", SEND_FLAG, moment - TAIL_S, now):
        if event.ts - _seconds(event) - LEAD_S <= moment:
            runs.add(event.run_id)
    for approval in await store.flagged_approvals(SEND_FLAG, moment - LONGEST_STEP_S, moment + LEAD_S):
        answered: float = _answered(approval)
        ended: EventRecord | None = await store.event_after(approval.run_id, _ENDS, answered)
        if answered - LEAD_S <= moment <= (answered + LONGEST_STEP_S if ended is None else ended.ts + TAIL_S):
            runs.add(approval.run_id)
    return sorted(runs)


def _seconds(event: EventRecord) -> float:
    """How long the step of a ``tool_result`` event took, from its start to the record of its end."""
    took: object = event.data.get("seconds")
    return float(took) if isinstance(took, int | float) and not isinstance(took, bool) else 0.0


def _answered(approval: ApprovalRecord) -> float:
    return approval.created_at if approval.decided_at is None else approval.decided_at
