"""Short identifiers that sort by creation time."""

import secrets
import time

_DIGITS: str = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    chars: list[str] = []
    remaining: int = value
    while remaining:
        remaining, digit = divmod(remaining, 36)
        chars.append(_DIGITS[digit])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Return ``<prefix>_<millis in base36>_<6 hex>``."""
    millis: int = int(time.time() * 1000)
    return f"{prefix}_{_base36(millis)}_{secrets.token_hex(3)}"
