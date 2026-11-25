"""The caps of what the runs spend (item 1731, ``[budget]``) and what a run is expected to spend.

A cap is a number of tokens (input and output of every model call, cached input included, as the Usage page counts
them): for a run with the runs it delegated to, for every run that began on the local day, and for the runs of one
source that began on it. Before the calls of a step run (:func:`dotagent.agent.executor.execute_calls`, the one path of
every tool call of Dot's loop, of a run on the Claude plan and of OpenCode's agent), the run is read against them; past
one, the owner is asked through the approval broker whether it goes on, in every approval mode, yolo and free mode
included: the owner's own word of 2026-11-24 (docs/freedom.md). **Continue** lets that cap's tokens again go past it
(for the run, or for the day); **Stop**, or no answer before ``[policy] approval_timeout_s``, sets the run's stop and
the step's calls are refused, so the run ends as the Stop button ends it. One card per cap at a time: the runs that
reach the cap of the same day while it is asked wait for that answer, and a Stop holds for each of them.

What the owner let past a cap is kept in this process alone, as free mode's switch is: after a restart the next step
past the cap asks again. The estimate is what the ended runs of the same model, mode and source used in the last days,
with their helpers. Dot keeps no price for a model, so nothing here is money: a plan (the Claude plan, a subscription)
is a window that fills, which the run's page shows beside it (item 1403).
"""

import asyncio
import time
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal

from dotagent.agent.approvals import ApprovalBroker, ApprovalOutcome
from dotagent.config import BudgetConfig
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.db import Store
from dotagent.store.models import BudgetSpent, RunRecord
from dotagent.store.stats import RunBudget, RunEstimate, local_day_start

log = get_logger(__name__)

# The tool and the severity of the card that asks the owner at a cap; the pages draw it as Continue or Stop.
BUDGET_TOOL: str = "budget"
BUDGET_SEVERITY: str = "budget"
# The ended runs an estimate is read from: the last so many of the same model, mode and source, of the last so many
# days, and none from fewer than the least.
ESTIMATE_RUNS: int = 50
ESTIMATE_DAYS: int = 30
ESTIMATE_LEAST: int = 3
_SECONDS_PER_DAY: float = 86400.0
_MILLION: float = 1_000_000.0
_THOUSAND: float = 1_000.0
_MANY_MILLIONS: float = 10.0

type CapScope = Literal["run", "day", "source"]

# What the owner reads for a source in the words of a card.
_SOURCE_WORDS: Mapping[str, str] = {
    "ui": "your chats and tasks",
    "schedule": "the schedules",
    "initiative": "Dot's own work",
    "review": "the code reviews",
    "delegate": "the helpers",
}


class CapReached(FrozenModel):
    """A run past a cap: which cap (``scope``), its key among the caps (``run:<id>``, ``day:<date>``,
    ``source:<source>:<date>``), the run's source, what was spent, the cap as it stands with what the owner let past it,
    and what a Continue lets past it again (the cap as configured)."""

    scope: CapScope
    key: str
    source: str
    spent: int
    cap: int
    more: int
    setting: str


def source_cap(config: BudgetConfig, source: str) -> tuple[int, str]:
    """The day's cap of the runs of ``source`` and its setting, ``(0, "")`` for a source with none."""
    match source:
        case "ui":
            return config.ui_day_tokens, "ui_day_tokens"
        case "schedule":
            return config.schedule_day_tokens, "schedule_day_tokens"
        case "initiative":
            return config.initiative_day_tokens, "initiative_day_tokens"
        case "review":
            return config.review_day_tokens, "review_day_tokens"
        case "delegate":
            return config.delegate_day_tokens, "delegate_day_tokens"
        case _:
            return 0, ""


