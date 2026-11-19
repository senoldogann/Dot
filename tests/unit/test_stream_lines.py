"""A stream is read line by line where a line feed, a carriage return or both end a line, and nowhere else (item 1243):
httpx's ``aiter_lines`` and ``str.splitlines`` also ended a line at U+0085, U+2028 and U+2029, which JSON leaves raw in
a string, so a model's answer, Dot's own event or a Claude Code transcript that quoted one was cut in two."""

import json
from collections.abc import AsyncIterator

import httpx

from dotagent.core.text import file_lines, stream_lines
from dotagent.llm.http import iter_ndjson, iter_sse

_BREAKERS: str = "\u0085\u2028\u2029"


async def _pieces(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


async def _lines(*chunks: str) -> list[str]:
    return [line async for line in stream_lines(_pieces(*chunks))]


async def test_only_a_line_feed_or_a_carriage_return_ends_a_line_wherever_the_pieces_break() -> None:
    assert await _lines(f"a{_BREAKERS}b\n", "c\r", "\nd\re", "f") == [f"a{_BREAKERS}b", "c", "d", "ef"]
    assert await _lines("x\r\n\r\n", "") == ["x", ""]
    assert await _lines("") == []
    assert await _lines("one\n") == ["one"]


async def test_an_event_whose_text_holds_a_line_separator_comes_through_whole() -> None:
    text: str = f"done{_BREAKERS}really"
    event: str = json.dumps({"text": text}, ensure_ascii=False)
    sse = httpx.Response(200, content=f"event: delta\ndata: {event}\n\n".encode())
    found = [event async for event in iter_sse(sse)]
    assert [(event.event, json.loads(event.data)["text"]) for event in found] == [("delta", text)]
    ndjson = httpx.Response(200, content=f"{event}\r\n\n{event}\n".encode())
    assert [line["text"] async for line in iter_ndjson(ndjson, {})] == [text, text]


def test_a_transcript_line_that_holds_a_line_separator_stays_one_line() -> None:
    line: str = json.dumps({"command": f"echo a{_BREAKERS}b"}, ensure_ascii=False)
    assert file_lines(f"{line}\r\n{line}\n") == [line, line]
    assert file_lines("") == []
