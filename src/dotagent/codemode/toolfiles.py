"""The tools of a run as files (item 1502): a function for each tool a code may call, in ``dot_tools.servers``.

The code of a ``code_run`` finds its tools as it finds any library: ``from dot_tools.servers.dot import read_file``,
``from dot_tools.servers.github import list_issues``. Each tool is a file, ``servers/<server>/<tool>.py``, with one
function: a signature of keyword-only parameters made from the tool's JSON schema (a required parameter has no
default, an optional one is ``None`` and is left out of the call when it is), the tool's description and its parameters
as the docstring, and one call into the bridge. A server's ``__init__`` imports a tool's file when it is asked for, so
that code that needs two tools of a server of two hundred reads two files. No definition of any of them goes into a
prompt: the code finds what it needs with ``dot_tools.search_tools`` and reads it from the files.

What is written is made from the catalogue (:mod:`dotagent.codemode.catalog`), whose names are valid in Python and the
same for the same tools. The text of a description is third parties' words for an MCP tool: it is put in the file as a
string literal made by ``repr`` and read as a docstring, never as code, however it is written.
"""

import hashlib
import keyword
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from dotagent.codemode.catalog import CatalogTool, build, identifier, render
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolSpec

# A description longer than this is cut in the docstring (the catalogue holds it whole): a file is read by ``help``, and
# the longest descriptions of MCP tools are pages.
DOC_CHARS: int = 6000
# A parameter's own words in the docstring are cut to this many characters.
PARAMETER_DOC_CHARS: int = 400
# The marker that says which tools the files were made from, written after them; the form of the files is part of what
# it says, so that a run that outlives an upgrade is written again.
DIGEST_FILE: tuple[str, ...] = ("dot_tools", ".digest")
FORMAT: int = 1
_SCALARS: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
    "array": "list[Any]",
    "object": "dict[str, Any]",
}
_BRIDGE_CALL: str = "_dot_call"
_RESERVED: frozenset[str] = frozenset({_BRIDGE_CALL, "Any", "Literal"})


@dataclass(frozen=True)
class Parameter:
    """One parameter of a tool as the function has it: the name in Python, the name the tool knows it by, whether the
    tool requires it, its annotation and its words."""

    name: str
    wire: str
    required: bool
    annotation: str
    description: str


@dataclass(frozen=True)
class Package:
    """What a run's tools are as files: ``files`` (by their parts below the tools folder) and the ``digest`` of the
    tools they were made from, which is the marker written last."""

    digest: str
    files: dict[tuple[str, ...], bytes]


def _literal(value: JsonValue) -> str | None:
    """``value`` as the source of a ``Literal`` member, or ``None`` when Python has none for it."""
    return repr(value) if value is None or isinstance(value, bool | int | str) else None


def _union(options: list[str]) -> str:
    unique: list[str] = list(dict.fromkeys(options))
    return "Any" if "Any" in unique or not unique else " | ".join(unique)


def annotation(node: JsonValue) -> str:
    """The annotation of a JSON schema node: the type it names, a ``Literal`` of its enum, the union of its
    alternatives, a list of what its items are; ``Any`` for what it does not say or says in a way Python has no name
    for."""
    if not isinstance(node, dict):
        return "Any"
    values: JsonValue = node.get("enum")
    if isinstance(values, list) and values:
        members: list[str | None] = [_literal(value) for value in values]
        if all(member is not None for member in members):
            return f"Literal[{', '.join(str(member) for member in members)}]"
        return "Any"
    constant: JsonValue = node.get("const")
    if constant is not None and _literal(constant) is not None:
        return f"Literal[{_literal(constant)}]"
    for key in ("anyOf", "oneOf"):
        options: JsonValue = node.get(key)
        if isinstance(options, list) and options:
            return _union([annotation(option) for option in options])
    kind: JsonValue = node.get("type")
    if isinstance(kind, list):
        return _union([annotation({**node, "type": one}) for one in kind])
    if kind == "array":
        items: JsonValue = node.get("items")
        return f"list[{annotation(items)}]" if isinstance(items, dict) else "list[Any]"
    return _SCALARS.get(kind, "Any") if isinstance(kind, str) else "Any"


def parameters_of(schema: JsonObject) -> tuple[Parameter, ...]:
    """The parameters of a tool from its schema, in its order, each with a name valid in Python and unique."""
    properties: JsonValue = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    required: JsonValue = schema.get("required")
    needed: set[str] = {name for name in required if isinstance(name, str)} if isinstance(required, list) else set()
    taken: set[str] = set(_RESERVED)
    found: list[Parameter] = []
    for wire, node in properties.items():
        name: str = identifier(wire)
        while name in taken or keyword.iskeyword(name):
            name = f"{name}_"
        taken.add(name)
        words: JsonValue = node.get("description") if isinstance(node, dict) else None
        found.append(
            Parameter(
                name=name,
                wire=wire,
                required=wire in needed,
                annotation=annotation(node),
                description=words.strip() if isinstance(words, str) else "",
            )
        )
    return tuple(found)


