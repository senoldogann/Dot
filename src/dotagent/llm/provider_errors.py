"""Provider error bodies and their mapping onto the typed error hierarchy.

OpenAI answers ``{"error": {"message", "type", "code"}}``. OpenCode Go and Anthropic-style routes answer
``{"type": "error", "error": {"type", "message"}}`` (OpenCode adds ``metadata.limitName``). Mid-stream errors
use the same shapes, sometimes flattened to the top level. Each provider supplies a classifier; the shared
code below decides whether a failure is worth retrying by choosing the error class.
"""

import json
from collections.abc import Callable, Mapping

from pydantic import JsonValue

from dotagent.core.errors import (
    ProviderAuthError,
    ProviderHttpError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from dotagent.core.text import redact_secrets
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.wire import get_obj, get_scalar_text, get_str

_MESSAGE_CHARS: int = 300


class WireError(FrozenModel):
    """The error a provider described in a response or stream body (empty strings for absent parts)."""

    kind: str
    code: str
    message: str
    limit_name: str


type StatusClassifier = Callable[[int, WireError | None, float | None, JsonObject], ProviderHttpError]

_OPENAI_QUOTA: frozenset[str] = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }
)
_OPENCODE_QUOTA: frozenset[str] = frozenset(
    {"GoUsageLimitError", "CreditsError", "MonthlyLimitError", "UserLimitError"}
)

# HTTP-equivalent status of an error that arrived inside an already-open stream, by error code or type.
_STREAM_STATUS: dict[str, int] = {
    "server_error": 500,
    "api_error": 500,
    "error": 500,
    "rate_limit_exceeded": 429,
    "rate_limit_error": 429,
    "slow_down": 429,
    "RateLimitError": 429,
    "service_unavailable_error": 503,
    "server_is_overloaded": 503,
    "overloaded_error": 529,
    "authentication_error": 401,
    "invalid_api_key": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "invalid_request_error": 400,
    "invalid_prompt": 400,
    "context_length_exceeded": 400,
    "request_too_large": 413,
}


def wire_error_from_object(obj: JsonObject) -> WireError | None:
    """Read an error description from an already-parsed JSON object, in any of the known shapes."""
    metadata: JsonObject = get_obj(obj, "metadata")
    nested: JsonValue = obj.get("error")
    if isinstance(nested, dict):
        source: JsonObject = nested
    elif isinstance(nested, str):
        return WireError(kind="", code="", message=nested, limit_name=get_str(metadata, "limitName"))
    elif get_str(obj, "type") == "error" or "code" in obj:
        source = obj
    else:
        return None
    return WireError(
        kind=get_str(source, "type"),
        code=get_scalar_text(source, "code"),
        message=get_str(source, "message"),
        limit_name=get_str(metadata, "limitName"),
    )


def parse_wire_error(body: str) -> WireError | None:
    """Parse a response body into a ``WireError``; ``None`` when the body is not a JSON error description."""
    try:
        parsed: JsonValue = json.loads(body)
    except json.JSONDecodeError:
        return None
    return wire_error_from_object(parsed) if isinstance(parsed, dict) else None


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """The server's minimum wait in seconds (``retry-after-ms`` first, then ``retry-after``)."""
    milliseconds: str | None = headers.get("retry-after-ms")
    if milliseconds is not None:
        try:
            return max(0.0, float(milliseconds) / 1000.0)
        except ValueError:
            return None
    seconds: str | None = headers.get("retry-after")
    if seconds is None:
        return None
    try:
        return max(0.0, float(seconds))
    except ValueError:
        return None


def error_message(status: int, wire: WireError | None) -> str:
    base: str = f"provider answered HTTP {status}"
    if wire is None or not wire.message:
        return base
    return f"{base}: {redact_secrets(wire.message)[:_MESSAGE_CHARS]}"


def classify_generic(
    status: int, wire: WireError | None, retry_after_s: float | None, detail: JsonObject
) -> ProviderHttpError:
    """401/403 are credentials problems, 429 is a rate limit, anything else a plain HTTP failure."""
    message: str = error_message(status, wire)
    if status in (401, 403):
        return ProviderAuthError(message, status, detail)
    if status == 429:
        return ProviderRateLimitError(message, status, retry_after_s, detail)
    return ProviderHttpError(message, status, detail)


def classify_openai(
    status: int, wire: WireError | None, retry_after_s: float | None, detail: JsonObject
) -> ProviderHttpError:
    """OpenAI: a 429 that says the account is out of credit or over its spend cap is terminal."""
    if wire is not None and (wire.code in _OPENAI_QUOTA or wire.kind in _OPENAI_QUOTA):
        return ProviderQuotaError(error_message(status, wire), status, retry_after_s, detail)
    return classify_generic(status, wire, retry_after_s, detail)


def classify_opencode(
    status: int, wire: WireError | None, retry_after_s: float | None, detail: JsonObject
) -> ProviderHttpError:
    """OpenCode Go: quota and credit errors are terminal whatever their status; ``RateLimitError`` is a burst."""
    if wire is not None and wire.kind in _OPENCODE_QUOTA:
        return ProviderQuotaError(
            error_message(status, wire), status, retry_after_s, {**detail, "limit_name": wire.limit_name}
        )
    return classify_generic(status, wire, retry_after_s, detail)


def status_for_stream_error(wire: WireError) -> int:
    """Map an in-stream error to the HTTP status it corresponds to (500 when nothing is known)."""
    for key in (wire.code, wire.kind):
        if key in _STREAM_STATUS:
            return _STREAM_STATUS[key]
    if wire.code.startswith("image_"):
        return 400
    if wire.code in _OPENAI_QUOTA or wire.kind in _OPENCODE_QUOTA:
        return 429
    return 500


def error_from_stream(wire: WireError, context: JsonObject, classify: StatusClassifier) -> ProviderHttpError:
    """The typed error for a failure reported inside a stream whose HTTP status was already 200."""
    detail: JsonObject = {
        **context,
        "stream_error": {"type": wire.kind, "code": wire.code, "message": redact_secrets(wire.message)[:1000]},
    }
    return classify(status_for_stream_error(wire), wire, None, detail)
