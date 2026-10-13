"""The name the model uses for an MCP tool: ``mcp__<server>__<tool>`` with letters, digits, ``_`` and ``-`` only."""

import hashlib
import re

from pydantic import JsonValue

from dotagent.core.errors import McpError
from dotagent.core.types import JsonObject
from dotagent.mcp.types import McpTool

PREFIX: str = "mcp__"
MAX_NAME_CHARS: int = 64
_HASH_CHARS: int = 8
_OUTSIDE_ALPHABET: re.Pattern[str] = re.compile(r"[^A-Za-z0-9_-]")


def exposed_name(server: str, tool: str) -> str:
    """Providers accept 64 characters of ``[A-Za-z0-9_-]``; a longer name keeps its start and ends in a hash."""
    full: str = f"{PREFIX}{server}__{_OUTSIDE_ALPHABET.sub('_', tool)}"
    if len(full) <= MAX_NAME_CHARS:
        return full
    digest: str = hashlib.sha256(f"{server}\x00{tool}".encode()).hexdigest()[:_HASH_CHARS]
    return f"{full[: MAX_NAME_CHARS - _HASH_CHARS - 1]}_{digest}"


def require_distinct_names(tools: tuple[McpTool, ...]) -> None:
    """Replacing characters can make two tools of one server share a name; the model could not tell them apart. Tools
    of two servers never do: a server's name has no ``_``, so ``mcp__<server>__`` keeps them apart."""
    owners: dict[str, list[JsonValue]] = {}
    for tool in tools:
        owners.setdefault(tool.exposed_name, []).append(f"{tool.server}/{tool.remote_name}")
    clashes: JsonObject = {name: sources for name, sources in owners.items() if len(sources) > 1}
    if clashes:
        raise McpError("MCP tools would share one name", {"clashes": clashes})
