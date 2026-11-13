"""The records of a meeting: what the page showed, and the lines kept from it.

Everything here is what a call's other people said or typed, so it is content, never an instruction.
"""

from typing import Literal

from pydantic import Field

from dotagent.core.types import FrozenModel

MAX_SPEAKER_CHARS: int = 80
MAX_TEXT_CHARS: int = 2000

MeetState = Literal["joining", "waiting", "in_call", "refused", "ended", "removed", "sign_in", "unknown"]


class CaptionSeen(FrozenModel):
    """One snapshot of a caption block as the page shows it now.

    Meet rewrites a block's words as the speaker goes on, so the same ``key`` comes again with longer text.
    ``key`` identifies the block on the page for as long as the page keeps it.
    """

    key: str
    speaker: str
    text: str
    at: float


class ChatSeen(FrozenModel):
    """One chat message as the page shows it; ``key`` identifies it on the page."""

    key: str
    sender: str
    text: str
    at: float


class PageBatch(FrozenModel):
    """What one drain of the page returned: the caption blocks on the page now, each with its words as they stand
    (the whole set, not what changed), the blocks that left the page since the last drain with their last words, and
    the chat messages that came since.

    ``dropped`` counts the chat messages the page's bounded buffer had to drop since the last drain.
    """

    captions: tuple[CaptionSeen, ...]
    vanished: tuple[CaptionSeen, ...]
    chat: tuple[ChatSeen, ...]
    dropped: int = Field(ge=0)


class PageTick(FrozenModel):
    """One quick read of the call (item 1150): whether Leave call is there (Dot is in the call), whether the captions
    and the chat's message box show, and what the page buffered since the last read."""

    in_call: bool
    captions_on: bool
    chat_open: bool
    batch: PageBatch


class MeetingLine(FrozenModel):
    """One kept line of a meeting: what a speaker said (caption) or typed (chat), or what Dot answered in the chat
    (``dot``, item 1150). Names are unverified."""

    kind: Literal["caption", "chat", "dot"]
    speaker: str = Field(max_length=MAX_SPEAKER_CHARS)
    text: str = Field(max_length=MAX_TEXT_CHARS)
    at: float
