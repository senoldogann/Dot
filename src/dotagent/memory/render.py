"""How notes read to the model: one line each, led by the id it needs to update or forget the note."""

import time

from dotagent.memory.service import REFLECTION_SOURCE
from dotagent.store.models import MemoryRecord


def _day(stamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def render_note(record: MemoryRecord) -> str:
    """``[mem_x] text #tag #tag (the user wrote it, 2026-10-06)``: who wrote the note and when it last changed, so
    the model can tell a standing statement of the user from its own note and an old note from a new one; a note saved
    for a while says until when it holds (``you saved it, 2026-10-06, holds until 2026-10-20``, item 306)."""
    tags: str = "".join(f" #{tag}" for tag in record.tags)
    author: str = (
        "the user wrote it"
        if record.source_run is None
        else "your nightly reflection wrote it"
        if record.source_run == REFLECTION_SOURCE
        else "you saved it"
    )
    until: str = "" if record.expires_at is None else f", holds until {_day(record.expires_at)}"
    return f"[{record.id}] {record.content}{tags} ({author}, {_day(record.updated_at)}{until})"
