"""The forms a secret takes when a tool, a page, a script or a log writes it back, and the one place that masks them
(item 1292).

A secret Dot holds is masked wherever it stands: as it is, and as an address, a form, a JSON string, a page of HTML or
a base64 text carries it. Every mask of a held value (the text a tool returns, an event, the tail of a log, a note, a
page a login filled) goes through :func:`mask_values`, so that no place knows fewer forms than another. This module
imports nothing of Dot's, so that the core and the vault can both use it."""

import base64
import html
import json
import string
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import quote, quote_plus

# What a browser leaves as it is when it writes a form's field into an address (the URL standard's form encoding).
_FORM_KEPT: frozenset[str] = frozenset(string.ascii_letters + string.digits + "*-._")
# The values whose forms are kept, the most recently masked: every cookie value of every site a script ran in passes
# through here, and session cookies change, so a cache without a bound grew for as long as the daemon ran (item 730).
_FORMS_KEPT: int = 2048
# The sets of values whose masked forms are kept compiled, so that a long output is masked with no work but the
# search: a run holds the same few secrets for as long as it goes.
_SETS_KEPT: int = 64
# Where a value written after 0, 1 or 2 bytes of other text starts to say the value alone in base64: the characters
# before hold bits of the bytes before it.
_BASE64_SKIP: tuple[int, ...] = (0, 2, 3)
# The shortest base64 form masked: a shorter run of its letters stands by chance in the base64 of any picture a page
# carries (the pieces of a one-time code's base64 that sit inside a longer text are shorter, and are not masked).
_BASE64_MIN_CHARS: int = 8


def _form_encoded(value: str) -> str:
    """``value`` as a browser writes a form's field into an address: letters, digits and ``*-._`` as they are, a
    space as ``+``, every other byte of its UTF-8 as ``%XX``."""
    return "".join(
        chr(byte) if chr(byte) in _FORM_KEPT else "+" if byte == 0x20 else f"%{byte:02X}"
        for byte in value.encode("utf-8")
    )


def _base64_forms(value: str) -> set[str]:
    """``value`` as base64 writes it, standard and URL-safe, padded and not: on its own, and inside a longer text,
    where the bytes before it change the characters that take its first bytes and the bytes after it the last
    ones, so that each of the three alignments gives the characters of the value alone, and the whole of it where
    the text ends with it."""
    data: bytes = value.encode("utf-8")
    written: set[str] = set()
    for before, skip in enumerate(_BASE64_SKIP):
        shifted: bytes = bytes(before) + data
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            padded: str = encode(shifted).decode("ascii")
            plain: str = padded.rstrip("=")
            # A last group of one or two bytes is written with the bits of the byte that follows it in a longer text,
            # so only its whole characters hold the value alone.
            inside: str = plain if len(shifted) % 3 == 0 else plain[:-1]
            written.update(form[skip:] for form in (inside, plain, padded) if len(form) - skip >= _BASE64_MIN_CHARS)
    return written


def _spelled(value: str) -> set[str]:
    """``value`` as a text writes it that is not base64."""
    return {
        value,
        _form_encoded(value),
        quote(value, safe="!~*'()"),
        quote(value, safe="!~*'();,/?:@&=+$#"),
        quote(value, safe=""),
        quote(value),
        quote_plus(value),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        json.dumps(value)[1:-1].replace("/", "\\/"),
        json.dumps(json.dumps(value)[1:-1])[1:-1],
        html.escape(value),
        html.escape(value, quote=False),
    }


@lru_cache(maxsize=_FORMS_KEPT)
def forms_of(value: str) -> frozenset[str]:
    """``value`` as a page, a script, a tool or a log may write it back: as it is; in an address, as a form, a script's
    ``encodeURIComponent`` and ``encodeURI``, and Python write it (``%21`` or ``!``, ``+`` or ``%20`` for a space);
    inside a JSON string (with a character beyond ASCII escaped or not, a slash escaped or not, and escaped again
    where a log line carries a JSON text as a string); as HTML escapes it;
    as base64 (standard and URL-safe, padded and not, at each of the three alignments); and each of the others with
    its runs of blanks made one space, as a line of a view is written."""
    written: set[str] = _spelled(value)
    return frozenset(written | {" ".join(form.split()) for form in written} | _base64_forms(value))


@lru_cache(maxsize=_SETS_KEPT)
def _ordered(values: frozenset[str], min_chars: int) -> tuple[str, ...]:
    """Every form of every value of ``values`` of ``min_chars`` characters or more, each once, the longest first, so
    that a value inside another is masked with it."""
    forms: set[str] = {
        form for value in values if len(value) >= min_chars for form in forms_of(value) if len(form) >= min_chars
    }
    return tuple(sorted(forms, key=lambda form: (-len(form), form)))


def held_forms(values: Iterable[str], min_chars: int) -> tuple[str, ...]:
    """The forms :func:`mask_values` masks for ``values``, the longest first."""
    return _ordered(frozenset(values), min_chars)


def mask_values(text: str, values: Iterable[str], mask: str, min_chars: int) -> str:
    """``text`` with each of ``values`` of ``min_chars`` characters or more replaced by ``mask`` in each of the forms
    of :func:`forms_of`, the longest first. A shorter value is left alone: masking it would cut ordinary words. The
    forms of a set of values are made once and kept, so a long text costs one search for each."""
    for form in _ordered(frozenset(values), min_chars):
        text = text.replace(form, mask)
    return text
