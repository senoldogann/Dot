"""How the end of a schedule's run is told on the phone and the desktop (item 461): ``always`` tells every end, as Dot
did; ``problems`` tells a failure that follows a run that went well, and an end that leaves the owner something to do;
``never`` tells nothing (the run's page and the Schedules row still say how it went). A run the owner started by hand is
told as ``always``: they asked for it. The question a run asks, a draft it leaves, the switch-off of a schedule and the
other notices of Dot are no run's end and are told whatever the level."""

from dotagent.store.models import NotifyLevel, RunStatus

# The level of a run started by hand (item 455).
BY_HAND_LEVEL: NotifyLevel = "always"


def notify_level(own: NotifyLevel | None, configured: NotifyLevel) -> NotifyLevel:
    """The level of a schedule: its own choice, else the configured one."""
    return configured if own is None else own


def tells(level: NotifyLevel, status: RunStatus, needs: str, failed_in_row: int | None) -> bool:
    """Whether the end of a schedule's run is told: at ``problems``, a failed run when it is the first of a row
    (``failed_in_row`` is 1, or unknown when the row could not be written), and a run that ended with something it
    needs from the owner (``needs``) unless it was stopped."""
    if level == "always":
        return True
    if level == "never":
        return False
    if status == "failed":
        return failed_in_row is None or failed_in_row == 1
    return needs.strip() != "" and status != "cancelled"