def _takes_more(schema: JsonObject) -> bool:
    """Whether the tool takes parameters beyond those it names (``additionalProperties`` that is not false)."""
    more: JsonValue = schema.get("additionalProperties")
    return more is True or isinstance(more, dict)


def docstring(tool: CatalogTool, parameters: tuple[Parameter, ...]) -> str:
    """The text ``help`` shows of a tool: its description, the name the bridge calls it by, and its parameters."""
    description: str = tool.description.strip()
    if len(description) > DOC_CHARS:
        description = f"{description[:DOC_CHARS]}\n[... the rest is in dot_tools.search_tools(detail='description')]"
    lines: list[str] = [description or tool.function, "", f"Tool: {tool.tool}"]
    if parameters:
        lines += ["", "Parameters (keywords):"]
        for parameter in parameters:
            words: str = " ".join(parameter.description.split())[:PARAMETER_DOC_CHARS]
            kind: str = "required" if parameter.required else "optional"
            lines.append(f"  {parameter.name}: {parameter.annotation}, {kind}" + (f". {words}" if words else ""))
    return "\n".join(lines)


def tool_source(tool: CatalogTool) -> str:
    """The file of one tool: a function that calls the bridge with the arguments it was given, the ones it was not
    given left out."""
    parameters: tuple[Parameter, ...] = parameters_of(tool.parameters)
    takes_more: bool = _takes_more(tool.parameters)
    signature: list[str] = []
    for parameter in parameters:
        if parameter.required:
            signature.append(f"{parameter.name}: {parameter.annotation}")
        else:
            signature.append(f"{parameter.name}: {_optional(parameter.annotation)} = None")
    if takes_more:
        signature.append("**more: Any")
    pairs: str = ", ".join(f"{parameter.wire!r}: {parameter.name}" for parameter in parameters)
    keywords: str = "*, " if parameters else ""
    head: str = f"def {tool.function}({keywords}{', '.join(signature)}) -> Any:"
    merged: str = f"{{**more, **{{{pairs}}}}}" if takes_more else f"{{{pairs}}}"
    kept: str = "{key: value for key, value in arguments.items() if value is not None}"
    return "\n".join(
        [
            f'"""The tool {tool.tool}: see help({tool.function})."""',
            "from typing import Any, Literal",
            "",
            f"from dot_tools._bridge import call as {_BRIDGE_CALL}",
            "",
            "",
            head,
            f"    {docstring(tool, parameters)!r}",
            f"    arguments: dict[str, Any] = {merged}",
            f"    return {_BRIDGE_CALL}({tool.tool!r}, {kept})",
            "",
        ]
    )


def _optional(text: str) -> str:
    return text if text == "Any" or text.endswith("| None") or text == "None" else f"{text} | None"


def _server_init(module: str, functions: list[str], kind: str, name: str) -> str:
    """A server's ``__init__``: its tools by name, each imported when it is asked for."""
    where: str = "Dot's own tools" if kind == "dot" else f"the tools of the MCP server {name}"
    return "\n".join(
        [
            f'"""{where}: one function for each, imported when it is asked for."""',
            "import importlib",
            "",
            f"_TOOLS = {tuple(sorted(functions))!r}",
            "",
            "",
            "def __getattr__(name):",
            "    if name in _TOOLS:",
            '        function = getattr(importlib.import_module(f"{__name__}.{name}"), name)',
            "        globals()[name] = function",
            "        return function",
            "    raise AttributeError(f'{__name__} has no tool {name!r}: dot_tools.search_tools finds tools')",
            "",
            "",
            "def __dir__():",
            "    return list(_TOOLS)",
            "",
        ]
    )


def package_of(specs: Mapping[str, ToolSpec]) -> Package:
    """The files of the tools ``specs``: the catalogue, a file for each tool, an ``__init__`` for each server, and the
    digest of what they were made from."""
    tools: tuple[CatalogTool, ...] = build(specs)
    files: dict[tuple[str, ...], bytes] = {
        ("dot_tools", "_catalog.json"): render(tools),
        ("dot_tools", "servers", "__init__.py"): b"",
    }
    by_module: dict[str, list[CatalogTool]] = {}
    for tool in tools:
        by_module.setdefault(tool.module, []).append(tool)
        files[("dot_tools", "servers", tool.module, f"{tool.function}.py")] = tool_source(tool).encode("utf-8")
    for module, members in by_module.items():
        files[("dot_tools", "servers", module, "__init__.py")] = _server_init(
            module, [member.function for member in members], members[0].kind, members[0].server
        ).encode("utf-8")
    digest: str = hashlib.sha256(files[("dot_tools", "_catalog.json")] + f"|{FORMAT}".encode()).hexdigest()
    files[DIGEST_FILE] = digest.encode("ascii")
    return Package(digest=digest, files=files)
