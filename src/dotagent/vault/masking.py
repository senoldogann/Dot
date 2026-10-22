"""The values a login of the password manager filled into a page (item 493), taken out of what the browser tools of
the same chat give back: a page that shows a code it was given, a script's value, an error that quotes a field. A value
shorter than a few characters is left as it is: masking it would cut ordinary words."""

import json
import string
from collections.abc import Iterable
from functools import cache
from urllib.parse import quote, quote_plus, unquote

from pydantic import JsonValue

from dotagent.core.errors import DotError, ForbiddenUrlError

# What stands where a value was.
MASK: str = "[filled from 1Password]"
# The shortest value masked: a one-time code has six digits; a shorter one would match ordinary text.
_MASKED_FROM_CHARS: int = 4
# How many times over the escapes of an address are read back, for a value escaped again and again.
_READ_BACK_TIMES: int = 3
# What a browser leaves as it is when it writes a form's field into an address (the URL standard's form encoding).
_FORM_KEPT: frozenset[str] = frozenset(string.ascii_letters + string.digits + "*-._")


def _form_encoded(value: str) -> str:
    """``value`` as a browser writes a form's field into an address: letters, digits and ``*-._`` as they are, a
    space as ``+``, every other byte of its UTF-8 as ``%XX``."""
    return "".join(
        chr(byte) if chr(byte) in _FORM_KEPT else "+" if byte == 0x20 else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )


@cache
def _forms(value: str) -> frozenset[str]:
    """``value`` as a page or a script may write it back: as it is; in an address, as a form, a script's
    ``encodeURIComponent`` and ``encodeURI``, and Python write it (``%21`` or ``!``, ``+`` or ``%20`` for a space);
    inside a JSON string; and each of those with its runs of blanks made one space, as a line of a view is written."""
    written: set[str] = {
        value,
        _form_encoded(value),
        quote(value, safe="!~*'()"),
        quote(value, safe="!~*'();,/?:@&=+$#"),
        quote(value, safe=""),
        quote(value),
        quote_plus(value),
        json.dumps(value)[1:-1],
    }
    return frozenset(written | {" ".join(form.split()) for form in written})


def masked(text: str, values: Iterable[str], mask: str = MASK) -> str:
    """``text`` with each of ``values`` of a few characters or more replaced by ``mask`` (:data:`MASK` for a value of
    the password manager), in each of the forms a page may write it in, the longest first, so that a value inside
    another is masked with it."""
    forms: set[str] = {
        form
        for value in values
        if len(value) >= _MASKED_FROM_CHARS
        for form in _forms(value)
        if len(form) >= _MASKED_FROM_CHARS
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, mask)
    return text


def _read_back(address: str) -> frozenset[str]:
    """``address`` as it is and with its escapes read back, up to :data:`_READ_BACK_TIMES` times over (an escape of
    an escape), as UTF-8 and as a page of Western European text writes them (Windows-1252, which browsers read Latin-1
    as), with ``+`` as a space and as itself."""
    readings: set[str] = {address}
    latest: set[str] = {address}
    for _ in range(_READ_BACK_TIMES):
        latest = {
            unquote(spelled, encoding=encoding, errors="replace")
            for reading in latest
            for spelled in (reading, reading.replace("+", " "))
            for encoding in ("utf-8", "cp1252")
        } - readings
        if not latest:
            break
        readings |= latest
    return frozenset(readings)


def in_address(address: str, values: Iterable[str]) -> bool:
    """Whether ``address`` carries one of ``values`` of a few characters or more: in any of the forms :func:`masked`
    knows, in the address as it is or once its escapes are read back. Reading them back finds a value as a form or a
    script escaped it, an escape left as it was, a byte of a Western European charset, or escapes of escapes, a few
    times over; a value a script changed otherwise is not found."""
    kept: tuple[str, ...] = tuple(values)
    return any(masked(reading, kept) != reading for reading in _read_back(address))


def masked_json(value: JsonValue, values: Iterable[str], mask: str = MASK) -> JsonValue:
    """``value`` with each of its strings masked as :func:`masked` masks text, its keys among them, all the way down."""
    kept: tuple[str, ...] = tuple(values)
    if isinstance(value, str):
        return masked(value, kept, mask)
    if isinstance(value, list):
        return [masked_json(item, kept, mask) for item in value]
    if isinstance(value, dict):
        return {masked(key, kept, mask): masked_json(item, kept, mask) for key, item in value.items()}
    return value


def mask_error(error: DotError, values: Iterable[str], mask: str = MASK) -> None:
    """Mask ``values`` in ``error``'s words and in its context, which the model reads as well (``str`` of the error),
    and in the address a refused one names."""
    kept: tuple[str, ...] = tuple(values)
    error.message = masked(error.message, kept, mask)
    error.context = {key: masked_json(item, kept, mask) for key, item in error.context.items()}
    if isinstance(error, ForbiddenUrlError):
        error.url = masked(error.url, kept, mask)
