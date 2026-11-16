"""Telling the owner that a model's account ran out of money (item 1207).

A provider that answers "Insufficient balance" or "Prepaid credit balance is insufficient" stops every run and schedule
on its models, and before this nothing said so: three schedules failed on 2026-11-11 before anyone looked. The router
pauses such a model (:data:`dotagent.llm.router.MONEY_REASONS`); :class:`MoneyTeller` tells the owner once a day for
each provider, through :func:`dotagent.notify.owner.tell_owner`, so a balance that stays spent is not told at every
pause."""

import time

from dotagent.events import EventBus
from dotagent.llm.router import ModelRef, Pause
from dotagent.notify.owner import tell_owner

# A provider is told about again after this long while its money stays spent.
RETELL_S: float = 86_400.0
# Where the tell opens: the models page, where the model of the runs is chosen.
MODELS_ROUTE: str = "#/settings/models"


def money_title(provider: str) -> str:
    """The title of the tell for ``provider``."""
    return f"{provider} cannot answer: payment needed"


def money_text(ref: ModelRef, pause: Pause, words: str) -> str:
    """The text of the tell: which model, why, and the provider's own words, which say where to top up."""
    return (
        f"{ref.provider}:{ref.model} is paused ({pause.reason}); the runs and schedules on it fail until the account "
        f"is topped up or another model is chosen. The provider said: {words}"
    )


def due(told: dict[str, float], provider: str, now: float) -> bool:
    """Whether ``provider`` is to be told about at ``now``, given when each provider was told last."""
    last: float | None = told.get(provider)
    return last is None or now - last >= RETELL_S


class MoneyTeller:
    """Tells the owner about a provider whose money is spent, at most once in :data:`RETELL_S` for each provider. The
    times live in memory: a restart of the daemon tells once more, which is what a restart should show."""

    def __init__(self, bus: EventBus) -> None:
        self._bus: EventBus = bus
        self._told: dict[str, float] = {}

    async def __call__(self, ref: ModelRef, pause: Pause, words: str) -> None:
        now: float = time.time()
        if not due(self._told, ref.provider, now):
            return
        await tell_owner(self._bus, money_title(ref.provider), money_text(ref, pause, words), MODELS_ROUTE)
        self._told[ref.provider] = now
