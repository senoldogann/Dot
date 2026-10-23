"""The numbers and addresses the owner paired with the iMessage channel (item 572), where they are kept, and how a
number or an address a draft names is told to be one of them. The channel reads the conversations with these handles and
answers in them; a draft to one is refused (:mod:`dotagent.drafts.service`), because the conversation is the owner's to
write in and Dot answers there only through the channel."""

import re
from collections.abc import Sequence

from dotagent.core.errors import ChannelError
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

# The key of the store where the channel keeps what it knows (``ImessageState``).
STATE_KEY: str = "channels.imessage"
_PHONE: re.Pattern[str] = re.compile(r"\+[1-9][0-9]{6,14}")
_EMAIL: re.Pattern[str] = re.compile(r"[^@\s<>\",;:()]+@[^@\s<>\",;:()]+\.[^@\s<>\",;:()]+")
_PHONE_MARKS: re.Pattern[str] = re.compile(r"[ ().\-]")
_NOT_DIGITS: re.Pattern[str] = re.compile(r"\D")
# The fewest digits a number written without its country code needs to be told from another: a shorter run is no number.
_LEAST_DIGITS: int = 7


class ImessageState(FrozenModel):
    """What the channel keeps in the store: the handles paired, the newest row read (``None`` until the first poll sets
    it) and the handle of the chat the owner wrote in last, which Dot answers in."""

    handles: tuple[str, ...] = ()
    cursor: int | None = None
    reply_to: str | None = None


def normalize_handle(text: str) -> str:
    """The handle ``text`` names as Messages keeps it: an address in lower case, or a phone number with its country code
    (``+358401234567``) without spaces or marks. Raises :class:`ChannelError` for anything else: a number without its
    country code could not be told from the one the chat is named for."""
    stripped: str = text.strip()
    if "@" in stripped:
        address: str = stripped.casefold()
        if _EMAIL.fullmatch(address) is None:
            raise ChannelError("that is not an email address", {})
        return address
    number: str = _PHONE_MARKS.sub("", stripped)
    if _PHONE.fullmatch(number) is None:
        raise ChannelError("a phone number needs its country code, as +358401234567, or give your Apple ID email", {})
    return number


async def paired_handles(store: Store) -> tuple[str, ...]:
    """The numbers and addresses paired now, as the channel keeps them."""
    kept: str | None = await store.kv_get(STATE_KEY)
    return () if kept is None else ImessageState.model_validate_json(kept).handles


def names_a_handle(address: str, handles: Sequence[str]) -> bool:
    """Whether ``address``, as a draft names a recipient, is one of ``handles`` (as :func:`normalize_handle` writes
    them): an address by its letters, a number by its digits, with its country code or without it (a number written as
    it is dialled at home, ``040 123 4567``, reaches Messages as the same person)."""
    wanted: str = address.strip().casefold()
    if "@" in wanted:
        return wanted in handles
    digits: str = _NOT_DIGITS.sub("", wanted)
    if len(digits) < _LEAST_DIGITS:
        return False
    # A number with no country code starts with its trunk zero (or the 00 of a call abroad), which is no part of it.
    national: str = digits.lstrip("0")
    for handle in handles:
        if "@" in handle:
            continue
        paired: str = _NOT_DIGITS.sub("", handle)
        if wanted.startswith("+"):
            if digits == paired:
                return True
        elif len(national) >= _LEAST_DIGITS and paired.endswith(national):
            return True
    return False
