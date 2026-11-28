"""Which bots carry too much (item 2067), read from counts with no model.

A bot with too many open work orders or routines switched on, or whose share of runs that ended done fell from the
week before, drops balls: the owner's word of 2026-11-28, after Grok Bot's note that a manager should see it. The
store counts what each bot carries (:meth:`Store.bot_load_rows`); :func:`bot_load` reads one bot's row against the
limits below and says why it is overloaded, if it is. The Agents page and the phone's bot list and profile show the
mark quietly, with its reasons, and the manager review (item 2068) proposes what to change.
"""

from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.store.models import BotLoadRow

# The window the runs are counted in, and the one before it that the done share is compared with.
LOAD_WINDOW_S: float = 7 * 86400.0
# More open work orders than this at once: a bot whose orders wait on each other.
MOST_OPEN_ORDERS: int = 5
# More routines switched on than this: a bot whose week is its routines' (the owner's bots had one each, item 2067).
MOST_ROUTINES: int = 4
# A done share is compared only when both windows hold at least this many runs: two runs say little.
FEWEST_RUNS: int = 5
# A done share that fell by this much or more (0.2: from 90 % to 70 %) is falling.
DONE_DROP: float = 0.2

OverloadKind = Literal["orders", "routines", "done_share"]


class OverloadReason(FrozenModel):
    """One reason a bot is overloaded: ``count`` against ``limit`` (open orders or routines on, against the most it
    should hold), or, for ``done_share``, its done share now against the week before's, both in whole percent."""

    kind: OverloadKind
    count: int
    limit: int


class BotLoad(FrozenModel):
    """What a bot carries as its row and profile show it: its open work orders, routines switched on, the active
    objectives it serves, its runs of the window, the share of them that ended done and their average steps
    (``None`` with no run), and why it is overloaded (none: it is not)."""

    open_orders: int
    active_routines: int
    objectives: int
    runs: int
    done_share: float | None
    steps: float | None
    reasons: tuple[OverloadReason, ...]


def _share(done: int, runs: int) -> float | None:
    return None if runs == 0 else done / runs


def overload_reasons(row: BotLoadRow) -> tuple[OverloadReason, ...]:
    """Why the bot of ``row`` is overloaded, in the order the mark lists them; empty when it is not."""
    reasons: list[OverloadReason] = []
    if row.open_orders > MOST_OPEN_ORDERS:
        reasons.append(OverloadReason(kind="orders", count=row.open_orders, limit=MOST_OPEN_ORDERS))
    if row.active_routines > MOST_ROUTINES:
        reasons.append(OverloadReason(kind="routines", count=row.active_routines, limit=MOST_ROUTINES))
    if row.runs >= FEWEST_RUNS and row.prior_runs >= FEWEST_RUNS:
        now: float = row.done / row.runs
        before: float = row.prior_done / row.prior_runs
        if before - now >= DONE_DROP:
            reasons.append(OverloadReason(kind="done_share", count=round(now * 100), limit=round(before * 100)))
    return tuple(reasons)


def bot_load(row: BotLoadRow) -> BotLoad:
    """The load of the bot of ``row``."""
    return BotLoad(
        open_orders=row.open_orders,
        active_routines=row.active_routines,
        objectives=row.objectives,
        runs=row.runs,
        done_share=_share(row.done, row.runs),
        steps=None if row.runs == 0 else row.steps / row.runs,
        reasons=overload_reasons(row),
    )
