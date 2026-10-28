"""Whether a model's server can be connected to (item 769), on servers of this Mac: a port that listens can, a closed
one cannot; and a call whose connection broke to a server that cannot be connected to is told so at once instead of
being retried in silence."""

import asyncio
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from dotagent.core.errors import ProviderTransportError
from dotagent.core.retry import RetryPolicy
from dotagent.llm import reach
from dotagent.llm.connector import post_json, retry_with_reset
from dotagent.llm.http import is_retryable_provider_error, wrap_transport_error
from dotagent.llm.provider_errors import classify_generic
from dotagent.llm.reach import OFFLINE, cannot_connect, host_of, out_of_reach, reaches
from dotagent.llm.types import ModelReply, StreamDelta

_POLICY: RetryPolicy = RetryPolicy(attempts=4, base_delay_s=0.0, max_delay_s=0.0)


def _closed_port() -> int:
    """A port of this Mac nothing listens on: one the system just gave out and took back."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest_asyncio.fixture
async def listening() -> AsyncIterator[str]:
    """The address of a server of this Mac that takes connections."""

    async def drop(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server: asyncio.Server = await asyncio.start_server(drop, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    async with server:
        yield f"http://127.0.0.1:{port}/v1"


@pytest.fixture
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_a_server_that_listens_is_reached_and_a_closed_port_is_not(listening: str) -> None:
    assert await reaches(listening) is True
    assert await reaches(f"http://127.0.0.1:{_closed_port()}/v1") is False


@pytest.mark.asyncio
async def test_an_address_without_a_host_or_behind_a_proxy_counts_as_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await reaches("not an address") is True
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{_closed_port()}")
    assert await reaches(f"http://127.0.0.1:{_closed_port()}/v1") is True


def test_the_host_is_named_with_its_port_when_the_address_has_one() -> None:
    assert host_of("https://openrouter.ai/api/v1") == "openrouter.ai"
    assert host_of("http://localhost:11434") == "localhost:11434"
    assert host_of("no address") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_a_broken_call_to_a_server_that_cannot_be_connected_to_says_so(listening: str) -> None:
    url: str = f"http://127.0.0.1:{_closed_port()}/v1/chat"
    broken: ProviderTransportError = wrap_transport_error(httpx.ConnectError("refused"), {"url": url})
    told: ProviderTransportError | None = await cannot_connect(broken)
    assert told is not None
    host: str = url.split("/")[2]
    assert out_of_reach(told) == host and told.context[OFFLINE] == host
    assert told.message.startswith(f"cannot connect to {host}: transport failure")
    # Still a broken connection to the run, which waits for it.
    assert is_retryable_provider_error(told)
    there: ProviderTransportError = wrap_transport_error(httpx.ReadError("reset"), {"url": f"{listening}/chat"})
    assert await cannot_connect(there) is None
    assert out_of_reach(there) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_a_stream_to_a_server_that_cannot_be_connected_to_is_not_retried(listening: str) -> None:
    """The night of 2026-10-28: each ask of a step whose network was down took six silent tries, 80 s, while the page
    said it asked again in 20."""
    tries: list[str] = []

    async def sink(_delta: StreamDelta) -> None:
        return None

    def failing(url: str) -> ProviderTransportError:
        return wrap_transport_error(httpx.ConnectTimeout(""), {"url": url})

    async def down() -> ModelReply:
        tries.append("down")
        raise failing(f"http://127.0.0.1:{_closed_port()}/v1/chat")

    with pytest.raises(ProviderTransportError) as caught:
        await retry_with_reset(down, sink, _POLICY, "test")
    assert tries == ["down"] and out_of_reach(caught.value) is not None

    async def flaky() -> ModelReply:
        tries.append("flaky")
        raise failing(f"{listening}/chat")

    # A server that is there but broke the call is retried as before.
    with pytest.raises(ProviderTransportError) as caught:
        await retry_with_reset(flaky, sink, _POLICY, "test")
    assert tries.count("flaky") == 4 and out_of_reach(caught.value) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_a_json_request_to_a_server_that_cannot_be_connected_to_is_not_retried() -> None:
    url: str = f"http://127.0.0.1:{_closed_port()}/v1/models"
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderTransportError) as caught:
            await post_json(client, url, {}, {}, classify_generic, _POLICY, "test")
    assert len(sent) == 1 and out_of_reach(caught.value) == url.split("/")[2]


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_the_connection_tries_the_next_address_beside_one_that_does_not_answer(
    listening: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review of item 769: tried one address after another, a network whose IPv6 route drops packets
    spent the whole check on the first, and was told to have no connection while the connectors connect over IPv4."""
    asked: list[dict[str, object]] = []
    opening = asyncio.open_connection

    async def recording(host: str, port: int, **options: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        asked.append(dict(options))
        return await opening(host, port, **options)

    monkeypatch.setattr(asyncio, "open_connection", recording)
    assert await reaches(listening) is True
    assert asked == [{"happy_eyeballs_delay": 0.25, "interleave": 1}]


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_proxy")
async def test_a_lookup_that_outlasts_its_check_is_waited_for_by_the_next_not_made_again(
    listening: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review of item 769: a lookup goes on in a thread until the resolver gives up, so checks every few
    seconds on a dead network would fill the default executor."""
    lookups: list[str] = []
    answered: asyncio.Event = asyncio.Event()

    async def slow(host: str, port: int) -> list[object]:
        lookups.append(host)
        await answered.wait()
        return []

    resolving = reach._resolve
    monkeypatch.setattr(reach, "_resolve", slow)
    assert await reaches(listening, 0.05) is False
    assert await reaches(listening, 0.05) is False
    assert lookups == ["127.0.0.1"]
    answered.set()
    await asyncio.sleep(0)
    monkeypatch.setattr(reach, "_resolve", resolving)
    # Once it is answered, the next check looks the name up again.
    assert await reaches(listening) is True
