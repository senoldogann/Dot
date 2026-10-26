"""A tool whose input schema nests deeper than Dot follows, refers to itself or to nothing (item 671): the part is cut
to a loose schema of its kind that says so, where a reference first comes back on its own path, the rest of the tool's
parameters stay as they were, and the tool is handed to the model. Before, one such tool (Vercel's ``update_flag``)
left its whole MCP server out of every run. A schema that refers to itself many times over (a filter of and, or and
not) is cut at once, small; one that grows too large is refused. A tool whose schema is refused is left out alone, and
its server's other tools serve, unless the owner's ``allow`` names it or no tool is left."""

import time

import pytest
from pydantic import JsonValue

from dotagent.config import McpServerConfig
from dotagent.core.errors import McpError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject
from dotagent.mcp.hub import usable_tools
from dotagent.mcp.naming import exposed_name
from dotagent.mcp.types import McpTool, ToolHints
from dotagent.tools.schema import flatten_json_schema

_CUT: str = "Its parts nest deeper than shown here: give it as the service's docs say."


def _tool(name: str, schema: JsonObject) -> McpTool:
    return McpTool(
        server="vercel",
        remote_name=name,
        exposed_name=exposed_name("vercel", name),
        description=f"the tool {name}",
        input_schema=schema,
        hints=ToolHints(read_only=False, destructive=False, open_world=True),
        startup_timeout_s=10.0,
        call_timeout_s=10.0,
    )


def _deepest(node: JsonValue) -> int:
    """How many objects deep ``node`` goes."""
    if isinstance(node, dict):
        return 1 + max((_deepest(child) for child in node.values()), default=0)
    if isinstance(node, list):
        return max((_deepest(child) for child in node), default=0)
    return 0


def _nested(levels: int) -> JsonObject:
    """An object schema whose property ``rule`` holds another, ``levels`` times, a string at the bottom: a feature
    flag's rules within rules."""
    node: JsonObject = {"type": "string", "description": "the value"}
    for _ in range(levels):
        node = {"type": "object", "description": "a rule", "properties": {"rule": node}}
    return node


def test_a_schema_that_refers_to_itself_is_cut_where_it_would_go_on() -> None:
    tree: JsonObject = {
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/Node"}, "project": {"type": "string"}},
        "required": ["root", "project"],
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
            }
        },
    }
    flat: JsonObject = flatten_json_schema(tree)
    assert flat["required"] == ["root", "project"] and flat["properties"]["project"] == {"type": "string"}
    assert "$ref" not in str(flat) and _CUT in str(flat)
    assert _deepest(flat) < 40


def test_a_schema_deeper_than_dot_follows_keeps_its_top_and_cuts_its_bottom() -> None:
    flat: JsonObject = flatten_json_schema(
        {"type": "object", "properties": {"flag": {"type": "string"}, "rules": _nested(20)}, "required": ["flag"]}
    )
    assert flat["properties"]["flag"] == {"type": "string"} and flat["required"] == ["flag"]
    node: JsonValue = flat["properties"]["rules"]
    while isinstance(node, dict) and "properties" in node:
        node = node["properties"]["rule"]
    assert node == {"type": "object", "description": f"a rule {_CUT}"}


def test_a_schema_within_the_depth_dot_follows_is_not_cut() -> None:
    assert _CUT not in str(flatten_json_schema({"type": "object", "properties": {"rules": _nested(5)}}))


def test_a_reference_to_nothing_is_a_loose_object_that_says_so() -> None:
    flat: JsonObject = flatten_json_schema(
        {"type": "object", "properties": {"env": {"$ref": "#/$defs/Missing", "description": "where it runs"}}}
    )
    assert flat["properties"]["env"] == {"type": "object", "description": f"where it runs {_CUT}"}


