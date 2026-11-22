"""HTTP plumbing shared by all providers: client factory, error mapping, NDJSON and SSE readers."""

import json
import ssl
from collections.abc import AsyncGenerator, AsyncIterator

import httpx
from pydantic import JsonValue

from dotagent.core.errors import (
    ProviderAuthError,
    ProviderHttpError,
    ProviderProtocolError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRunnerError,
    ProviderTransportError,
)
from dotagent.core.text import redact_secrets, stream_lines, strip_blobs
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.provider_errors import StatusClassifier, parse_retry_after, parse_wire_error

_RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524, 529})
# A server asking for a longer pause than this is reported instead of silently stalling the agent.
_MAX_AUTOMATIC_WAIT_S: float = 120.0
# Every failure on the way to the provider and back: httpx's transport errors (connect, timeouts, a proxy, a protocol
# broken on either side, a connection that fails to close) and a body that cannot be decoded (item 116), and a TLS read
# that fails on the wire ("bad record mac"), which reaches the caller as the raw ssl error, not as a ReadError (item
# 107): the network path corrupted a record, the call is as retryable as any other transport failure.
_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (httpx.TransportError, httpx.DecodingError, ssl.SSLError)


# How long an idle connection to a provider is kept for the next call (item 1314). A connection that sat longer than the
# provider's own side keeps it is broken on the wire ("bad record mac" in the log), and each retry of a call that met it
# sends the prompt (28 to 51k characters) again; 30 s is under what the services keep, and a run's steps follow each
# other within seconds.
KEEPALIVE_EXPIRY_S: float = 30.0


class SseEvent(FrozenModel):
    event: str | None
    data: str


def build_client(connect_timeout_s: float, read_timeout_s: float) -> httpx.AsyncClient:
    timeout: httpx.Timeout = httpx.Timeout(
        connect=connect_timeout_s, read=read_timeout_s, write=60.0, pool=connect_timeout_s
    )
    limits: httpx.Limits = httpx.Limits(
        max_connections=32, max_keepalive_connections=16, keepalive_expiry=KEEPALIVE_EXPIRY_S
    )
    return httpx.AsyncClient(timeout=timeout, limits=limits, http2=False, follow_redirects=False)


def request_context(url: str, payload: JsonObject) -> JsonObject:
    """A log-safe description of an outgoing request (no images, no secrets)."""
    shown: JsonValue = strip_blobs(payload)
    rendered: str = redact_secrets(json.dumps(shown, ensure_ascii=False, default=str))
    return {"url": url, "payload": rendered[:3000]}


async def raise_for_provider_status(response: httpx.Response, context: JsonObject, classify: StatusClassifier) -> None:
    """Raise the provider's typed error for a non-2xx response, including the (redacted) response body."""
    if response.is_success:
        return
    body_bytes: bytes = await response.aread()
    body: str = body_bytes.decode("utf-8", errors="replace")
    detail: JsonObject = {**context, "response_body": redact_secrets(body)[:4000]}
    raise classify(response.status_code, parse_wire_error(body), parse_retry_after(response.headers), detail)


def is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, ProviderAuthError | ProviderQuotaError):
        return False
    if isinstance(exc, ProviderRateLimitError) and (exc.retry_after_s or 0.0) > _MAX_AUTOMATIC_WAIT_S:
        return False
    if isinstance(exc, ProviderRunnerError | ProviderRateLimitError):
        # A rate limit is retried whatever its status: OpenRouter's credits held by requests still running answer 402.
        return True
    if isinstance(exc, ProviderHttpError):
        return exc.status in _RETRYABLE_STATUS
    return isinstance(exc, ProviderTransportError)


def retry_after_hint(exc: Exception) -> float | None:
    if isinstance(exc, ProviderRateLimitError):
        return exc.retry_after_s
    return None


def wrap_transport_error(exc: Exception, context: JsonObject) -> ProviderTransportError:
    """The error of a connection that broke: its message says what broke it (the kind and the words of ``exc``), which
    the context keeps too, beside the request's."""
    words: str = str(exc)
    reason: str = type(exc).__name__ if words == "" else f"{type(exc).__name__}: {words}"
    return ProviderTransportError(
        f"transport failure while talking to the provider ({reason})",
        {**context, "error_type": type(exc).__name__, "error": words},
    )


def transport_error_types() -> tuple[type[Exception], ...]:
    return _TRANSPORT_ERRORS


async def iter_ndjson(response: httpx.Response, context: JsonObject) -> AsyncIterator[JsonObject]:
    """Yield one JSON object per non-empty line of a newline-delimited JSON stream."""
    async for line in stream_lines(response.aiter_text()):
        stripped: str = line.strip()
        if stripped == "":
            continue
        try:
            parsed: JsonValue = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError("stream line is not valid JSON", {**context, "line": stripped[:500]}) from exc
        if not isinstance(parsed, dict):
            raise ProviderProtocolError("stream line is not a JSON object", {**context, "line": stripped[:500]})
        yield parsed


async def iter_sse(response: httpx.Response) -> AsyncGenerator[SseEvent]:
    """Yield Server-Sent Events (``event:`` + ``data:`` blocks separated by blank lines)."""
    event_name: str | None = None
    data_lines: list[str] = []
    async for line in stream_lines(response.aiter_text()):
        if line == "":
            if data_lines:
                yield SseEvent(event=event_name, data="\n".join(data_lines))
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        cleaned: str = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = cleaned
        elif field == "data":
            data_lines.append(cleaned)
    if data_lines:
        yield SseEvent(event=event_name, data="\n".join(data_lines))
