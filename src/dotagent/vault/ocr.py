"""Reading a card's photo on this Mac (item 621), for the Card dialog's "Upload a photo".

The picture is read from memory by Vision, the text recognition of macOS itself, which PyObjC's core reaches without a
package of its own. It is written to no file, sent to no model and to no service, and neither it nor the text read from
it is logged. What comes back is a suggestion for the dialog's fields, which the owner reads and corrects before the
card is saved: the number (one that passes its check digit), the expiry and the name."""

import asyncio
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.vault import cards

# The biggest picture read, in bytes: a phone's photo is a few megabytes.
MOST_BYTES: int = 12_000_000
_VISION: str = "/System/Library/Frameworks/Vision.framework"
# Vision's recognition level "accurate", and the argument number of the error that ``performRequests:error:`` fills.
_ACCURATE: int = 0
_ERROR_ARGUMENT: int = 3
_NUMBER: re.Pattern[str] = re.compile(r"(?<![0-9])(?:[0-9][ \-]?){11,18}[0-9](?![0-9])")
# A line that is a number with the letters an eye or a recognizer takes for digits.
_LOOKS_LIKE_A_NUMBER: re.Pattern[str] = re.compile(r"[0-9OoIlSB \-]{14,30}")
_CONFUSED: dict[int, str] = {ord("O"): "0", ord("o"): "0", ord("I"): "1", ord("l"): "1", ord("S"): "5", ord("B"): "8"}
_EXPIRY: re.Pattern[str] = re.compile(r"(?<![0-9])(0[1-9]|1[0-2])\s*[/\-]\s*([0-9]{4}|[0-9]{2})(?![0-9])")
_EXPIRY_CUE: re.Pattern[str] = re.compile(r"(?i)valid|thru|through|expir|exp\.?\b|good|son kul|s\.k\.t")
# The capital letters names are printed in, the accented and the Turkish ones among them.
_CAPITAL: str = "A-Z\N{LATIN CAPITAL LETTER A WITH GRAVE}-\N{LATIN SMALL LETTER LONG S}"
_WORD: str = rf"[{_CAPITAL}][{_CAPITAL}.'\-]*"
_NAME: re.Pattern[str] = re.compile(rf"{_WORD}(?: {_WORD}){{1,3}}")
# Words printed on a card that are not a name.
_NOT_A_NAME: frozenset[str] = frozenset(
    {
        "VALID", "THRU", "THROUGH", "GOOD", "DEBIT", "CREDIT", "BANK", "BANKASI", "CARD", "KART", "VISA", "MASTERCARD",
        "AMERICAN", "EXPRESS", "DISCOVER", "PLATINUM", "GOLD", "CLASSIC", "BUSINESS", "MEMBER", "SINCE", "EXPIRES",
        "DATE", "ELECTRON", "MAESTRO", "WORLD", "TROY", "DINERS", "CLUB", "INTERNATIONAL", "CONTACTLESS", "PREPAID",
        "SIGNATURE", "INFINITE", "STANDARD", "AUTHORIZED", "NOT", "TRANSFERABLE",
    }
)  # fmt: skip


class CardPhoto(FrozenModel):
    """What was read in a card's photo: its number as digits, its expiry as ``MM/YY`` and the name printed on it, each
    ``None`` when none was found."""

    number: str | None
    expiry: str | None
    name: str | None


def _numbers_in(lines: Sequence[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        text: str = line.translate(_CONFUSED) if _LOOKS_LIKE_A_NUMBER.fullmatch(line.strip()) else line
        for match in _NUMBER.finditer(text):
            digits: str | None = cards.digits_of(match.group(0))
            if digits is not None and cards.number_problem(digits) is None:
                found.append(digits)
    return found


def _expiry_in(lines: Sequence[str], today: date) -> str | None:
    """The expiry the card prints: the one on a line that says valid thru or expires, else the first not passed."""
    candidates: list[tuple[bool, str]] = []
    for line in lines:
        cued: bool = _EXPIRY_CUE.search(line) is not None
        for match in _EXPIRY.finditer(line):
            expiry: cards.Expiry | None = cards.parse_expiry(f"{match.group(1)}/{match.group(2)}")
            if expiry is not None and cards.expiry_problem(cards.render_expiry(expiry), today) is None:
                candidates.append((cued, cards.render_expiry(expiry)))
    for cued, written in candidates:
        if cued:
            return written
    return candidates[0][1] if candidates else None


def _name_in(lines: Sequence[str]) -> str | None:
    """The line of capital letters that looks like a name, the last on the card: it is printed below the number."""
    names: list[str] = []
    for line in lines:
        text: str = " ".join(line.split())
        if _NAME.fullmatch(text) is None:
            continue
        if any(word.strip(".'-") in _NOT_A_NAME for word in text.split()):
            continue
        names.append(text)
    return names[-1] if names else None


def parse_card_text(lines: Sequence[str], today: date) -> CardPhoto:
    """The number, the expiry and the name that the lines of text of a card hold, as far as they hold them."""
    numbers: list[str] = _numbers_in(lines)
    return CardPhoto(
        number=numbers[0] if numbers else None,
        expiry=_expiry_in(lines, today),
        name=_name_in(lines),
    )


def _recognize(image: bytes) -> list[str]:
    """The lines of text Vision reads in ``image``, in the order it finds them. Blocking."""
    try:
        import objc
        from Foundation import NSData
    except ImportError as error:
        raise InvalidValueError("this Mac cannot read the text in a picture", {}) from error
    loaded: dict[str, Any] = {}
    objc.loadBundle("Vision", loaded, bundle_path=_VISION)
    objc.registerMetaDataForSelector(
        b"VNImageRequestHandler",
        b"performRequests:error:",
        {"arguments": {_ERROR_ARGUMENT: {"type_modifier": objc._C_OUT}}},
    )
    handler: Any = (
        objc.lookUpClass("VNImageRequestHandler")
        .alloc()
        .initWithData_options_(NSData.dataWithBytes_length_(image, len(image)), {})
    )
    request: Any = objc.lookUpClass("VNRecognizeTextRequest").alloc().init()
    request.setRecognitionLevel_(_ACCURATE)
    # A number is no word: a recognizer that corrects its words toward a language would change its digits.
    request.setUsesLanguageCorrection_(False)
    done, failure = handler.performRequests_error_([request], None)
    if not done:
        raise InvalidValueError("that does not look like a picture that can be read: try a sharper photo", {}) from (
            None if failure is None else RuntimeError(type(failure).__name__)
        )
    lines: list[str] = []
    for observation in request.results() or []:
        candidates: Any = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return lines


async def read_card_photo(image: bytes, today: date) -> CardPhoto:
    """Read the number, the expiry and the name in a card's photo. The picture is dropped when this returns."""
    if not image or len(image) > MOST_BYTES:
        raise InvalidValueError(
            f"the picture must be a photo of up to {MOST_BYTES // 1_000_000} MB", {"bytes": len(image)}
        )
    lines: list[str] = await asyncio.to_thread(_recognize, image)
    return parse_card_text(lines, today)
