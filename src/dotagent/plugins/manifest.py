"""Reading a plugin's files into Dot's own configuration: the manifest, the MCP servers and the variables they use."""

import json
import re
from pathlib import Path

from pydantic import ValidationError

from dotagent.config import MCP_NAME_PATTERN, McpServerConfig, McpTransport
from dotagent.core.errors import PluginError
from dotagent.core.types import FrozenModel
from dotagent.plugins.types import JsonTransport, McpServerJson, McpServersFile, PluginManifest

_VARIABLE: re.Pattern[str] = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_SERVER_NAME: re.Pattern[str] = re.compile(MCP_NAME_PATTERN)
_USER_CONFIG_PREFIX: str = "user_config."
_TRANSPORTS: dict[JsonTransport, McpTransport] = {
    "stdio": "stdio",
    "http": "http",
    "sse": "sse",
    "streamable-http": "http",
}


class PluginVariables(FrozenModel):
    """What ``${CLAUDE_PLUGIN_ROOT}``, ``${CLAUDE_PLUGIN_DATA}`` and ``${CLAUDE_PROJECT_DIR}`` stand for."""

    root: Path
    data: Path
    project: Path

    def values(self) -> dict[str, str]:
        return {
            "CLAUDE_PLUGIN_ROOT": str(self.root),
            "CLAUDE_PLUGIN_DATA": str(self.data),
            "CLAUDE_PROJECT_DIR": str(self.project),
        }


def _validation_errors(exc: ValidationError) -> str:
    return exc.json(include_url=False, include_context=False, include_input=False)


def _loaded_json(path: Path, text: str) -> object:
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PluginError("a plugin file is not valid JSON", {"path": str(path), "error": str(exc)}) from exc
    return loaded


def parse_manifest(path: Path, text: str) -> PluginManifest:
    try:
        return PluginManifest.model_validate(_loaded_json(path, text))
    except ValidationError as exc:
        raise PluginError(
            "plugin.json does not follow the Claude Code plugin layout",
            {"path": str(path), "errors": _validation_errors(exc)},
        ) from exc


def parse_servers_file(path: Path, text: str) -> McpServersFile:
    try:
        return McpServersFile.model_validate(_loaded_json(path, text))
    except ValidationError as exc:
        raise PluginError(
            'the MCP servers of a plugin must be written as {"mcpServers": {...}}',
            {"path": str(path), "errors": _validation_errors(exc)},
        ) from exc


def expand_variables(value: str, variables: PluginVariables, path: Path) -> str:
    """The plugin variables filled in; any other ``${NAME}`` stays for Dot's own secret lookup."""
    known: dict[str, str] = variables.values()

    def replace(match: re.Match[str]) -> str:
        name: str = match.group(1)
        if name in known:
            return known[name]
        if name.startswith(_USER_CONFIG_PREFIX):
            raise PluginError(
                "a plugin asks for a user_config value, which Dot does not support",
                {"path": str(path), "variable": name},
            )
        return match.group(0)

    return _VARIABLE.sub(replace, value)


def expand_fully(value: str, variables: PluginVariables, path: Path, field: str) -> str:
    """Like :func:`expand_variables`, for a value that must be complete: Dot fills ``${NAME}`` only in env and
    headers."""
    expanded: str = expand_variables(value, variables, path)
    leftover: re.Match[str] | None = _VARIABLE.search(expanded)
    if leftover is not None:
        raise PluginError(
            "a plugin MCP server uses a variable Dot fills only in env and headers",
            {"path": str(path), "field": field, "variable": leftover.group(1)},
        )
    return expanded


def server_name(plugin: str, server: str) -> str:
    """``<plugin>-<server>`` in lower case with ``_`` and spaces as ``-``; it must satisfy Dot's server-name rule."""
    derived: str = f"{plugin}-{server}".lower().replace("_", "-").replace(" ", "-")
    if _SERVER_NAME.match(derived) is None:
        raise PluginError(
            "the name of a plugin MCP server does not fit Dot's server-name rule",
            {
                "plugin": plugin,
                "server": server,
                "derived": derived,
                "rule": "a lower-case letter, then lower-case letters, digits and hyphens; at most 24 characters",
            },
        )
    return derived


def to_server_config(
    plugin: str, server: str, entry: McpServerJson, variables: PluginVariables, path: Path
) -> McpServerConfig:
    """Dot's configuration of one plugin server, with the plugin variables filled in."""
    name: str = server_name(plugin, server)
    transport: McpTransport | None = None if entry.transport is None else _TRANSPORTS[entry.transport]
    try:
        return McpServerConfig(
            name=name,
            command=None if entry.command is None else expand_fully(entry.command, variables, path, "command"),
            args=[expand_fully(argument, variables, path, "args") for argument in entry.args],
            env={key: expand_variables(value, variables, path) for key, value in entry.env.items()},
            url=None if entry.url is None else expand_fully(entry.url, variables, path, "url"),
            headers={key: expand_variables(value, variables, path) for key, value in entry.headers.items()},
            transport=transport,
        )
    except ValidationError as exc:
        raise PluginError(
            "a plugin MCP server is not valid for Dot",
            {"path": str(path), "server": server, "errors": _validation_errors(exc)},
        ) from exc
