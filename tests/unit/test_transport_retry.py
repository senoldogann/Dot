"""A TLS read that fails on the wire (``ssl.SSLError``, "bad record mac") is a transport failure (item 107): the
streaming call wraps it, the retry runs again with a reset of the sink, and the last failure is raised."""

import ssl

import httpx
import pytest

from dotagent.core.errors import ProviderTransportError
from dotagent.core.retry import RetryPolicy
from dotagent.llm.codec_chat import ChatOptions, chat_codec
from dotagent.llm.connector import retry_with_reset, stream_model_reply
from dotagent.llm.http import is_retryable_provider_error, transport_error_types
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