def first_cap_reached(
    config: BudgetConfig, allowed: Mapping[str, int], spent: BudgetSpent, run_id: str, day: str
) -> CapReached | None:
    """The first cap ``spent`` has reached, the run's own first, then its source's, then the day's; ``allowed`` holds
    what the owner let past each cap by its key. ``None`` while the run is under every cap that is set."""
    source_tokens, source_setting = source_cap(config, spent.source)
    candidates: tuple[tuple[CapScope, str, int, int, str], ...] = (
        ("run", f"run:{run_id}", config.run_tokens, spent.run, "run_tokens"),
        ("source", f"source:{spent.source}:{day}", source_tokens, spent.source_day, source_setting),
        ("day", f"day:{day}", config.day_tokens, spent.day, "day_tokens"),
    )
    for scope, key, configured, used, setting in candidates:
        if configured == 0:
            continue
        cap: int = configured + allowed.get(key, 0)
        if used >= cap:
            return CapReached(
                scope=scope, key=key, source=spent.source, spent=used, cap=cap, more=configured, setting=setting
            )
    return None


def loosens(before: BudgetConfig, after: BudgetConfig) -> bool:
    """Whether ``after`` raises a cap of ``before`` or takes one off (0 where it was set): what a phone may not do
    (item 678), while lowering one or setting one is."""
    for key in type(before).model_fields:
        was: int = getattr(before, key)
        now: int = getattr(after, key)
        if was > 0 and (now == 0 or now > was):
            return True
    return False


def tokens_words(tokens: int) -> str:
    """``tokens`` as the owner reads them: ``512M``, ``4,000M``, ``2.3M``, ``45k``."""
    if tokens >= _MANY_MILLIONS * _MILLION:
        return f"{tokens / _MILLION:,.0f}M"
    if tokens >= _MILLION:
        return f"{tokens / _MILLION:.1f}M"
    return f"{tokens / _THOUSAND:.0f}k"


def cap_question(reached: CapReached) -> str:
    """The words of the card that asks the owner at ``reached``."""
    match reached.scope:
        case "run":
            what = f"This task and its helpers have used {tokens_words(reached.spent)} tokens"
            which = "its cap"
        case "source":
            what = f"Today {_SOURCE_WORDS.get(reached.source, reached.source)} have used {tokens_words(reached.spent)}"
            what += " tokens"
            which = "their cap for the day"
        case "day":
            what = f"Today's tasks have used {tokens_words(reached.spent)} tokens"
            which = "the day's cap"
    return (
        f"{what}, past {which} of {tokens_words(reached.cap)} ([budget] {reached.setting}). The task waits. Continue "
        f"lets {tokens_words(reached.more)} more go; Stop ends the task here."
    )


def cap_args(reached: CapReached) -> JsonObject:
    """What the card carries besides its words, for the pages and the record."""
    return {
        "scope": reached.scope,
        "source": reached.source,
        "spent": reached.spent,
        "cap": reached.cap,
        "more": reached.more,
        "more_words": tokens_words(reached.more),
        "setting": reached.setting,
    }


def stopped_text(reached: CapReached) -> str:
    """What each call of the step the owner stopped at a cap returns."""
    return (
        f"Not run: the owner stopped this task at its budget ({tokens_words(reached.spent)} tokens, past the cap of "
        f"{tokens_words(reached.cap)}, [budget] {reached.setting}). The task ends now."
    )


