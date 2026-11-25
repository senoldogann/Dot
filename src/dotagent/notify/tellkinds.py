"""The kinds of tell (item 1743), apart from :mod:`dotagent.notify.tells` so that :mod:`dotagent.notify.owner`, which
every sender imports, does not import the ledger."""

from typing import Literal

# What a tell is: the morning brief, the evening check-in, a watcher's finding (its subject is the watcher's id), what
# Dot's own initiative did or let go, the end of a run or of a schedule's run (the pushes of ``notify/push.py``), and
# every other word of Dot's own (a restart, a repair, a degraded memory, a work order that waits).
TellKind = Literal["brief", "checkin", "watcher", "initiative", "run_end", "schedule_end", "system"]
