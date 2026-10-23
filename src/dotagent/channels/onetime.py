"""A code the owner types back to prove that a channel is theirs (item 572): six digits, good for ten minutes and for
five tries, one at a time for a channel and held in memory only, so that a restart forgets it and the owner asks for
another. A new code takes the place of the one before, and five wrong tries use a code up, so that it cannot be guessed
while it is good."""

import hmac
import secrets
from dataclasses import dataclass, replace

CODE_DIGITS: int = 6
CODE_TTL_S: float = 600.0
CODE_TRIES: int = 5


@dataclass(frozen=True)
class OneTimeCode:
    """A code that is good until ``expires_at`` (seconds since the epoch) for ``tries_left`` more wrong tries."""

    code: str
    expires_at: float
    tries_left: int


@dataclass(frozen=True)
class Redeemed:
    """What a try did: whether it matched, and the code that is still good after it (``None`` once there is none)."""

    matched: bool
    left: OneTimeCode | None


def new_code(now: float) -> OneTimeCode:
    """A new code, good for :data:`CODE_TTL_S` from ``now``."""
    return OneTimeCode(
        code=f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}", expires_at=now + CODE_TTL_S, tries_left=CODE_TRIES
    )


def shown_code(code: OneTimeCode) -> str:
    """The code as the owner reads it, in two groups of three: ``482 913``."""
    half: int = CODE_DIGITS // 2
    return f"{code.code[:half]} {code.code[half:]}"


def typed_digits(text: str) -> str:
    """What a person typed, as digits only: spaces, dashes and the like are left out."""
    return "".join(char for char in text if char in "0123456789")


def redeem(current: OneTimeCode | None, typed: str, now: float) -> Redeemed:
    """Try ``typed`` against the code that is good, if any: a match uses the code up, a wrong try takes one of its
    tries, and the last one uses it up too."""
    if current is None or now >= current.expires_at:
        return Redeemed(matched=False, left=None)
    if hmac.compare_digest(typed_digits(typed).encode("ascii"), current.code.encode("ascii")):
        return Redeemed(matched=True, left=None)
    tries: int = current.tries_left - 1
    return Redeemed(matched=False, left=replace(current, tries_left=tries) if tries > 0 else None)
