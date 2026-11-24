"""The activity lease of a phone (item 1420): the app says every 25 seconds that it is in the foreground and which
chat it shows, and the daemon keeps the alerts of a run's end to that phone quiet for as long as the lease lives.

A lease is held in memory alone and expires by itself: the app renews it every 25 seconds, well before its 45 seconds
are over, and a daemon that starts again, a phone that lost its network or an app that iOS suspended without a word
ends up with none, which is the safe side (the alerts go). ``background`` ends the lease at once. It holds back the
alerts of a run's end alone (:func:`dotagent.notify.push.quiet_while_shown`); what asks something of the owner (an
approval, a question, a secret, a hand-off) is never held back by it, and neither is the Live Activity, which the lease
does not touch."""

import time
from collections.abc import Callable
from typing import Literal

from dotagent.core.types import FrozenModel

# How long a lease lives when the app names no time, and the longest it may name: a lease that outlives the app by long
# keeps alerts quiet that the owner is not looking at.
DEFAULT_TTL_S: float = 45.0
MAX_TTL_S: float = 90.0

type LeaseState = Literal["foreground", "background"]


def lease_seconds(ttl_s: float | None) -> float:
    """How long a lease lives for the ``ttl_s`` the app named: :data:`DEFAULT_TTL_S` when it named none, at most
    :data:`MAX_TTL_S`."""
    return DEFAULT_TTL_S if ttl_s is None else min(ttl_s, MAX_TTL_S)


class ActivityLease(FrozenModel):
    """What the app of a phone last said: it is in the foreground, showing ``chat`` (a thread id) when it names one,
    until ``expires_at`` (seconds since the epoch) unless it says so again."""

    phone_id: str
    chat: str | None
    expires_at: float


class PhoneLeases:
    """The live leases of the paired phones, one each. One per daemon, shared by the route that renews them and the
    watch that sends the alerts. ``clock`` is where the time comes from, so that a test moves it."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock: Callable[[], float] = clock
        self._leases: dict[str, ActivityLease] = {}

    def renew(self, phone_id: str, state: LeaseState, chat: str | None, ttl_s: float | None) -> ActivityLease | None:
        """Take what the app of ``phone_id`` says. ``foreground`` makes the lease it names (``ttl_s`` seconds, or
        :data:`DEFAULT_TTL_S`, at most :data:`MAX_TTL_S`) and returns it; ``background`` ends the lease at once and
        returns ``None``."""
        if state == "background":
            self._leases.pop(phone_id, None)
            return None
        lease: ActivityLease = ActivityLease(
            phone_id=phone_id, chat=chat, expires_at=self._clock() + lease_seconds(ttl_s)
        )
        self._leases[phone_id] = lease
        return lease

    def live(self, phone_id: str, now: float | None = None) -> ActivityLease | None:
        """The lease of ``phone_id`` that holds at ``now`` (the clock's time when none is given); one that ran out is
        dropped and is none."""
        at: float = self._clock() if now is None else now
        lease: ActivityLease | None = self._leases.get(phone_id)
        if lease is not None and lease.expires_at <= at:
            del self._leases[phone_id]
            return None
        return lease

    def forget(self, phone_id: str) -> None:
        """Drop the lease of ``phone_id``: the phone was removed."""
        self._leases.pop(phone_id, None)
