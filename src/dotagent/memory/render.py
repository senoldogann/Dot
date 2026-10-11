"""How notes read to the model: one line each, led by the id it needs to update or forget the note."""

import time

from dotagent.memory.service import REFLECTION_SOURCE
from dotagent.store.models import MemoryRecord


def _day(stamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def render_note(record: MemoryRecord) -> str:
    """``[mem_x] text #tag #tag (the user wrote it, 2026-10-06)``: who wrote the note and when it last changed, so
    the model can tell a standing statement of the user from its own note and an old note from a new one."""
    tags: str = "".join(f" #{tag}" for tag in record.tags)
    author: str = (
        "the user wrote it"
        if record.source_run is None
        else "your nightly reflection wrote it"
        if record.source_run == REFLECTION_SOURCE
        else "you saved it"
    )
    return f"[{record.id}] {record.content}{tags} ({author}, {_day(record.updated_at)})"
