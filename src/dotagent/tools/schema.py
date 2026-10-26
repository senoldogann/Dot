"""JSON Schema for tool parameters, flattened so every provider accepts it.

A part that refers again to a definition its own path already went through (a tree whose nodes hold nodes), one that
nests deeper than :data:`_MAX_DEPTH`, or one that refers to a definition that is not there is cut to a loose schema of
its kind, which says so in its description (item 671): the tool keeps its other parameters, and the service checks
what the model sends for that part. Before, one such part kept the whole MCP server out of every run (Vercel's
``update_flag``). A schema whose flattening would grow past :data:`_MAX_NODES` nodes or :data:`MAX_SCHEMA_CHARS`
characters is refused, as one whose top is no object is: definitions used many times over each other multiply."""

from dataclasses import dataclass

from pydantic import BaseModel, JsonValue

from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject

_MAX_DEPTH: int = 12
# The most nodes a flattening makes, and the longest a flattened schema may be: a tool's parameters go with every
# request of every run that has the tool, so one far larger than any tool needs is refused rather than sent.
_MAX_NODES: int = 5000
MAX_SCHEMA_CHARS: int = 32_000
_SCHEMA_KEYS_WITH_MAP: frozenset[str] = frozenset({"properties", "$defs"})
_SCHEMA_KEYS_WITH_LIST: frozenset[str] = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
_SCHEMA_KEYS_WITH_NODE: frozenset[str] = frozenset({"items", "additionalProperties", "not"})
_DROPPED_KEYS: frozenset[str] = frozenset({"title", "$defs", "definitions", "$schema"})
_SCALARS: frozenset[str] = frozenset({"string", "number", "integer", "boolean"})
# What a cut part says of itself, after its own description.
_CUT_NOTE: str = "Its parts nest deeper than shown here: give it as the service's docs say."


@dataclass
class _Walk:
    """One flattening: the definitions it reads and how many nodes it made so far."""

    defs: JsonObject
    nodes: int = 0

    def count(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise ValueError(f"tool schema grows past {_MAX_NODES} parts once its references are written in")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _resolve_ref(ref: str, defs: JsonObject) -> JsonObject | None:
    """The definition ``ref`` names, or ``None`` when the document has none of that name."""
    target: JsonValue = defs.get(_ref_name(ref))
    return target if isinstance(target, dict) else None


def _kind(node: JsonObject, defs: JsonObject) -> str | None:
    """The one type ``node`` says it has, after its reference and leaving ``null`` out of a list of them."""
    ref: JsonValue = node.get("$ref")
    target: JsonObject | None = _resolve_ref(ref, defs) if isinstance(ref, str) else None
    said: JsonValue = node.get("type", target.get("type") if target is not None else None)
    if isinstance(said, list):
        kinds: list[str] = [each for each in said if isinstance(each, str) and each != "null"]
        return kinds[0] if len(kinds) == 1 else None
    return said if isinstance(said, str) else None


def _cut(node: JsonValue, defs: JsonObject) -> JsonObject:
    """A loose schema of ``node``'s kind in its place, its description kept and saying that it was cut: a scalar keeps
    its type, an array is a list of objects, anything else an object."""
    found: JsonObject = node if isinstance(node, dict) else {}
    ref: JsonValue = found.get("$ref")
    target: JsonObject = (_resolve_ref(ref, defs) or {}) if isinstance(ref, str) else {}
    said: JsonValue = found.get("description", target.get("description"))
    description: str = f"{said} {_CUT_NOTE}" if isinstance(said, str) and said.strip() else _CUT_NOTE
    kind: str | None = _kind(found, defs)
    if kind in _SCALARS:
        return {"type": kind, "description": description}
    if kind == "array":
        return {"type": "array", "items": {"type": "object"}, "description": description}
    return {"type": "object", "description": description}


def _is_null(node: JsonValue) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _transform(node: JsonValue, walk: _Walk, depth: int, path: frozenset[str]) -> JsonValue:
    """``node`` flattened; ``path`` names the definitions the way to it went through, so that a reference to one of
    them again (a cycle) is cut where it first comes back."""
    walk.count()
    if not isinstance(node, dict):
        return node
    if depth > _MAX_DEPTH:
        return _cut(node, walk.defs)
    if "$ref" in node and isinstance(node["$ref"], str):
        name: str = _ref_name(node["$ref"])
        resolved: JsonObject | None = _resolve_ref(node["$ref"], walk.defs)
        if resolved is None or name in path:
            return _cut(node, walk.defs)
        merged: JsonObject = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return _transform(merged, walk, depth + 1, path | {name})
    any_of: JsonValue = node.get("anyOf")
    if isinstance(any_of, list):
        non_null: list[JsonValue] = [item for item in any_of if not _is_null(item)]
        if len(non_null) == 1 and len(non_null) != len(any_of):
            inner: JsonValue = _transform(non_null[0], walk, depth + 1, path)
            rest: JsonObject = {k: v for k, v in node.items() if k not in ("anyOf", "default", "title")}
            if not isinstance(inner, dict):
                return inner
            merged_node: JsonObject = {**inner, **rest}
            # An optional part that was cut keeps saying so under the field's own description.
            cut: JsonValue = inner.get("description")
            own: JsonValue = rest.get("description")
            if isinstance(cut, str) and cut.endswith(_CUT_NOTE) and isinstance(own, str) and _CUT_NOTE not in own:
                merged_node["description"] = f"{own} {_CUT_NOTE}" if own.strip() else _CUT_NOTE
            return merged_node
    out: JsonObject = {}
    for key, value in node.items():
        if key in _DROPPED_KEYS:
            continue
        if key == "default" and value is None:
            continue
        if key in _SCHEMA_KEYS_WITH_MAP and isinstance(value, dict):
            out[key] = {name: _transform(child, walk, depth + 1, path) for name, child in value.items()}
        elif key in _SCHEMA_KEYS_WITH_LIST and isinstance(value, list):
            out[key] = [_transform(child, walk, depth + 1, path) for child in value]
        elif key in _SCHEMA_KEYS_WITH_NODE:
            out[key] = _transform(value, walk, depth + 1, path)
        else:
            out[key] = value
    return out


def _definitions(raw: JsonObject) -> JsonObject:
    """The reusable sub-schemas of a document, whether it calls them ``$defs`` (current) or ``definitions`` (draft
    7)."""
    collected: JsonObject = {}
    for key in ("definitions", "$defs"):
        found: JsonValue = raw.get(key)
        if isinstance(found, dict):
            collected = {**collected, **found}
    return collected


def flatten_json_schema(raw: JsonObject) -> JsonObject:
    """Flattened JSON Schema (no ``$ref``, no titles, optional fields without ``null`` unions) of an object. A schema
    whose top is no object (a string, a list), or that grows too large once flattened, is refused with
    ``ValueError``."""
    flat: JsonValue = _transform(raw, _Walk(defs=_definitions(raw)), 0, frozenset())
    if not isinstance(flat, dict) or flat.get("type", "object") != "object":
        raise ValueError("tool schema flattened to a non-object")
    flat.setdefault("type", "object")
    flat.setdefault("properties", {})
    if len(dumps_compact(flat)) > MAX_SCHEMA_CHARS:
        raise ValueError(f"tool schema is longer than {MAX_SCHEMA_CHARS:,} characters once flattened")
    return flat


def tool_parameters_schema(model: type[BaseModel]) -> JsonObject:
    """Flattened JSON Schema of the arguments model of a tool."""
    return flatten_json_schema(model.model_json_schema())