def estimate_of(samples: Sequence[int]) -> RunEstimate | None:
    """The estimate from what like runs spent, ``None`` from fewer than :data:`ESTIMATE_LEAST`."""
    if len(samples) < ESTIMATE_LEAST:
        return None
    ordered: list[int] = sorted(samples)
    middle: int = len(ordered) // 2
    median: int = ordered[middle] if len(ordered) % 2 == 1 else (ordered[middle - 1] + ordered[middle]) // 2
    p90: int = ordered[min(len(ordered) - 1, (len(ordered) * 9) // 10)]
    return RunEstimate(runs=len(ordered), median=median, p90=p90)


class Budget:
    """The caps as they stand (Settings changes them while Dot runs) and what the owner let past each, by its key; one
    lock per cap, so that one card at a time asks about it, and when the owner last stopped at each."""

    def __init__(self, config: BudgetConfig) -> None:
        self._config: BudgetConfig = config
        self._allowed: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._stopped_at: dict[str, float] = {}

    @property
    def config(self) -> BudgetConfig:
        return self._config

    @property
    def allowed(self) -> Mapping[str, int]:
        return self._allowed

    def apply(self, config: BudgetConfig) -> None:
        """Hold the runs to ``config`` from their next step on."""
        self._config = config
        log.info("budget_caps_changed", **config.model_dump())

    def allow(self, key: str, more: int) -> None:
        """Let ``more`` tokens past the cap ``key``."""
        self._allowed[key] = self._allowed.get(key, 0) + more

    def stop(self, key: str) -> None:
        """Keep that the owner stopped at the cap ``key`` now."""
        self._stopped_at[key] = time.monotonic()

    def stopped_since(self, key: str, since: float) -> bool:
        """Whether the owner stopped at the cap ``key`` after ``since`` (monotonic seconds)."""
        return self._stopped_at.get(key, -1.0) >= since

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


def _today(now: float) -> tuple[str, float]:
    """The local day of ``now`` as ``YYYY-MM-DD`` and its start in epoch seconds."""
    return date.fromtimestamp(now).isoformat(), local_day_start(now, 0)


async def _reached(budget: Budget, store: Store, run_id: str) -> CapReached | None:
    day, since = _today(time.time())
    spent: BudgetSpent = await store.budget_spent(run_id, since)
    return first_cap_reached(budget.config, budget.allowed, spent, run_id, day)


async def keep_to_budget(
    budget: Budget, store: Store, approvals: ApprovalBroker, run_id: str, cancel: asyncio.Event
) -> str | None:
    """Hold the run ``run_id`` to its caps before the calls of a step run: ``None`` while it is under every cap, or once
    the owner let it go on past the one it reached; past a cap the owner stopped at (or left unanswered), the run's stop
    is set and the text each of the step's calls returns comes back."""
    # With every cap off the step reads nothing (the freedom contract: the gate's path gets no wait).
    if not any(getattr(budget.config, key) for key in type(budget.config).model_fields):
        return None
    while True:
        reached: CapReached | None = await _reached(budget, store, run_id)
        if reached is None:
            return None
        waited_from: float = time.monotonic()
        async with budget.lock(reached.key):
            if budget.stopped_since(reached.key, waited_from):
                cancel.set()
                return stopped_text(reached)
            # Another run may have been let past this cap while this one waited for the lock.
            again: CapReached | None = await _reached(budget, store, run_id)
            if again is None or again.key != reached.key:
                continue
            log.info("budget_cap_reached", run_id=run_id, scope=again.scope, spent=again.spent, cap=again.cap)
            outcome: ApprovalOutcome = await approvals.request(
                run_id, BUDGET_TOOL, cap_question(again), BUDGET_SEVERITY, cap_args(again)
            )
            if outcome.status == "approved":
                budget.allow(again.key, again.more)
                log.info("budget_cap_raised", run_id=run_id, key=again.key, more=again.more)
                continue
            budget.stop(again.key)
            log.info("budget_cap_stopped", run_id=run_id, key=again.key, status=outcome.status)
            cancel.set()
            return stopped_text(again)


async def run_budget(budget: Budget, store: Store, run: RunRecord) -> RunBudget:
    """The run ``run`` against its caps, with what like runs spent."""
    now: float = time.time()
    day, since = _today(now)
    spent: BudgetSpent = await store.budget_spent(run.id, since)
    samples: list[int] = await store.similar_spends(
        run.model, run.mode, run.source, now - ESTIMATE_DAYS * _SECONDS_PER_DAY, ESTIMATE_RUNS
    )
    config: BudgetConfig = budget.config
    allowed: Mapping[str, int] = budget.allowed
    source_tokens, _ = source_cap(config, spent.source)
    return RunBudget(
        estimate=estimate_of(samples),
        spent=spent.run,
        cap=0 if config.run_tokens == 0 else config.run_tokens + allowed.get(f"run:{run.id}", 0),
        source=spent.source,
        day=spent.day,
        day_cap=0 if config.day_tokens == 0 else config.day_tokens + allowed.get(f"day:{day}", 0),
        source_day=spent.source_day,
        source_day_cap=0 if source_tokens == 0 else source_tokens + allowed.get(f"source:{spent.source}:{day}", 0),
    )
