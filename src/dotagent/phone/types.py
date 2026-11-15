"""Typed values of the phone subsystem (item 1196): what a look at the phone found and the state of the link to it."""

from typing import Literal

from dotagent.core.types import FrozenModel

# ``unclear``: an address answers, but not whether the phone is locked (a refusal, or an answer of another shape).
type PhoneStateKind = Literal["up", "locked", "unclear", "unreachable"]


class PhoneProbe(FrozenModel):
    """What one look at the phone found: the address that answered (``None`` when none did) and whether it is locked
    (``None`` when that could not be had: no address answered, or the one that did would not say)."""

    url: str | None
    locked: bool | None

    @property
    def kind(self) -> PhoneStateKind:
        if self.url is None:
            return "unreachable"
        if self.locked is None:
            return "unclear"
        return "locked" if self.locked else "up"


class PhoneState(FrozenModel):
    """The link to the phone as the last look left it: its kind, since when (a monotonic time of the daemon), the
    address that answered, and whether the owner was told of this state already."""

    kind: PhoneStateKind
    since: float
    url: str | None
    notified: bool
    checked_at: float
