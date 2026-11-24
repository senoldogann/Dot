"""The catalogue of the tools a code may call, with the names they have in Python (items 1501 and 1502).

``dot_tools.search_tools`` reads it (``dot_tools/_catalog.json``) and the typed function of each tool is made from it
(:mod:`dotagent.codemode.toolfiles`). A tool has the name the bridge calls it by (``tool``: ``read_file``,
``mcp__github__create_issue``) and a place in the package: Dot's own tools are the server ``dot``, the tools of an MCP
server are the server's, under its own name with every character that is no letter, digit or ``_`` read as ``_`` (a
server of Dot's own name keeps ``dot``, and the MCP server gets ``dot_mcp``), and the function is the tool's name with
the server's prefix taken off. A name that is a keyword of Python, begins with a digit or that two tools share once
written so is made unique and valid: ``class`` is ``class_``, a second ``a_b`` is ``a_b_2``.
"""

import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass

from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolSpec
from dotagent.mcp.naming import PREFIX

DOT_SERVER: str = "dot"
_NOT_IN_A_NAME: re.Pattern[str] = re.compile(r"\W")


@dataclass(frozen=True)
class CatalogTool:
    """One tool a code may call: where it is in ``dot_tools.servers`` (``module`` and ``function``), the ``server`` it
    is of as the run knows it (``kind``: Dot's own, ``dot``, or an ``mcp`` server's), and the ``tool`` name the bridge
    calls it by."""

    kind: str
    server: str
    module: str
    function: str
    tool: str
    description: str
    parameters: JsonObject


def identifier(text: str) -> str:
    """``text`` as a name Python can import: letters, digits and ``_``, not a keyword, not starting with a digit."""
    name: str = _NOT_IN_A_NAME.sub("_", text).strip() or "tool"
    if name[0].isdigit():
        name = f"_{name}"
    return f"{name}_" if keyword.iskeyword(name) else name


def _unique(name: str, taken: set[str]) -> str:
    chosen: str = name
    number: int = 2
    while chosen in taken:
        chosen = f"{name}_{number}"
        number += 1
    taken.add(chosen)
    return chosen


def _server_and_tool(exposed: str) -> tuple[str, str, str]:
    """The kind of server a tool is of, the server and the tool: an MCP tool's name says which server it is of, Dot's
    own tools are of the server ``dot``, and an MCP server that is called ``dot`` is another."""
    if exposed.startswith(PREFIX):
        server, _, tool = exposed.removeprefix(PREFIX).partition("__")
        return "mcp", server, tool
    return "dot", DOT_SERVER, exposed


def build(specs: Mapping[str, ToolSpec]) -> tuple[CatalogTool, ...]:
    """The catalogue of ``specs``, in the order of the tools' names: the same tools always get the same names."""
    modules: dict[tuple[str, str], str] = {("dot", DOT_SERVER): DOT_SERVER}
    taken_modules: set[str] = {DOT_SERVER}
    functions: dict[str, set[str]] = {}
    tools: list[CatalogTool] = []
    for exposed in sorted(specs):
        kind, server, tool = _server_and_tool(exposed)
        if (kind, server) not in modules:
            base: str = identifier(server)
            modules[(kind, server)] = _unique(f"{base}_mcp" if base == DOT_SERVER else base, taken_modules)
        module: str = modules[(kind, server)]
        function: str = _unique(identifier(tool), functions.setdefault(module, set()))
        spec: ToolSpec = specs[exposed]
        tools.append(CatalogTool(kind, server, module, function, exposed, spec.description, spec.parameters))
    return tuple(tools)


def servers_of(tools: tuple[CatalogTool, ...]) -> list[JsonObject]:
    """The servers of ``tools`` with how many tools each has: Dot's own first, then the MCP servers by name."""
    counts: dict[str, JsonObject] = {}
    for tool in tools:
        entry: JsonObject = counts.setdefault(
            tool.module,
            {
                "server": tool.module,
                "name": tool.server,
                "kind": tool.kind,
                "tools": 0,
            },
        )
        entry["tools"] = int(str(entry["tools"])) + 1
    return sorted(counts.values(), key=lambda entry: (entry["kind"] != "dot", str(entry["server"])))


def render(tools: tuple[CatalogTool, ...]) -> bytes:
    """``dot_tools/_catalog.json``: the servers and the tools, as the child's ``search_tools`` reads them."""
    body: JsonObject = {
        "servers": list(servers_of(tools)),
        "tools": [
            {
                "kind": tool.kind,
                "server": tool.server,
                "module": tool.module,
                "function": tool.function,
                "tool": tool.tool,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ],
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
