"""The values a login of the password manager filled into a page (item 493), taken out of what the browser tools of
the same chat give back: a page that shows a code it was given, a script's value, an error that quotes a field. A value
shorter than a few characters is left as it is: masking it would cut ordinary words."""

from collections.abc import Iterable

# What stands where a value was.
MASK: str = "[filled from 1Password]"
# The shortest value masked: a one-time code has six digits; a shorter one would match ordinary text.
_MASKED_FROM_CHARS: int = 4


def masked(text: str, values: Iterable[str]) -> str:
    """``text`` with each of ``values`` of a few characters or more replaced by :data:`MASK`, the longest first, so
    that a value inside another is masked with it."""
    for value in sorted((one for one in values if len(one) >= _MASKED_FROM_CHARS), key=len, reverse=True):
        text = text.replace(value, MASK)
    return text
