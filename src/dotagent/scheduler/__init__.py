"""Schedules: goals that Dot runs later or on a cron expression, started by the daemon."""

from dotagent.core.types import FrozenModel

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
