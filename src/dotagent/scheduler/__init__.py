"""Schedules: goals that Dot runs later or on a cron expression, started by the daemon."""

# The ``source`` of the threads and runs that a schedule starts.
SCHEDULE_SOURCE: str = "schedule"

# What to tell whoever creates or lists schedules.
DAEMON_NOTE: str = "Schedules start their runs only while the Dot daemon is running (dotx daemon)."
