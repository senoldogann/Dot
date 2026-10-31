"""How the end of a schedule's run is told on the phone and the desktop (item 461): ``always`` tells every end, as Dot
did; ``problems`` tells an end that leaves the owner something to do, and a failure that follows a run that went well;
``never`` tells nothing (the run's page and the Schedules row still say how it went). A run the owner started by hand is
told as ``always``: they asked for it. The question a run asks, a draft it leaves, the switch-off of a schedule and the
other notices of Dot are no run's end and are told whatever the level."""

from dotagent.store.db import Store
from dotagent.store.models import EventRecord, NotifyLevel, RunStatus
from dotagent.tools.meta import FINISH_TOOL

# The level of a run started by hand (item 455).
BY_HAND_LEVEL: NotifyLevel = "always"


async def said_nothing_new(store: Store, run_id: str) -> bool:
    """Whether the finish call that ended ``run_id`` said it found nothing new for the owner (item 880): the newest one
    that went through, not one refused before the run went on (the review of item 880)."""
    finished: EventRecord | None = await store.last_ok_call_of(run_id, FINISH_TOOL)
    args: object = None if finished is None else finished.data.get("args")
    return isinstance(args, dict) and args.get("nothing_new") is True


def notify_level(own: NotifyLevel | None, configured: NotifyLevel) -> NotifyLevel:
    """The level of a schedule: its own choice, else the configured one."""
    return configured if own is None else own


def tells(
    level: NotifyLevel, status: RunStatus, needs: str, failed_in_row: int | None, nothing_new: bool = False
) -> bool:
    """Whether the end of a schedule's run is told: at ``problems``, a run that ended with something it needs from the
    owner (``needs``) unless it was stopped, failed or not, and a failed run when it is the first of a row
    (``failed_in_row`` is 1, or unknown when the row could not be written). At ``always``, every end but one done that
    said it found ``nothing_new`` and needs nothing (item 880)."""
    if level == "always":
        return not (nothing_new and status == "done" and needs.strip() == "")
    if level == "never":
        return False
    if needs.strip() != "" and status != "cancelled":
        return True
    return status == "failed" and (failed_in_row is None or failed_in_row == 1)
