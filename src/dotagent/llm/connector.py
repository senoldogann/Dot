"""Plumbing shared by the model connectors: JSON requests with retry, SSE streaming, retries that restart listeners."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass

import httpx
from pydantic import JsonValue

from dotagent.core.errors import JsonParseError, ProviderProtocolError, ProviderTransportError
from dotagent.core.jsonutil import parse_json_object
from dotagent.core.logs import get_logger
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.core.types import JsonObject
from dotagent.llm.http import (
    SseEvent,
    is_retryable_provider_error,
    iter_sse,
    raise_for_provider_status,
    request_context,
    retry_after_hint,
    transport_error_types,
    wrap_transport_error,
)
from dotagent.llm.provider_errors import StatusClassifier
from dotagent.llm.reach import cannot_connect, out_of_reach
from dotagent.llm.types import DeltaSink, ModelReply, StreamDelta

log = get_logger(__name__)

# How long to keep reading a stream whose reply is already complete so the connection can be reused.
_DRAIN_TIMEOUT_S: float = 1.0
_DRAIN_ERRORS: tuple[type[Exception], ...] = (TimeoutError, *transport_error_types())


@dataclass(frozen=True)
class StreamCodec[S]:
    """How one wire format turns an SSE stream into a reply: a fold over the events plus the end conditions."""

    initial: Callable[[str], S]
    fold: Callable[[S, SseEvent, JsonObject], tuple[S, tuple[StreamDelta, ...]]]
    is_final: Callable[[S], bool]
    accepts_eof: Callable[[S], bool]
    to_reply: Callable[[S], ModelReply]


def parse_event_object(event: SseEvent, context: JsonObject) -> JsonObject:
    """The JSON object in an SSE event's data; anything else violates the wire format."""
    shown: JsonObject = {**context, "event": event.event or "", "data": event.data[:500]}
    try:
        parsed: JsonValue = json.loads(event.data)
    except json.JSONDecodeError as exc:
        raise ProviderProtocolError("stream event is not valid JSON", shown) from exc
    if not isinstance(parsed, dict):
        raise ProviderProtocolError("stream event is not a JSON object", shown)
    return parsed


async def _drain(events: AsyncIterator[SseEvent]) -> None:
    """Read what is left of a finished stream (trailing pings, cost events); stop quickly if the server lingers."""
    try:
        async with asyncio.timeout(_DRAIN_TIMEOUT_S):
            async for _ in events:
                pass
    except _DRAIN_ERRORS as exc:
        log.debug("stream_drain_stopped", error_type=type(exc).__name__)


async def stream_model_reply[S](
    client: httpx.AsyncClient,
    url: str,
    body: str,
    headers: Mapping[str, str],
    context: JsonObject,
    classify: StatusClassifier,
    codec: StreamCodec[S],
    model: str,
    sink: DeltaSink,
) -> ModelReply:
    """POST ``body`` and fold the SSE events into a reply, forwarding text and reasoning to ``sink`` as they arrive."""
    state: S = codec.initial(model)
    try:
        async with client.stream("POST", url, content=body, headers=headers) as response:
            await raise_for_provider_status(response, context, classify)
            async with aclosing(iter_sse(response)) as events:
                async for event in events:
                    state, deltas = codec.fold(state, event, context)
                    for delta in deltas:
                        await sink(delta)
                    if codec.is_final(state):
                        await _drain(events)
                        break
    except transport_error_types() as exc:
        raise wrap_transport_error(exc, context) from exc
    if not (codec.is_final(state) or codec.accepts_eof(state)):
        raise ProviderTransportError("stream ended before the reply was complete", context)
    return codec.to_reply(state)


def _worth_retrying(error: Exception) -> bool:
    """A failure retrying can get past, unless its server cannot be connected to (item 769): the network is down, or a
    local server is not running, and the caller says so at once instead of retrying in silence for a minute."""
    return is_retryable_provider_error(error) and out_of_reach(error) is None


async def _unless_out_of_reach[T](operation: Callable[[], Awaitable[T]]) -> T:
    """``operation``, whose broken connection is told as one to a server that cannot be connected to when it cannot
    be (:func:`~dotagent.llm.reach.cannot_connect`)."""
    try:
        return await operation()
    except ProviderTransportError as error:
        unreachable: ProviderTransportError | None = await cannot_connect(error)
        if unreachable is None:
            raise
        raise unreachable from error


async def retry_with_reset(
    attempt_once: Callable[[], Awaitable[ModelReply]], sink: DeltaSink, policy: RetryPolicy, label: str
) -> ModelReply:
    """Run a streaming call with retries; each retry first tells the sink to drop what the failed try streamed."""
    started: list[bool] = []

    async def attempt() -> ModelReply:
        if started:
            await sink(StreamDelta(kind="reset", text=""))
        started.append(True)
        return await _unless_out_of_reach(attempt_once)

    return await retry_async(attempt, _worth_retrying, retry_after_hint, policy, label)


async def _json_attempt(
    send: Callable[[], Awaitable[httpx.Response]], context: JsonObject, classify: StatusClassifier, label: str
) -> JsonObject:
    try:
        response: httpx.Response = await send()
    except transport_error_types() as exc:
        raise wrap_transport_error(exc, context) from exc
    await raise_for_provider_status(response, context, classify)
    try:
        return parse_json_object(response.text, label)
    except JsonParseError as exc:
        # A success whose body is not JSON (a proxy's page, a cut body) breaks the wire format like any other (item
        # 116).
        raise ProviderProtocolError(
            "the provider answered with a body that is not a JSON object", {**context, "error": exc.message}
        ) from exc


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    payload: JsonObject,
    classify: StatusClassifier,
    policy: RetryPolicy,
    label: str,
) -> JsonObject:
    """POST a JSON body and return the JSON object that answers, retrying transient failures."""
    context: JsonObject = request_context(url, payload)

    async def send() -> httpx.Response:
        return await client.post(url, json=payload, headers=headers)

    return await retry_async(
        lambda: _unless_out_of_reach(lambda: _json_attempt(send, context, classify, label)),
        _worth_retrying,
        retry_after_hint,
        policy,
        label,
    )


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    classify: StatusClassifier,
    policy: RetryPolicy,
    label: str,
) -> JsonObject:
    """GET a JSON object, retrying transient failures."""
    context: JsonObject = {"url": url}

    async def send() -> httpx.Response:
        return await client.get(url, headers=headers)

    return await retry_async(
        lambda: _unless_out_of_reach(lambda: _json_attempt(send, context, classify, label)),
        _worth_retrying,
        retry_after_hint,
        policy,
        label,
    )
