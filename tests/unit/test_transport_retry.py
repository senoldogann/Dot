"""A TLS read that fails on the wire (``ssl.SSLError``, "bad record mac") is a transport failure (item 107): the
streaming call wraps it, the retry runs again with a reset of the sink, and the last failure is raised."""

import asyncio
import ssl
import typing
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
import pytest

from dotagent.core.errors import ProviderTransportError
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import JsonObject
from dotagent.llm import http as http_module
from dotagent.llm.codec_chat import ChatOptions, chat_codec
from dotagent.llm.connector import post_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import KEEPALIVE_EXPIRY_S, is_retryable_provider_error, transport_error_types
from dotagent.llm.provider_errors import classify_opencode
from dotagent.llm.types import StreamDelta

_CHAT: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=False,
    replay_reasoning=True,
    cache_key_in_body=False,
    reasoning_object=False,
    replay_details=False,
)


def test_a_tls_error_is_a_transport_error() -> None:
    assert ssl.SSLError in transport_error_types()


@pytest.mark.asyncio
async def test_a_tls_read_that_fails_is_retried_and_the_last_failure_raised() -> None:
    calls: list[str] = []
    resets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise ssl.SSLError(1, "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac (_ssl.c:2658)")

    async def sink(delta: StreamDelta) -> None:
        resets.append(delta.kind)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        async def attempt() -> object:
            return await stream_model_reply(
                client,
                "https://go.example/v1/chat",
                "{}",
                {},
                {"url": "x"},
                classify_opencode,
                chat_codec(classify_opencode, _CHAT),
                "m",
                sink,
            )

        policy = RetryPolicy(attempts=3, base_delay_s=0.0, max_delay_s=0.0)
        with pytest.raises(ProviderTransportError) as caught:
            await retry_with_reset(attempt, sink, policy, "test")
    assert is_retryable_provider_error(caught.value)
    assert caught.value.context["error_type"] == "SSLError"
    assert len(calls) == 3
    assert resets == ["reset", "reset"]


class _Wire(httpcore.AsyncNetworkStream):
    """One connection of the test's network: the first one fails its read the way a corrupted TLS record does
    (``ssl.SSLError``), the others answer a JSON object."""

    def __init__(self, backend: "_Network", number: int) -> None:
        self._backend: _Network = backend
        self._number: int = number
        self._answered: bool = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:  # noqa: ASYNC109 - httpcore's signature
        if self._number == 1:
            raise ssl.SSLError(1, "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac (_ssl.c:2658)")
        if self._answered:
            return b""
        self._answered = True
        body: bytes = b'{"ok": true}'
        return b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: %d\r\n\r\n%s" % (len(body), body)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:  # noqa: ASYNC109 - httpcore's signature
        self._backend.written.append((self._number, buffer))

    async def aclose(self) -> None:
        self._backend.closed.append(self._number)

    def get_extra_info(self, info: str) -> object:
        return None


class _Network(httpcore.AsyncNetworkBackend):
    """The network under httpcore's own connection pool: it numbers the connections it opens, and keeps what was written
    to them and which were closed."""

    def __init__(self) -> None:
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.written: list[tuple[int, bytes]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int] | tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.opened.append(len(self.opened) + 1)
        return _Wire(self, self.opened[-1])

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        socket_options: Iterable[tuple[int, int, int] | tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _PoolTransport(httpx.AsyncBaseTransport):
    """httpx over httpcore's connection pool on the test's network (what ``httpx.AsyncHTTPTransport`` does over the
    real one)."""

    def __init__(self, network: _Network) -> None:
        self._pool: httpcore.AsyncConnectionPool = httpcore.AsyncConnectionPool(
            network_backend=network, keepalive_expiry=KEEPALIVE_EXPIRY_S
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        stream = request.stream
        assert isinstance(stream, httpx.AsyncByteStream)
        sent: httpcore.Request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=stream,
            extensions=request.extensions,
        )
        answered: httpcore.Response = await self._pool.handle_async_request(sent)
        assert isinstance(answered.stream, typing.AsyncIterable)
        return httpx.Response(
            status_code=answered.status,
            headers=answered.headers,
            content=b"".join([part async for part in answered.stream]),
            extensions=answered.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


@pytest.mark.asyncio
async def test_a_retry_after_a_tls_error_goes_out_on_a_fresh_connection_with_the_same_prompt() -> None:
    """Item 1314: a connection whose read failed on the wire is dropped by httpcore and not handed to the retry, which
    opens another and sends the same request again (a prompt of 28 to 51k characters each time)."""
    network: _Network = _Network()
    prompt: str = "p" * 30_000
    async with httpx.AsyncClient(transport=_PoolTransport(network)) as client:
        answered: JsonObject = await post_json(
            client,
            "http://provider.test/v1/chat",
            {},
            {"prompt": prompt},
            classify_opencode,
            RetryPolicy(attempts=3, base_delay_s=0.0, max_delay_s=0.0),
            "test",
        )
    assert answered == {"ok": True}
    assert network.opened == [1, 2], "the retry did not reuse the broken connection"
    assert 1 in network.closed, "the broken connection was dropped"
    sent: dict[int, bytes] = {}
    for number, chunk in network.written:
        sent[number] = sent.get(number, b"") + chunk
    assert prompt.encode() in sent[1] and prompt.encode() in sent[2], "each try carried the whole prompt"


def test_an_idle_connection_to_a_provider_is_kept_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float | None] = []
    real = httpx.Limits

    def limits(**kwargs: Any) -> httpx.Limits:
        seen.append(kwargs.get("keepalive_expiry"))
        return real(**kwargs)

    monkeypatch.setattr(http_module.httpx, "Limits", limits)
    http_module.build_client(5.0, 60.0)
    assert seen == [30.0] and KEEPALIVE_EXPIRY_S == 30.0
