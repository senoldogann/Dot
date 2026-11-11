"""The ledger and the progress ladder of a run, stored so a resumed run does not start them again.

The loop writes one ``loop_state`` event at the end of a step and after a compaction. A run that goes on from another
reads that run's newest event and continues from it. A run with no such event starts both again, and its note says so.
The limit clock is not in the snapshot: a resumed run starts its own minutes and tokens.
"""

import asyncio
import sqlite3

from pydantic import JsonValue, ValidationError

from dotagent.agent.continuity import RunLedger
from dotagent.agent.progress import WatchState
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_json
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import EventBus
from dotagent.secrets import known_secrets
from dotagent.store.db import Store

log = get_logger(__name__)

LOOP_STATE_EVENT: str = "loop_state"


class LoopState(FrozenModel):
    """What a resumed run picks up: the ledger, the watchdog's rung, and the counters that decide whether a finish
    is sent back."""

    ledger: RunLedger
    watch: WatchState
    nudges: int = 0
    sent_back: int = 0
    plan_reminders: int = 0
    plan_handed: bool = False


async def remember_loop_state(bus: EventBus, run_id: str, state: LoopState) -> None:
    """Store ``state`` for ``run_id``. A store that refuses is logged: the step goes on, and the next one tries."""
    # The ledger keeps each call's arguments as the model sent them: masked as a call's own event masks them (item 551),
    # since the snapshot is an event the store, the API and the page read.
    known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    masked: JsonValue = redact_json(state.model_dump(mode="json"), known)
    data: JsonObject = masked if isinstance(masked, dict) else {}
    try:
        await bus.emit(run_id, LOOP_STATE_EVENT, data, True)
    except (DotError, sqlite3.Error, OSError) as exc:
        log.warning(
            "loop_state_not_stored",
            run_id=run_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def load_loop_state(store: Store, run_id: str) -> LoopState | None:
    """The newest snapshot of ``run_id``, or ``None`` when it kept none or the one it kept cannot be read."""
    event = await store.last_event(run_id, LOOP_STATE_EVENT)
    if event is None:
        return None
    try:
        return LoopState.model_validate(event.data)
    except ValidationError as exc:
        log.warning("loop_state_unreadable", run_id=run_id, error=str(exc))
        return None
