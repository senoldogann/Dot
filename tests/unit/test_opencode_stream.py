"""OpenCode's event stream (item 351): a line that is not a JSON object ends the stream as a broken one, which the run
reads and reports. It ended the reader of the stream without a word before, and the run waited for it for good."""

import asyncio

import httpx

from dotagent.core.errors import OpenCodeError
from dotagent.opencode.run import _Broke, _Ended, _Item, _pump_events
from dotagent.opencode.server import _events


async def _pumped(body: bytes) -> list[_Item]:
    queue: asyncio.Queue[_Item] = asyncio.Queue()
    await _pump_events(_events(httpx.Response(200, content=body)), queue)
    return [queue.get_nowait() for _ in range(queue.qsize())]


def test_a_line_that_is_not_json_breaks_the_stream_with_its_reason() -> None:
    items: list[_Item] = asyncio.run(_pumped(b'data: {"type": "session.idle"}\n\ndata: {not json\n\n'))
    assert items[0] == {"type": "session.idle"}
    assert len(items) == 2 and isinstance(items[1], _Broke)
    assert isinstance(items[1].error, OpenCodeError) and items[1].error.context["line"] == "{not json"


def test_a_stream_that_ends_well_says_so() -> None:
    items: list[_Item] = asyncio.run(_pumped(b'data: {"type": "session.idle"}\n\n'))
    assert items[0] == {"type": "session.idle"} and isinstance(items[1], _Ended)
