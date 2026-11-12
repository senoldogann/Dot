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
    """What one drain of the page returned.

    ``dropped`` counts the entries the page's bounded buffer had to drop since the last drain.
    """

    captions: tuple[CaptionSeen, ...]
    chat: tuple[ChatSeen, ...]
    dropped: int = Field(ge=0)


class MeetingLine(FrozenModel):
    """One kept line of a meeting: what a speaker said (caption) or typed (chat). Names are unverified."""

    kind: Literal["caption", "chat"]
    speaker: str = Field(max_length=MAX_SPEAKER_CHARS)
    text: str = Field(max_length=MAX_TEXT_CHARS)
    at: float
