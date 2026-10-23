"""The body of a web answer is read as it comes and inflated with a limit on each step (item 554): a few hundred bytes
of gzip inside gzip are not gigabytes in one call, whatever the cap; gzip and deflate are read, once, and an answer in
other codings or in more than one is refused. The same reading serves the SearXNG instance."""

import gzip
import threading
import tracemalloc
import zlib
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import WebError
from dotagent.web import client as web_client
from dotagent.web.client import WebClient
from dotagent.web.search import search_searxng

_URL: str = "https://pages.example/doc"
_CAP: int = 1000
_ZEROS: int = 64 * 1024 * 1024
# What the reading may hold at its most: the pieces of the body and the one it cuts, never what the body inflates to.
_PEAK_BYTES: int = 8 * 1024 * 1024


class _Chunks(httpx.AsyncByteStream):
    """A body that arrives in pieces, as a server sends it."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks: list[bytes] = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _client(monkeypatch: pytest.MonkeyPatch, coding: str | None, chunks: list[bytes]) -> WebClient:
    def answer(request: httpx.Request) -> httpx.Response:
        headers: dict[str, str] = {"Content-Type": "text/plain"}
        if coding is not None:
            headers["Content-Encoding"] = coding
        return httpx.Response(200, headers=headers, stream=_Chunks(chunks))

    def fake_transport(backend: object, limits: httpx.Limits) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(answer)

    monkeypatch.setattr(web_client, "_CheckedTransport", fake_transport)
    return WebClient(load_default_config().web, (), ())


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coding", "pack"),
    [
        (None, lambda data: data),
        ("identity", lambda data: data),
        ("gzip", gzip.compress),
        ("GZIP", gzip.compress),
        ("x-gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("deflate", _raw_deflate),
        ("gzip, identity", gzip.compress),
    ],
)
async def test_a_body_in_one_coding_is_read_whole_however_it_comes(
    monkeypatch: pytest.MonkeyPatch, coding: str | None, pack: object
) -> None:
    text: bytes = b"A page of words. " * 400
    packed: bytes = pack(text)  # type: ignore[operator]
    pieces: list[bytes] = [packed[start : start + 97] for start in range(0, len(packed), 97)]
    client: WebClient = _client(monkeypatch, coding, pieces)
    try:
        document = await client.get(_URL, len(text) + 10)
    finally:
        await client.aclose()
    assert document.body == text and not document.truncated


@pytest.mark.asyncio
async def test_a_body_over_the_cap_is_cut_at_it_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    text: bytes = bytes(range(256)) * 40
    client: WebClient = _client(monkeypatch, "gzip", [gzip.compress(text)])
    try:
        document = await client.get(_URL, _CAP)
    finally:
        await client.aclose()
    assert document.body == text[:_CAP] and document.truncated


@pytest.mark.asyncio
async def test_a_body_that_inflates_to_far_more_than_the_cap_is_not_inflated_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One piece of a few tens of kilobytes that inflates to 64 MB: gzip's ratio. The cap is a thousand bytes, and the
    reading holds about as much, whatever the piece would have inflated to."""
    packed: bytes = gzip.compress(bytes(_ZEROS))
    assert len(packed) < 200_000
    client: WebClient = _client(monkeypatch, "gzip", [packed])
    tracemalloc.start()
    try:
        document = await client.get(_URL, _CAP)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        await client.aclose()
    assert document.body == bytes(_CAP) and document.truncated
    assert peak < _PEAK_BYTES, peak


@pytest.mark.asyncio
@pytest.mark.parametrize("coding", ["gzip, gzip", "gzip,deflate", "deflate, gzip", "gzip, gzip, gzip"])
async def test_a_body_in_more_than_one_coding_is_refused_before_any_of_it_is_inflated(
    monkeypatch: pytest.MonkeyPatch, coding: str
) -> None:
    """The bomb the audit found: 484 bytes sent as ``gzip, gzip`` inflated to 200 MB through a cap of a thousand."""
    bomb: bytes = gzip.compress(gzip.compress(bytes(_ZEROS)))
    assert len(bomb) < 2_000
    client: WebClient = _client(monkeypatch, coding, [bomb])
    tracemalloc.start()
    try:
        with pytest.raises(WebError, match="compressed in a way the web tools do not read") as refused:
            await client.get(_URL, _CAP)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        await client.aclose()
    assert refused.value.context["content_encoding"] == coding
    assert peak < _PEAK_BYTES, peak


@pytest.mark.asyncio
@pytest.mark.parametrize("coding", ["br", "zstd", "compress", "gzip, br"])
async def test_a_coding_the_web_tools_cannot_read_is_refused_and_not_handed_over_as_text(
    monkeypatch: pytest.MonkeyPatch, coding: str
) -> None:
    client: WebClient = _client(monkeypatch, coding, [b"\x1b\x00garbage"])
    try:
        with pytest.raises(WebError, match="compressed in a way the web tools do not read"):
            await client.get(_URL, _CAP)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_body_that_is_not_the_coding_it_claims_is_an_error_of_the_web_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: WebClient = _client(monkeypatch, "gzip", [b"this is not gzip at all"])
    try:
        with pytest.raises(WebError, match="could not be decompressed"):
            await client.get(_URL, _CAP)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_the_request_names_the_codings_it_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["accept-encoding"])
        return httpx.Response(200, stream=_Chunks([b"ok"]), headers={"Content-Type": "text/plain"})

    monkeypatch.setattr(web_client, "_CheckedTransport", lambda backend, limits: httpx.MockTransport(answer))
    client: WebClient = WebClient(load_default_config().web, (), ())
    try:
        await client.get(_URL, _CAP)
    finally:
        await client.aclose()
    assert seen == ["gzip, deflate"]


class _Instance(BaseHTTPRequestHandler):
    """A SearXNG instance whose answer is a bomb: gzip inside gzip, a few hundred bytes."""

    bomb: ClassVar[bytes] = b""

    def do_GET(self) -> None:
        body: bytes = self.bomb
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip, gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def bomb_instance() -> Iterator[str]:
    # Made before anything is measured: the 64 MB it inflates to is the test's, not the reading's.
    _Instance.bomb = gzip.compress(gzip.compress(bytes(_ZEROS)))
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Instance)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_a_searxng_answer_that_inflates_to_gigabytes_is_refused_too(bomb_instance: str) -> None:
    client: WebClient = WebClient(load_default_config().web, (), ())
    tracemalloc.start()
    try:
        with pytest.raises(WebError, match="compressed in a way the web tools do not read"):
            await search_searxng(client, bomb_instance, "reaktor", 5)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        await client.aclose()
    assert peak < _PEAK_BYTES, peak
