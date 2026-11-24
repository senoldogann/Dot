"""``dot_tools``, the package the code of a ``code_run`` imports its tools from (item 1502).

Standard library only: the daemon copies this file into the run's tools folder as ``dot_tools/__init__.py``, beside
``_bridge.py`` (the way out), ``_catalog.json`` (every tool the run may call from code, with its description and its
parameters) and ``servers/<server>/<tool>.py`` (one function for each tool, a call into the bridge). Dot's own tools are
the server ``dot``; the tools of an MCP server are the server's, under its own name with a ``-`` read as ``_``.

    from dot_tools.servers.dot import read_file
    from dot_tools.servers.github import list_issues

A tool is called with its parameters as keywords, and answers what it answered: the JSON it said when it said JSON, else
its text. A call the run's rules refuse raises ``ToolRefused``, one that fails ``ToolError``.
"""

import json
import os
import re
from typing import Any

from dot_tools._bridge import BridgeError, ToolError, ToolRefused  # type: ignore[import-not-found]

__all__ = ["BridgeError", "ToolError", "ToolRefused", "list_servers", "search_tools"]

_CATALOG: dict[str, Any] | None = None
_WORDS: re.Pattern[str] = re.compile(r"[a-z0-9]+")
# How many tools a search names at most: a longer list is a dump, and only what is printed reaches the model.
_MOST: int = 25
_DETAILS: tuple[str, ...] = ("name", "description", "schema")


def _catalog() -> dict[str, Any]:
    global _CATALOG
    if _CATALOG is None:
        with open(os.path.join(os.path.dirname(__file__), "_catalog.json"), encoding="utf-8") as handle:
            _CATALOG = json.load(handle)
    return _CATALOG


def list_servers() -> list[dict[str, Any]]:
    """The servers whose tools can be called from code: Dot's own (``dot``) and each MCP server the run has, with how
    many tools each has. ``server`` is the name to import from: ``from dot_tools.servers.<server> import <tool>``."""
    return [dict(server) for server in _catalog()["servers"]]


def _score(words: list[str], tool: dict[str, Any]) -> int:
    named: str = f"{tool['module']} {tool['function']}".lower()
    described: str = str(tool["description"]).lower()
    points: int = 0
    for word in words:
        if word in named:
            points += 3
        if word in described:
            points += 1
    return points


def search_tools(query: str = "", detail: str = "name", limit: int = 10) -> list[Any]:
    """Find tools by the words of ``query`` among their names and descriptions, best first, at most ``limit`` (and 25).
    ``detail`` is how much of each is returned: ``"name"`` is ``"<server>.<tool>"``, ``"description"`` adds what the
    tool does and how to import it, ``"schema"`` adds its parameters as a JSON schema. An empty ``query`` lists the
    first tools of every server. Only what the code prints reaches the model: print a few names, not the catalogue."""
    if detail not in _DETAILS:
        raise ValueError(f"detail is one of {', '.join(_DETAILS)}")
    words: list[str] = _WORDS.findall(query.lower())
    ranked: list[tuple[int, dict[str, Any]]] = []
    for tool in _catalog()["tools"]:
        points: int = _score(words, tool)
        if points > 0 or not words:
            ranked.append((points, tool))
    ranked.sort(key=lambda pair: -pair[0])
    found: list[Any] = []
    for _, tool in ranked[: max(1, min(limit, _MOST))]:
        name: str = f"{tool['module']}.{tool['function']}"
        if detail == "name":
            found.append(name)
            continue
        entry: dict[str, Any] = {
            "name": name,
            "import": f"from dot_tools.servers.{tool['module']} import {tool['function']}",
            "description": tool["description"],
        }
        if detail == "schema":
            entry["parameters"] = tool["parameters"]
        found.append(entry)
    return found
