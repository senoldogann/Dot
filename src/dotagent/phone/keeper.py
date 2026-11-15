"""Keeping the phone reachable: the pure steps of the link's state, and the loop the daemon runs while ``[phone]``
is on.

Every ``check_every_s`` seconds the loop looks at the phone. When no address of WebDriverAgent answers and the Mac lists
the phone as available, it launches the runner app again (opening it starts the server) and looks once more. A phone
that stays locked or out of reach for ``notify_after_s`` is told to the owner once for that state; the owner is the one
who unlocks it or brings it back.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from dotagent.core.errors import DotError, PhoneError
from dotagent.core.logs import get_logger
from dotagent.phone.types import PhoneProbe, PhoneState, PhoneStateKind

log = get_logger(__name__)

type Teller = Callable[[str, str], Awaitable[None]]

# The title and the text that tell the owner of a state that needs them; a phone that is up needs nothing.
_NOTICES: dict[PhoneStateKind, tuple[str, str]] = {
    "locked": (
        "Your iPhone is locked",
        "Dot can do nothing on it while it is locked. Unlock it, and keep Auto-Lock at Never and Low Power Mode off "
        "while it charges.",
    ),
    "unclear": (
        "Dot cannot tell whether your iPhone is locked",
        "WebDriverAgent answers at its address but does not say whether the phone is locked; the daemon's log has what "
        "it answered. Open the runner app on the phone, or check [phone] wda_urls.",
    ),
    "unreachable": (
        "Dot cannot reach your iPhone",
        "Dot cannot reach WebDriverAgent on it. Bring it into reach of the Mac or open the runner app.",
    ),
}
# The state in a few words that stay the same while it does (the prompt is kept for a provider's cache).
_WORDS: dict[PhoneStateKind, str] = {
    "up": "reachable and unlocked",
    "locked": "reachable but locked",
    "unclear": "answering, but not saying whether it is locked",
    "unreachable": "not reachable",
}


def next_state(previous: PhoneState | None, probe: PhoneProbe, now: float) -> PhoneState:
    """The state after ``probe`` at ``now``: ``since`` and ``notified`` carry over while the kind is the same, and
    start again when it changes."""
    kind: PhoneStateKind = probe.kind
    if previous is not None and previous.kind == kind:
        return previous.model_copy(update={"url": probe.url, "checked_at": now})
    return PhoneState(kind=kind, since=now, url=probe.url, notified=False, checked_at=now)


def notice_for(state: PhoneState, notify_after_s: float) -> tuple[str, str] | None:
    """The title and the text that tell the owner of ``state`` now, or ``None``: a state that needs them is told once,
    after it has lasted ``notify_after_s``."""
    if state.kind == "up" or state.notified or state.checked_at - state.since < notify_after_s:
        return None
    return _NOTICES[state.kind]


def state_word(state: PhoneState | None) -> str:
    """The state as a few words that stay the same while it does (the prompt is kept for a provider's cache)."""
    return "not checked yet" if state is None else _WORDS[state.kind]


def state_line(state: PhoneState | None, now: float) -> str:
    """The state as one sentence, for the prompt and the tools."""
    if state is None:
        return "not checked yet"
    lasted: str = f"for {int(now - state.since)} s; checked {int(now - state.checked_at)} s ago"
    if state.kind == "locked":
        return f"reachable but locked: it can be neither read nor acted on ({lasted})"
    return f"{_WORDS[state.kind]} ({lasted})"


class PhoneLink(Protocol):
    """What the loop asks of the phone desk."""

    async def check(self) -> PhoneState: ...

    async def relaunch(self) -> bool: ...

    def mark_notified(self) -> None: ...


async def keep_once(link: PhoneLink, tell: Teller, notify_after_s: float) -> PhoneState:
    """One round of the loop: look; when the phone does not answer, try to start its runner and look again; tell the
    owner when ``state`` needs them. Returns the state it ends in."""
    state: PhoneState = await link.check()
    if state.kind == "unreachable":
        try:
            relaunched: bool = await link.relaunch()
        except PhoneError as error:
            # The Mac could not list or start it (devicectl failed or timed out): the owner is still told below.
            log.warning("phone_relaunch_failed", error=str(error))
            relaunched = False
        if relaunched:
            state = await link.check()
    notice: tuple[str, str] | None = notice_for(state, notify_after_s)
    if notice is not None:
        await tell(*notice)
        link.mark_notified()
        state = state.model_copy(update={"notified": True})
    return state


async def keep_phone(
    link: PhoneLink, tell: Teller, check_every_s: float, notify_after_s: float, stopping: asyncio.Event
) -> None:
    """Run :func:`keep_once` every ``check_every_s`` seconds until ``stopping`` is set. A round that fails with one of
    Dot's own errors is logged and the next one tries again; anything else ends the loop."""
    while not stopping.is_set():
        try:
            await keep_once(link, tell, notify_after_s)
        except DotError as error:
            log.warning("phone_keeper_round_failed", error=str(error), error_type=type(error).__name__)
        try:
            await asyncio.wait_for(stopping.wait(), check_every_s)
        except TimeoutError:
            continue
