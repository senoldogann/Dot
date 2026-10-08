"""Every configured MCP server, connected: the tools they offer and the way to call them."""

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

from mcp.types import CallToolResult
from pydantic import JsonValue

from dotagent.config import McpServerConfig
from dotagent.core.errors import DotError, McpError
from dotagent.core.logs import get_logger
from dotagent.core.paths import log_dir, workspace_dir
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.mcp.connection import McpConnection
from dotagent.mcp.launch import resolve_server_secrets, server_environment, server_headers
from dotagent.mcp.naming import require_distinct_names
from dotagent.mcp.types import McpTool

log = get_logger(__name__)

MAX_UNFILTERED_TOOLS: int = 30
_SHOWN_TOOL_NAMES: int = 60


@dataclass(frozen=True)
class _Connected:
    connection: McpConnection
    listed: int
    tools: tuple[McpTool, ...]


class ServerReport(FrozenModel):
    """What connecting to one configured server showed: the tools the model would get, or why it failed."""

    server: str
    enabled: bool
    listed: int
    tools: tuple[McpTool, ...]
    error: str | None
    detail: JsonObject
    log: str


def server_log_path(config: McpServerConfig) -> Path:
    return log_dir() / f"mcp-{config.name}.log"


def select_tools(config: McpServerConfig, listed: tuple[McpTool, ...]) -> tuple[McpTool, ...]:
    """The tools of one server that the model gets: those on the allow list, or all of them when there are few."""
    offered: dict[str, McpTool] = {tool.remote_name: tool for tool in listed}
    names: list[JsonValue] = [name for name in sorted(offered)[:_SHOWN_TOOL_NAMES]]
    if config.allow:
        wanted: list[str] = list(dict.fromkeys(config.allow))
        missing: list[JsonValue] = [name for name in wanted if name not in offered]
        if missing:
            raise McpError(
                "the allow list names tools that the MCP server does not offer",
                {"server": config.name, "missing": missing, "offered": names},
            )
        return tuple(offered[name] for name in wanted)
    if len(listed) > MAX_UNFILTERED_TOOLS:
        raise McpError(
            "the MCP server offers too many tools to hand them all to the model; choose some with `allow`",
            {"server": config.name, "tools": len(listed), "limit": MAX_UNFILTERED_TOOLS, "offered": names},
        )
    return listed


async def _connect_server(config: McpServerConfig, login_env: Mapping[str, str]) -> _Connected:
    secrets: dict[str, str] = await resolve_server_secrets(config)
    env: dict[str, str] = server_environment(config, login_env, secrets)
    headers: dict[str, str] = server_headers(config, secrets)
    connection: McpConnection = McpConnection(config, env, headers, workspace_dir(), server_log_path(config))
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(connection.close)
        listed: tuple[McpTool, ...] = await connection.list_tools()
        tools: tuple[McpTool, ...] = select_tools(config, listed)
        cleanup.pop_all()
    log.info("mcp_server_ready", server=config.name, tools=len(tools), offered=len(listed))
    return _Connected(connection=connection, listed=len(listed), tools=tools)


class McpHub:
    """The connected servers and the tools they offer to the model."""

    def __init__(self, connections: Mapping[str, McpConnection], tools: tuple[McpTool, ...]) -> None:
        self._connections: dict[str, McpConnection] = dict(connections)
        self.tools: tuple[McpTool, ...] = tools

    async def call(self, tool: McpTool, arguments: JsonObject) -> CallToolResult:
        return await self._connections[tool.server].call_tool(tool.remote_name, arguments)

    async def close(self) -> None:
        await asyncio.gather(*(connection.close() for connection in self._connections.values()))


async def open_hub(configs: Sequence[McpServerConfig], login_env: Mapping[str, str]) -> McpHub:
    """Start every enabled server at once. When any of them fails, the others are stopped again and the error raised."""
    enabled: list[McpServerConfig] = [config for config in configs if config.enabled]
    outcomes: list[_Connected | BaseException] = list(
        await asyncio.gather(*(_connect_server(config, login_env) for config in enabled), return_exceptions=True)
    )
    connected: list[_Connected] = [outcome for outcome in outcomes if isinstance(outcome, _Connected)]
    failures: list[tuple[str, BaseException]] = [
        (config.name, outcome)
        for config, outcome in zip(enabled, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if failures:
        await asyncio.gather(*(done.connection.close() for done in connected))
        unexpected: list[BaseException] = [error for _, error in failures if not isinstance(error, DotError)]
        if unexpected:
            raise unexpected[0]
        if len(failures) == 1:
            raise failures[0][1]
        problems: JsonObject = {name: str(error) for name, error in failures}
        raise McpError("several MCP servers failed to start", {"failures": problems})
    tools: tuple[McpTool, ...] = tuple(tool for done in connected for tool in done.tools)
    require_distinct_names(tools)
    return McpHub({config.name: done.connection for config, done in zip(enabled, connected, strict=True)}, tools)


async def _inspect(config: McpServerConfig, login_env: Mapping[str, str]) -> ServerReport:
    log_file: str = str(server_log_path(config))
    if not config.enabled:
        return ServerReport(server=config.name, enabled=False, listed=0, tools=(), error=None, detail={}, log=log_file)
    try:
        connected: _Connected = await _connect_server(config, login_env)
    except DotError as exc:
        return ServerReport(
            server=config.name,
            enabled=True,
            listed=0,
            tools=(),
            error=exc.message,
            detail=exc.context,
            log=log_file,
        )
    await connected.connection.close()
    return ServerReport(
        server=config.name,
        enabled=True,
        listed=connected.listed,
        tools=connected.tools,
        error=None,
        detail={},
        log=log_file,
    )


async def inspect_servers(configs: Sequence[McpServerConfig], login_env: Mapping[str, str]) -> tuple[ServerReport, ...]:
    """Connect to each server once and report what the model would get from it; failures are reported, not raised."""
    return tuple(await asyncio.gather(*(_inspect(config, login_env) for config in configs)))
