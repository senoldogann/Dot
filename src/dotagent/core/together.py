"""Awaiting independent work at once."""

import asyncio
from collections.abc import Sequence


async def await_together(tasks: Sequence[asyncio.Task[object]]) -> None:
    """Wait until every one of ``tasks`` is done. The first failure cancels the others, waits for them to end, and is
    raised as it was (not wrapped in an exception group), so that a caller meets the same error as when it awaited the
    work one after the other; each task's result is then read with ``task.result()``."""
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
