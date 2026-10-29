"""What the trend engine (:mod:`dotagent.trading.engine`, item 818) saw and did at its last look, and whether it is
paused: kept in the store's key/value table, read by the page, the tools and the engine itself."""

import time

from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

STATE_KEY: str = "trading:engine:state"
PAUSE_KEY: str = "trading:engine:paused"


class EngineShare(FrozenModel):
    """A product as the engine judged it: the share of the account it held before the look's orders, and the one the
    rule wants."""

    product: str
    weight: float
    target: float


class EngineState(FrozenModel):
    """The engine's last look: when, the shares, the orders it placed (their ids) and what held one back."""

    looked_at: float
    shares: tuple[EngineShare, ...]
    placed: tuple[str, ...]
    notes: tuple[str, ...]


class EnginePause(FrozenModel):
    """Why and since when the engine is paused, and who paused it (a run's id, or ``owner``)."""

    reason: str
    since: float
    by: str


async def read_state(store: Store) -> EngineState | None:
    kept: str | None = await store.kv_get(STATE_KEY)
    return None if kept is None else EngineState.model_validate_json(kept)


async def read_pause(store: Store) -> EnginePause | None:
    kept: str | None = await store.kv_get(PAUSE_KEY)
    return None if kept is None else EnginePause.model_validate_json(kept)


async def pause(store: Store, reason: str, by: str, now: float) -> EnginePause:
    """Pause the engine: it places nothing until it is resumed, and holds what it holds."""
    paused: EnginePause = EnginePause(reason=reason, since=now, by=by)
    await store.kv_set(PAUSE_KEY, paused.model_dump_json())
    return paused


async def resume(store: Store) -> bool:
    """Resume the engine; whether it was paused."""
    was: bool = await store.kv_get(PAUSE_KEY) is not None
    await store.kv_delete(PAUSE_KEY)
    return was


def state_text(state: EngineState | None, paused: EnginePause | None) -> str:
    """The engine's last look in words, for the supervisor's tool."""
    if state is None:
        return "The trend engine has not looked yet."
    lines: list[str] = [f"Last look {_utc(state.looked_at)}."]
    if paused is not None:
        lines.append(f"Paused since {_utc(paused.since)} by {paused.by}: {paused.reason}")
    lines.extend(
        f"{share.product}: held {share.weight:.0%} before that look's orders, the rule wants {share.target:.0%}"
        for share in state.shares
    )
    lines.append(f"Orders placed at that look: {len(state.placed)}.")
    lines.extend(state.notes)
    return "\n".join(lines)


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
