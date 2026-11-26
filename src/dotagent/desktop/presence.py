"""Whether the owner is at the Mac (item 1890): the owner's rule is that Dot takes the real pointer and keyboard only
while nobody has touched them for ``[desktop] away_after_s`` (10 minutes), and lets go of them at the owner's first
input, at the next step's boundary.

macOS keeps one clock of the last input, the HID system's idle time, and the events Dot's own steps post to the HID
tap reset it as the owner's do (measured on the owner's Mac on 2026-11-25: a pointer move posted there read 0.3 s
idle after 10 155 s). ``caffeinate -u``, which Dot holds to keep the display awake, does not reset it. So the reading
is told apart from Dot's own input by when Dot posted: an input that falls within a step Dot sent (with
:data:`OWN_INPUT_SLACK_S` of margin) is Dot's, and the owner's last input stays what it was before. The owner's input
while Dot posts is hidden by Dot's own; their next one after it is seen.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from dotagent.core.logs import get_logger

log = get_logger(__name__)

# How far an input may fall outside the span of a step Dot posted and still be Dot's: the HID clock is read a little
# after the last event of the step went, and a step's first event may be stamped a little before its span began.
OWN_INPUT_SLACK_S: float = 1.0


def idle_seconds() -> float | None:
    """How long nobody touched the keyboard, the mouse or the trackpad, in seconds; ``None`` when macOS cannot say,
    which counts as not idle. Events posted to the HID tap (Dot's own steps) count as a touch."""
    # Quartz is loaded by the look, so the initiative loads where it is not (item 1270).
    from Quartz import CGEventSourceSecondsSinceLastEventType, kCGAnyInputEventType, kCGEventSourceStateHIDSystemState

    try:
        seconds: object = CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
        )
    except Exception:
        log.warning("idle_time_unknown", exc_info=True)
        return None
    return float(seconds) if isinstance(seconds, int | float) else None


@dataclass(frozen=True, slots=True)
class OwnInput:
    """When a step of Dot's that posts input began and ended (``time.monotonic`` seconds)."""

    began: float
    ended: float


def owner_input_at(last_input_at: float, own: OwnInput | None, known: float | None, slack_s: float) -> float | None:
    """When the owner last touched the Mac: the last input macOS knows of (``last_input_at``), unless it falls within
    Dot's own last step (``own``), give or take ``slack_s``; then the owner's stays ``known``, what it was before."""
    if own is not None and own.began - slack_s <= last_input_at <= own.ended + slack_s:
        return known
    return last_input_at


def is_away(owner_at: float | None, now: float, away_after_s: float) -> bool:
    """Whether the owner is away: their last input was at least ``away_after_s`` ago. Not knowing when it was is not
    away."""
    return owner_at is not None and now - owner_at >= away_after_s


class OwnerPresence:
    """Reads whether the owner is at the Mac, from the HID idle clock less Dot's own input (the adapter over
    :func:`idle_seconds`; a test gives a clock of its own)."""

    def __init__(self, idle: Callable[[], float | None], clock: Callable[[], float], away_after_s: float) -> None:
        self._idle: Callable[[], float | None] = idle
        self._clock: Callable[[], float] = clock
        self._away_after_s: float = away_after_s
        self._own: OwnInput | None = None
        self._owner_at: float | None = None

    @property
    def away_after_s(self) -> float:
        return self._away_after_s

    def now(self) -> float:
        """The clock the presence is read by, which :meth:`note_own_input` takes."""
        return self._clock()

    def away(self) -> bool:
        """Whether nobody but Dot has touched the keyboard, the mouse or the trackpad for ``away_after_s``."""
        now: float = self._clock()
        idle: float | None = self._idle()
        if idle is None:
            return False
        self._owner_at = owner_input_at(now - idle, self._own, self._owner_at, OWN_INPUT_SLACK_S)
        return is_away(self._owner_at, now, self._away_after_s)

    def note_own_input(self, began: float, ended: float) -> None:
        """Remember that a step of Dot's posted input between ``began`` and ``ended`` (``time.monotonic``)."""
        self._own = OwnInput(began=began, ended=ended)


def mac_presence(away_after_s: float) -> OwnerPresence:
    """The owner's presence as this Mac's HID clock tells it."""
    return OwnerPresence(idle_seconds, time.monotonic, away_after_s)


# What a step of a batch says when it waits for the owner to step away (item 1890).
WAITS_FOR_AWAY: str = "not sent: it needs the Mac's own pointer or keyboard, and waits for the owner to step away"


def owner_here_note(index: int) -> str:
    """Why a batch stopped at step ``index``, the owner being at the Mac when it began to take the pointer."""
    return (
        f"step {index} needs the Mac's own pointer or keyboard and the owner is at the Mac, so it and the steps after "
        "it were not sent: they wait for the owner to step away (press, focus, set_value and menu need neither)"
    )


def owner_back_note(index: int) -> str:
    """Why a batch that took the pointer stopped at step ``index``: the owner came back."""
    return (
        f"the owner came back to the Mac before step {index}: Dot let go of the pointer and the keyboard, and the "
        f"steps from {index} on were not sent; they wait for the owner to step away"
    )
