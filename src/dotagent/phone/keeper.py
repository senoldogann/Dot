"""Keeping the phone reachable: the pure steps of the link's state, and the loop the daemon runs while ``[phone]``
is on.

Every ``check_every_s`` seconds the loop looks at the phone. When no address of WebDriverAgent answers and the Mac lists
the phone as available, it launches the runner app again (opening it starts the server) and looks once more. A phone
that stays out of reach, or answers without saying whether it is locked, for ``notify_after_s`` is told to the owner
once until it is up again; the owner is the one who brings it back. A locked phone is not told: locking it is how the
owner uses it, and a notice after every lock is noise (item 1197). Settings › Phone and ``phone_status`` still say it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from dotagent.core.errors import DotError, PhoneError
from dotagent.core.logs import get_logger
from dotagent.phone.types import PhoneProbe, PhoneState, PhoneStateKind

log = get_logger(__name__)

type Teller = Callable[[str, str], Awaitable[None]]

# After a round that failed the next waits twice as long for each failure in a row, up to this, so that a daemon with
# a phone that is gone does not ask devicectl every ``check_every_s`` for days.
_MOST_BACKOFF_S: float = 600.0
_BACKOFF_DOUBLINGS: int = 6
# How much of a command's error output the log keeps: its first line.
_STDERR_CHARS: int = 300


def first_line(text: object) -> str | None:
    """The first line of ``text``, cut to :data:`_STDERR_CHARS`; None when there is no text: a command's whole error
    output stays out of the log's line (it is in the error's context)."""
    if not isinstance(text, str):
        return None
    lines: list[str] = text.strip().splitlines()
    return None if not lines else lines[0][:_STDERR_CHARS]


# The title and the text that tell the owner of a state that needs them; a phone that is up or locked needs nothing.
_NOTICES: dict[PhoneStateKind, tuple[str, str]] = {
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
    """The state after ``probe`` at ``now``: ``since`` carries over while the kind is the same and starts again when it
    changes; ``notified`` carries over until the phone is up, so a phone going between locked, unclear and out of
    reach is told once for the whole time it is not up."""
    kind: PhoneStateKind = probe.kind
    if previous is not None and previous.kind == kind:
        return previous.model_copy(update={"url": probe.url, "checked_at": now})
    told: bool = previous is not None and previous.notified and kind != "up"
    return PhoneState(kind=kind, since=now, url=probe.url, notified=told, checked_at=now)


def notice_for(state: PhoneState, notify_after_s: float) -> tuple[str, str] | None:
    """The title and the text that tell the owner of ``state`` now, or ``None``: a state that needs them (out of
    reach, or unclear) is told once, after it has lasted ``notify_after_s``."""
    if state.kind not in _NOTICES or state.notified or state.checked_at - state.since < notify_after_s:
        return None
    return _NOTICES[state.kind]


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
            log.warning("phone_relaunch_failed", error=error.message, stderr=first_line(error.context.get("stderr")))
            relaunched = False
        if relaunched:
            state = await link.check()
    notice: tuple[str, str] | None = notice_for(state, notify_after_s)
    if notice is not None:
        await tell(*notice)
        link.mark_notified()
        state = state.model_copy(update={"notified": True})
    return state


def wait_after(check_every_s: float, failed_in_a_row: int) -> float:
    """How long the keeper waits for its next round: ``check_every_s``, doubled for each round in a row that failed, up
    to :data:`_MOST_BACKOFF_S` (never less than ``check_every_s``)."""
    return min(check_every_s * (1 << min(failed_in_a_row, _BACKOFF_DOUBLINGS)), max(_MOST_BACKOFF_S, check_every_s))


async def keep_phone(
    link: PhoneLink, tell: Teller, check_every_s: float, notify_after_s: float, stopping: asyncio.Event
) -> None:
    """Run :func:`keep_once` every ``check_every_s`` seconds until ``stopping`` is set. A round that fails is logged and
    the next one tries again, after a longer wait for each round in a row that failed (doubling, at most
    :data:`_MOST_BACKOFF_S`, never less than ``check_every_s``): a phone that is not reachable must not end the keeper
    for the rest of the daemon's life (item 1313), and a fault of Dot's own (a bug, an ``OSError`` of the system) is
    logged with its traceback, where one of Dot's own errors says its words."""
    failed_in_a_row: int = 0
    while not stopping.is_set():
        try:
            await keep_once(link, tell, notify_after_s)
        except DotError as error:
            failed_in_a_row += 1
            log.warning("phone_keeper_round_failed", error=str(error), error_type=type(error).__name__)
        except Exception as error:
            failed_in_a_row += 1
            log.error("phone_keeper_round_crashed", error=str(error), error_type=type(error).__name__, exc_info=True)
        else:
            failed_in_a_row = 0
        wait_s: float = wait_after(check_every_s, failed_in_a_row)
        try:
            await asyncio.wait_for(stopping.wait(), wait_s)
        except TimeoutError:
            continue
