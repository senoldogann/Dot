"""JSON Schema for tool parameters, flattened so every provider accepts it."""

from pydantic import BaseModel, JsonValue

from dotagent.core.types import JsonObject

_MAX_DEPTH: int = 12
_SCHEMA_KEYS_WITH_MAP: frozenset[str] = frozenset({"properties", "$defs"})
_SCHEMA_KEYS_WITH_LIST: frozenset[str] = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
_SCHEMA_KEYS_WITH_NODE: frozenset[str] = frozenset({"items", "additionalProperties", "not"})
_DROPPED_KEYS: frozenset[str] = frozenset({"title", "$defs", "definitions", "$schema"})


def _resolve_ref(ref: str, defs: JsonObject) -> JsonObject:
    name: str = ref.rsplit("/", 1)[-1]
    target: JsonValue = defs.get(name)
    if not isinstance(target, dict):
        raise ValueError(f"unresolvable schema reference {ref!r}")
    return target


def _is_null(node: JsonValue) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _transform(node: JsonValue, defs: JsonObject, depth: int) -> JsonValue:
    if depth > _MAX_DEPTH:
        raise ValueError("tool schema nests deeper than supported")
    if not isinstance(node, dict):
        return node
    if "$ref" in node and isinstance(node["$ref"], str):
        resolved: JsonObject = _resolve_ref(node["$ref"], defs)
        merged: JsonObject = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return _transform(merged, defs, depth + 1)
    any_of: JsonValue = node.get("anyOf")
    if isinstance(any_of, list):
        non_null: list[JsonValue] = [item for item in any_of if not _is_null(item)]
        if len(non_null) == 1 and len(non_null) != len(any_of):
            inner: JsonValue = _transform(non_null[0], defs, depth + 1)
            rest: JsonObject = {k: v for k, v in node.items() if k not in ("anyOf", "default", "title")}
            return {**inner, **rest} if isinstance(inner, dict) else inner
    out: JsonObject = {}
    for key, value in node.items():
        if key in _DROPPED_KEYS:
            continue
        if key == "default" and value is None:
            continue
        if key in _SCHEMA_KEYS_WITH_MAP and isinstance(value, dict):
            out[key] = {name: _transform(child, defs, depth + 1) for name, child in value.items()}
        elif key in _SCHEMA_KEYS_WITH_LIST and isinstance(value, list):
            out[key] = [_transform(child, defs, depth + 1) for child in value]
        elif key in _SCHEMA_KEYS_WITH_NODE:
            out[key] = _transform(value, defs, depth + 1)
        else:
            out[key] = value
    return out


def _definitions(raw: JsonObject) -> JsonObject:
    """The reusable sub-schemas of a document, whether it calls them ``$defs`` (current) or ``definitions`` (draft 7)."""
    collected: JsonObject = {}
    for key in ("definitions", "$defs"):
        found: JsonValue = raw.get(key)
        if isinstance(found, dict):
            collected = {**collected, **found}
    return collected


def flatten_json_schema(raw: JsonObject) -> JsonObject:
    """Flattened JSON Schema (no ``$ref``, no titles, optional fields without ``null`` unions) of an object."""
    flat: JsonValue = _transform(raw, _definitions(raw), 0)
    if not isinstance(flat, dict):
        raise ValueError("tool schema flattened to a non-object")
    flat.setdefault("type", "object")
    flat.setdefault("properties", {})
    return flat


def tool_parameters_schema(model: type[BaseModel]) -> JsonObject:
    """Flattened JSON Schema of the arguments model of a tool."""
    return flatten_json_schema(model.model_json_schema())
