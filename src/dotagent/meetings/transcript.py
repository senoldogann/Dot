"""Caption snapshots and chat messages to meeting lines. Pure: no clock, no page, no store."""

import unicodedata

from pydantic import Field

from dotagent.core.types import FrozenModel
from dotagent.meetings.types import (
    MAX_SPEAKER_CHARS,
    MAX_TEXT_CHARS,
    CaptionSeen,
    ChatSeen,
    MeetingLine,
)

_CUT: str = "…"


def clean_words(text: str, most: int) -> str:
    """The text without control characters (newlines and tabs become a space), trimmed, at most ``most`` characters.

    A longer text is cut: its head stays and the last character is "…", so a reader sees that it was cut.
    """
    spaced: str = "".join(" " if ch in "\n\r\t" else ch for ch in text)
    kept: str = "".join(ch for ch in spaced if unicodedata.category(ch)[0] != "C")
    flat: str = " ".join(kept.split())
    if len(flat) <= most:
        return flat
    return flat[: most - 1].rstrip() + _CUT


class CaptionBook(FrozenModel):
    """The caption blocks still open (they may be rewritten), by key, when each last changed, and the words of
    each committed block still on the page (so that a block left on the page is not emitted again)."""

    open: dict[str, CaptionSeen] = Field(default_factory=dict)
    changed_at: dict[str, float] = Field(default_factory=dict)
    done: dict[str, str] = Field(default_factory=dict)


def _caption_line(seen: CaptionSeen) -> MeetingLine | None:
    text: str = clean_words(seen.text, MAX_TEXT_CHARS)
    if not text:
        return None
    return MeetingLine(kind="caption", speaker=clean_words(seen.speaker, MAX_SPEAKER_CHARS), text=text, at=seen.at)


def fold_captions(
    book: CaptionBook, seen: tuple[CaptionSeen, ...], now: float, settle_s: float
) -> tuple[CaptionBook, tuple[MeetingLine, ...]]:
    """Merge the blocks the page shows now (all of them, in page order) into the book.

    A block's newest text replaces its earlier text. A block becomes a line once its text has not changed for
    ``settle_s`` seconds, or when it is no longer on the page. A committed block leaves the book, so it is never
    emitted twice; a block with no words is not a line. Lines come out in the order the blocks first appeared.
    """
    latest: dict[str, CaptionSeen] = {}
    for item in seen:
        latest[item.key] = item
    opened: dict[str, CaptionSeen] = dict(book.open)
    changed: dict[str, float] = dict(book.changed_at)
    done: dict[str, str] = {key: text for key, text in book.done.items() if key in latest}
    order: list[str] = list(book.open)
    for key, item in latest.items():
        if key in done:
            if item.text.startswith(done[key]):
                # A committed block the speaker went on with: only the new words are a new block.
                item = item.model_copy(update={"text": item.text[len(done[key]) :]})  # noqa: PLW2901
                if not item.text.strip():
                    continue
            else:
                del done[key]
        if key not in opened:
            order.append(key)
            changed[key] = now
        elif opened[key].text != item.text or opened[key].speaker != item.speaker:
            changed[key] = now
        opened[key] = item
    lines: list[MeetingLine] = []
    for key in order:
        gone: bool = key not in latest
        settled: bool = now - changed[key] >= settle_s
        if not (gone or settled):
            continue
        line: MeetingLine | None = _caption_line(opened[key])
        if line is not None:
            lines.append(line)
        if not gone:
            done[key] = done.get(key, "") + opened[key].text
        del opened[key]
        del changed[key]
    return CaptionBook(open=opened, changed_at=changed, done=done), tuple(lines)


def new_chat(known: frozenset[str], seen: tuple[ChatSeen, ...]) -> tuple[frozenset[str], tuple[MeetingLine, ...]]:
    """Each chat message once, by key: the keys now known, and the lines of the keys that were not known."""
    keys: set[str] = set(known)
    lines: list[MeetingLine] = []
    for item in seen:
        if item.key in keys:
            continue
        keys.add(item.key)
        text: str = clean_words(item.text, MAX_TEXT_CHARS)
        if text:
            lines.append(
                MeetingLine(kind="chat", speaker=clean_words(item.sender, MAX_SPEAKER_CHARS), text=text, at=item.at)
            )
    return frozenset(keys), tuple(lines)
