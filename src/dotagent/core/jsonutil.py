"""Strict JSON helpers built on pydantic's JSON value type."""

import json

from pydantic import JsonValue, TypeAdapter

from dotagent.core.errors import JsonParseError
from dotagent.core.types import JsonObject

_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def parse_json_object(text: str, label: str) -> JsonObject:
    """Parse ``text`` as a JSON object or raise ``JsonParseError`` with context."""
    try:
        return _OBJECT_ADAPTER.validate_json(text)
    except ValueError as exc:
        raise JsonParseError("expected a JSON object", {"label": label, "text": text[:500], "error": str(exc)}) from exc


def json_object(value: object, label: str) -> JsonObject:
    """``value``, a decoded JSON object from elsewhere (a library's parsed message), as a JSON object, or
    ``JsonParseError`` with context."""
    try:
        return _OBJECT_ADAPTER.validate_python(value)
    except ValueError as exc:
        raise JsonParseError(
            "expected a JSON object", {"label": label, "value": repr(value)[:500], "error": str(exc)}
        ) from exc


def parse_json_value(text: str, label: str) -> JsonValue:
    """Parse ``text`` as any JSON value or raise ``JsonParseError`` with context."""
    try:
        return _VALUE_ADAPTER.validate_json(text)
    except ValueError as exc:
        raise JsonParseError("expected valid JSON", {"label": label, "text": text[:500], "error": str(exc)}) from exc


def dumps_compact(value: JsonValue) -> str:
    """Serialise without whitespace and without escaping non-ASCII text."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
