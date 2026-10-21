"""The values a login of the password manager filled into a page (item 493), taken out of what the browser tools of
the same chat give back: a page that shows a code it was given, a script's value, an error that quotes a field. A value
shorter than a few characters is left as it is: masking it would cut ordinary words."""

import json
from collections.abc import Iterable
from urllib.parse import quote, quote_plus

from pydantic import JsonValue

# What stands where a value was.
MASK: str = "[filled from 1Password]"
# The shortest value masked: a one-time code has six digits; a shorter one would match ordinary text.
_MASKED_FROM_CHARS: int = 4


def _forms(value: str) -> set[str]:
    """``value`` as a page or a script may write it back: as it is, in an address (``%21`` for ``!``, ``+`` for a
    space) and inside a JSON string; and each of those with its runs of blanks made one space, as a line of a view is
    written."""
    written: set[str] = {value, quote(value, safe=""), quote(value), quote_plus(value), json.dumps(value)[1:-1]}
    return written | {" ".join(form.split()) for form in written}


def masked(text: str, values: Iterable[str]) -> str:
    """``text`` with each of ``values`` of a few characters or more replaced by :data:`MASK`, in each of the forms a
    page may write it in, the longest first, so that a value inside another is masked with it."""
    forms: set[str] = {
        form
        for value in values
        if len(value) >= _MASKED_FROM_CHARS
        for form in _forms(value)
        if len(form) >= _MASKED_FROM_CHARS
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, MASK)
    return text


def masked_json(value: JsonValue, values: Iterable[str]) -> JsonValue:
    """``value`` with each of its strings masked as :func:`masked` masks text, its keys among them, all the way down."""
    kept: tuple[str, ...] = tuple(values)
    if isinstance(value, str):
        return masked(value, kept)
    if isinstance(value, list):
        return [masked_json(item, kept) for item in value]
    if isinstance(value, dict):
        return {masked(key, kept): masked_json(item, kept) for key, item in value.items()}
    return value
