"""Pairing a phone (item 267): a short code, shown in Settings › Phone, that an app added to a phone's home screen
trades once for the access token. iOS gives such an app storage of its own, apart from Safari's, so the token that the
address of the QR code carried into Safari is not there, and nobody types a 43-character token on a phone.

A code is eight characters of Crockford's base32 (40 bits), good for ten minutes and for one use. A new code takes the
place of the one before, and five wrong tries use a code up, so that it cannot be guessed while it is good.
"""

import hmac
import secrets
from dataclasses import dataclass, replace

# Crockford's base32: no I, L, O or U, so that what is read off a screen is typed back right.
ALPHABET: str = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_CHARS: int = 8
CODE_TTL_S: float = 600.0
CODE_TRIES: int = 5
# What a person may type for a character of the alphabet, as Crockford's base32 reads it.
_READ_AS: dict[str, str] = {"O": "0", "I": "1", "L": "1"}
_LEFT_OUT: frozenset[str] = frozenset({" ", "-"})


@dataclass(frozen=True)
class PairingCode:
    """A code that is good until ``expires_at`` (seconds since the epoch) for ``tries_left`` more wrong tries."""

    code: str
    expires_at: float
    tries_left: int


@dataclass(frozen=True)
class Redeemed:
    """What a try did: whether it matched, and the code that is still good after it (``None`` once there is none)."""

    matched: bool
    left: PairingCode | None


def new_code(now: float) -> PairingCode:
    """A new code, good for :data:`CODE_TTL_S` from ``now``."""
    return PairingCode(
        code="".join(secrets.choice(ALPHABET) for _ in range(CODE_CHARS)),
        expires_at=now + CODE_TTL_S,
        tries_left=CODE_TRIES,
    )


def shown_code(code: PairingCode) -> str:
    """The code as Settings shows it, in two groups of four: ``7KQ2-M9XD``."""
    half: int = CODE_CHARS // 2
    return f"{code.code[:half]}-{code.code[half:]}"


def typed_code(text: str) -> str:
    """What a person typed, as the alphabet writes it: in upper case, without spaces or dashes, O as 0, I and L as 1."""
    return "".join(_READ_AS.get(char, char) for char in text.upper() if char not in _LEFT_OUT)


def redeem(current: PairingCode | None, typed: str, now: float) -> Redeemed:
    """Try ``typed`` against the code that is good, if any: a match uses the code up, a wrong try takes one of its
    tries, and the last one uses it up too."""
    if current is None or now >= current.expires_at:
        return Redeemed(matched=False, left=None)
    if hmac.compare_digest(typed_code(typed).encode("utf-8"), current.code.encode("ascii")):
        return Redeemed(matched=True, left=None)
    tries: int = current.tries_left - 1
    return Redeemed(matched=False, left=replace(current, tries_left=tries) if tries > 0 else None)
