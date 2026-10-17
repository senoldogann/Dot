"""A request of the web tools that failed in transit (the code review's Y14): a GET is tried again, a POST the server may
have got is not, since what it asked may have been done (a search the API bills); a POST that was never sent is."""

import httpx
import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import WebTransportError
from dotagent.core.retry import RetryPolicy
from dotagent.web import client as web_client
from dotagent.web.client import WebClient

_URL: str = "https://api.search.example/v1/search"
_MAX_BYTES: int = 4096


@pytest.mark.parametrize(
    ("method", "failure", "tries"),
    [
        ("POST", httpx.ReadTimeout, 1),
        ("POST", httpx.RemoteProtocolError, 1),
        ("POST", httpx.ConnectError, 2),
        ("GET", httpx.ReadTimeout, 2),
    ],
)
@pytest.mark.asyncio
async def test_a_post_the_server_may_have_got_is_sent_once_and_a_get_is_tried_again(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: type[httpx.TransportError], tries: int
) -> None:
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        raise failure("MARKER-lost-on-the-way", request=request)

    def fake_transport(backend: object, limits: httpx.Limits) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(answer)

    monkeypatch.setattr(web_client, "_CheckedTransport", fake_transport)
    monkeypatch.setattr(web_client, "_RETRY", RetryPolicy(attempts=2, base_delay_s=0.0, max_delay_s=0.0))
    client: WebClient = WebClient(load_default_config().web, (), ())
    try:
        with pytest.raises(WebTransportError) as raised:
            if method == "POST":
                await client.post_json(_URL, {"x-api-key": "MARKER-key"}, b'{"query": "dot"}', _MAX_BYTES)
            else:
                await client.get(_URL, _MAX_BYTES)
    finally:
        await client.aclose()
    assert isinstance(raised.value.__cause__, failure)
    assert sent == [method] * tries
