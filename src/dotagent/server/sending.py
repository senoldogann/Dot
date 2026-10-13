"""A message the pages send once (item 189): each new message of the pages carries a key of its own, and the message sent
again with that key, after an answer that was lost, gets what its first sending made, instead of a second chat, run,
queued message or steer."""

import time
from collections.abc import Awaitable, Callable
from typing import Literal

from dotagent.core.errors import RunConflict
from dotagent.store.db import Store
from dotagent.store.models import SentMessage

# What a message of the pages is sent for: a new run (with a chat, when it has none), a steer, a place in a queue.
SendingKind = Literal["run", "steer", "queue"]
# A sending under way for this long without coming to anything is taken for one whose daemon stopped part way.
_STALE_S: float = 120.0
# How long a key is kept: an answer that was lost is sent again in minutes, not days.
_KEPT_S: float = 86_400.0


async def send_once(store: Store, kind: SendingKind, key: str | None, send: Callable[[], Awaitable[str]]) -> str:
    """Do ``send`` for the message of ``key`` once and return what it came to: the id of the run it started or steered,
    or of the queued message it became, which ``send`` returns. The same key again returns what the first sending came
    to without doing it, and is refused (409) while that sending is under way; a sending that fails lets the key go.
    The keys of each ``kind`` of sending are apart, so a run's id is never taken for a queued message's. Without a key
    (the API used directly) ``send`` is done every time."""
    if key is None:
        return await send()
    kept: str = f"{kind}:{key}"
    now: float = time.time()
    first: SentMessage | None = await store.claim_sent(kept, now, now - _STALE_S, now - _KEPT_S)
    if first is not None:
        if first.target is None:
            raise RunConflict("the message is being sent already", {"kind": kind, "key": key})
        return first.target
    try:
        target: str = await send()
    except BaseException:
        await store.release_sent(kept)
        raise
    await store.settle_sent(kept, target)
    return target
