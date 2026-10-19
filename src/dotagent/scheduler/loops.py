"""A chat's loop (item 281): ``/loop 30m <message>`` in a chat asks Dot that message again on a rhythm, in that chat, as
one of its schedules (item 291), until the owner stops it or a run of it deletes it once its work is done for good.
A loop is a schedule like any other: the Schedules page lists it, and its runs start as the chat's follow-ups do."""

import re

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel

# The rhythms a loop keeps exactly with a cron expression: minutes that divide an hour, hours that divide a day. Five
# minutes is the scheduler's shortest gap ([scheduler] min_interval_s).
_MINUTES: tuple[int, ...] = (5, 10, 15, 20, 30)
_HOURS: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12)
_RHYTHM: re.Pattern[str] = re.compile(r"(\d+)\s*([mh])")
_NAME_CHARS: int = 40


class LoopRhythm(FrozenModel):
    """How often a loop asks again: as its schedule's cron expression, and in words."""

    cron: str
    words: str


def loop_rhythm(every: str) -> LoopRhythm:
    """The rhythm ``every`` names (``30m``, ``2h``), or :class:`InvalidValueError` for one a cron expression cannot keep
    exactly or the scheduler does not allow."""
    found: re.Match[str] | None = _RHYTHM.fullmatch(every.strip().lower())
    count: int = int(found.group(1)) if found is not None else 0
    if found is not None and found.group(2) == "m" and count in _MINUTES:
        return LoopRhythm(cron=f"*/{count} * * * *", words=f"every {count} minutes")
    if found is not None and found.group(2) == "h" and count in _HOURS:
        return LoopRhythm(
            cron="0 * * * *" if count == 1 else f"0 */{count} * * *",
            words="every hour" if count == 1 else f"every {count} hours",
        )
    raise InvalidValueError(
        "a loop asks again every 5, 10, 15, 20 or 30 minutes (5m … 30m), or every 1, 2, 3, 4, 6, 8 or 12 hours "
        "(1h … 12h)",
        {"every": every},
    )


def loop_name(text: str) -> str:
    """The name of the loop's schedule: its message's first line, short."""
    first: str = " ".join(text.strip().splitlines()[0].split()) if text.strip() else ""
    return f"Loop: {first if len(first) <= _NAME_CHARS else first[: _NAME_CHARS - 1] + '…'}"


def loop_goal(text: str, rhythm: LoopRhythm, name: str) -> str:
    """What each run of the loop is asked: the message, and how the loop ends."""
    return (
        f"{text.strip()}\n\n(This message is a loop of this chat: it is asked again {rhythm.words}. When what it "
        f'asks for is done for good, delete the schedule "{name}" (schedule_list gives its id) with schedule_delete, '
        "and the loop ends.)"
    )