def test_a_filter_that_refers_to_itself_many_times_over_is_cut_at_once_and_small() -> None:
    # A where-filter as GraphQL services write it: each of and, or and not holds the filter again, and its fields too.
    where: JsonObject = {
        "type": "object",
        "properties": {
            "name": {"$ref": "#/$defs/Text"},
            "_and": {"type": "array", "items": {"$ref": "#/$defs/Where"}},
            "_or": {"type": "array", "items": {"$ref": "#/$defs/Where"}},
            "_not": {"$ref": "#/$defs/Where"},
        },
    }
    raw: JsonObject = {
        "type": "object",
        "properties": {"where": {"$ref": "#/$defs/Where"}},
        "$defs": {"Where": where, "Text": {"type": "object", "properties": {"_eq": {"type": "string"}}}},
    }
    started: float = time.monotonic()
    flat: JsonObject = flatten_json_schema(raw)
    assert time.monotonic() - started < 1.0
    assert len(dumps_compact(flat)) < 2000, len(dumps_compact(flat))
    assert flat["properties"]["where"]["properties"]["_not"]["description"] == _CUT


def test_a_cut_part_keeps_the_kind_its_definition_or_its_list_of_types_says() -> None:
    raw: JsonObject = {
        "type": "object",
        "properties": {"labels": {"$ref": "#/$defs/Labels"}, "note": {"type": ["string", "null"], "$ref": "#/$defs/x"}},
        "$defs": {"Labels": {"type": "array", "items": {"$ref": "#/$defs/Labels"}}},
    }
    flat: JsonObject = flatten_json_schema(raw)
    assert flat["properties"]["labels"]["type"] == "array"
    assert flat["properties"]["labels"]["items"] == {"type": "array", "items": {"type": "object"}, "description": _CUT}
    assert flat["properties"]["note"] == {"type": "string", "description": _CUT}


def test_an_optional_part_that_was_cut_keeps_saying_so_under_its_own_description() -> None:
    # As pydantic writes `children: Node | None = Field(None, description="The ones under it.")`.
    raw: JsonObject = {
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "children": {
                        "anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}],
                        "default": None,
                        "description": "The ones under it.",
                    }
                },
            }
        },
    }
    children: JsonValue = flatten_json_schema(raw)["properties"]["root"]["properties"]["children"]
    assert children == {"type": "object", "description": f"The ones under it. {_CUT}"}
    # A field whose own description is empty says only that it was cut.
    raw["$defs"]["Node"]["properties"]["children"]["description"] = ""
    blank: JsonValue = flatten_json_schema(raw)["properties"]["root"]["properties"]["children"]
    assert blank == {"type": "object", "description": _CUT}


def test_a_schema_too_large_once_flattened_is_refused() -> None:
    wide: JsonObject = {
        "type": "object",
        "properties": {f"field_{n}": {"type": "string", "description": "x" * 400} for n in range(100)},
    }
    with pytest.raises(ValueError, match="longer than"):
        flatten_json_schema(wide)


def _server(allow: tuple[str, ...] = ()) -> McpServerConfig:
    return McpServerConfig(name="vercel", url="https://mcp.vercel.com", allow=list(allow))


def test_a_tool_whose_arguments_are_no_object_is_left_out_alone() -> None:
    good: McpTool = _tool("list_projects", {"type": "object", "properties": {"team": {"type": "string"}}})
    deep: McpTool = _tool("update_flag", {"type": "object", "properties": {"rules": _nested(20)}})
    odd: McpTool = _tool("odd", {"type": "string"})
    assert [tool.remote_name for tool in usable_tools(_server(), (good, deep, odd))] == ["list_projects", "update_flag"]


def test_a_tool_the_owner_allowed_or_a_server_left_with_none_is_said_and_not_dropped() -> None:
    good: McpTool = _tool("list_projects", {"type": "object", "properties": {}})
    odd: McpTool = _tool("odd", {"type": "string"})
    with pytest.raises(McpError) as allowed:
        usable_tools(_server(("list_projects", "odd")), (good, odd))
    assert (allowed.value.message, allowed.value.context["tool"]) == (
        "the input schema of an MCP tool cannot be used",
        "odd",
    )
    with pytest.raises(McpError, match="no input schema of the MCP server's tools can be used"):
        usable_tools(_server(), (odd,))
