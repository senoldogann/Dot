"""Schedules: goals that Dot runs later or on a cron expression, started by the daemon."""

from dotagent.core.types import FrozenModel
from dotagent.store.models import RunStatus

# The ``source`` of the threads and runs that a schedule starts.
SCHEDULE_SOURCE: str = "schedule"

# What to tell whoever creates or lists schedules.
DAEMON_NOTE: str = "Schedules start their runs only while the Dot daemon is running (dotx daemon)."

# The live event, of Dot itself (``dotagent.events.SYSTEM_RUN_ID``), of a firing a schedule missed for good where
# nobody saw it: one the daemon was not running for, or one whose run could not start. Sent when the schedule's status
# changes to it, so a schedule that keeps failing the same way is told once.
SCHEDULE_MISSED_EVENT: str = "schedule_missed"


class ScheduleMissed(FrozenModel):
    """What ``schedule_missed`` carries: the schedule and the status that says why its firing was missed."""

    schedule_id: str
    name: str
    status: str


# The live event, of Dot itself, of the end of a schedule's run that its level of notices tells (item 461); the phone
# and the desktop word it as every end of a run.
SCHEDULE_RUN_ENDED_EVENT: str = "schedule_run_ended"


class ScheduleRunEnded(FrozenModel):
    """What ``schedule_run_ended`` carries: the schedule, its run, how the run ended and its answer."""

    schedule_id: str
    run_id: str
    status: RunStatus
    result: str


# The live event, of Dot itself, of a repeating schedule switched off because its runs failed so many times in a row
# (item 458): sent once, as the schedule is off afterwards and its row starts afresh when it is switched on again.
SCHEDULE_STOPPED_EVENT: str = "schedule_stopped"


class ScheduleStopped(FrozenModel):
    """What ``schedule_stopped`` carries: the schedule and how many of its runs in a row failed."""

    schedule_id: str
    name: str
    failures: int


def stop_limit(own: int | None, configured: int) -> int:
    """How many failed runs in a row switch a schedule off: its own choice, else the configured one (0: never)."""
    return configured if own is None else own


def switches_off(repeating: bool, enabled: bool, failed_in_row: int, limit: int) -> bool:
    """Whether a schedule whose run just failed, ``failed_in_row`` in a row, is switched off (item 458): a repeating
    one that is on, when a limit applies and is reached. A one-time schedule has no next time to spare."""
    return repeating and enabled and limit > 0 and failed_in_row >= limit
