"""Every configured MCP server, connected: the tools they offer, the way to call them, and those that could not start."""

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
from dotagent.mcp.types import McpFailure, McpTool
from dotagent.tools.schema import flatten_json_schema

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


def tool_parameters(tool: McpTool) -> JsonObject:
    """The input schema of ``tool``, flattened as every provider takes it. ``McpError`` names a tool whose schema Dot
    cannot hand to a model: a reference that leads nowhere, or one that nests deeper than Dot follows (a tree whose
    nodes hold nodes)."""
    try:
        return flatten_json_schema(tool.input_schema)
    except ValueError as exc:
        raise McpError(
            "the input schema of an MCP tool cannot be used",
            {"server": tool.server, "tool": tool.remote_name, "error": str(exc), "hint": "leave it out with `allow`"},
        ) from exc


async def _connect_server(config: McpServerConfig, login_env: Mapping[str, str]) -> _Connected:
    secrets: dict[str, str] = await resolve_server_secrets(config)
    env: dict[str, str] = server_environment(config, login_env, secrets)
    headers: dict[str, str] = server_headers(config, secrets)
    connection: McpConnection = McpConnection(config, env, headers, workspace_dir(), server_log_path(config))
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(connection.close)
        listed: tuple[McpTool, ...] = await connection.list_tools()
        tools: tuple[McpTool, ...] = select_tools(config, listed)
        require_distinct_names(tools)
        # A tool that cannot be handed to a model leaves its server out, as a server that cannot start is left out.
        for tool in tools:
            tool_parameters(tool)
        cleanup.pop_all()
    log.info("mcp_server_ready", server=config.name, tools=len(tools), offered=len(listed))
    return _Connected(connection=connection, listed=len(listed), tools=tools)


class McpHub:
    """The connected servers and the tools they offer to the model, and the enabled servers left out at the start."""

    def __init__(
        self,
        connections: Mapping[str, McpConnection],
        tools: tuple[McpTool, ...],
        failures: tuple[McpFailure, ...],
    ) -> None:
        self._connections: dict[str, McpConnection] = dict(connections)
        self.tools: tuple[McpTool, ...] = tools
        self.failures: tuple[McpFailure, ...] = failures

    async def call(self, tool: McpTool, arguments: JsonObject) -> CallToolResult:
        return await self._connections[tool.server].call_tool(tool.remote_name, arguments)

    async def close(self) -> None:
        await asyncio.gather(*(connection.close() for connection in self._connections.values()))


def _failure(config: McpServerConfig, error: Exception) -> McpFailure:
    """The record of a server that could not start. An error that is not one of Dot's own is a fault in Dot or in the
    MCP library, logged with its traceback."""
    log_file: str = str(server_log_path(config))
    if isinstance(error, DotError):
        log.warning("mcp_server_left_out", server=config.name, error=error.message, context=error.context)
        return McpFailure(server=config.name, error=error.message, detail=error.context, log=log_file)
    log.error("mcp_server_crashed", server=config.name, exc_info=error)
    return McpFailure(server=config.name, error=f"{type(error).__name__}: {error}", detail={}, log=log_file)


async def open_hub(configs: Sequence[McpServerConfig], login_env: Mapping[str, str]) -> McpHub:
    """Start every enabled server at once. One that cannot start is left out with the reason, and the others serve
    their tools, so Dot starts without the tools of a broken server. A cancellation stops every server again."""
    enabled: list[McpServerConfig] = [config for config in configs if config.enabled]
    outcomes: list[_Connected | BaseException] = list(
        await asyncio.gather(*(_connect_server(config, login_env) for config in enabled), return_exceptions=True)
    )
    connected: dict[str, _Connected] = {
        config.name: outcome
        for config, outcome in zip(enabled, outcomes, strict=True)
        if isinstance(outcome, _Connected)
    }
    interrupted: list[BaseException] = [
        outcome for outcome in outcomes if isinstance(outcome, BaseException) and not isinstance(outcome, Exception)
    ]
    if interrupted:
        await asyncio.gather(*(done.connection.close() for done in connected.values()))
        raise interrupted[0]
    failures: tuple[McpFailure, ...] = tuple(
        _failure(config, outcome)
        for config, outcome in zip(enabled, outcomes, strict=True)
        if isinstance(outcome, Exception)
    )
    tools: tuple[McpTool, ...] = tuple(tool for done in connected.values() for tool in done.tools)
    return McpHub({name: done.connection for name, done in connected.items()}, tools, failures)


async def _inspect(config: McpServerConfig, login_env: Mapping[str, str]) -> ServerReport:
    log_file: str = str(server_log_path(config))
    if not config.enabled:
        return ServerReport(server=config.name, enabled=False, listed=0, tools=(), error=None, detail={}, log=log_file)
    try:
        connected: _Connected = await _connect_server(config, login_env)
    except Exception as exc:
        # Any failure, as :func:`open_hub` leaves the server out with it.
        failure: McpFailure = _failure(config, exc)
        return ServerReport(
            server=config.name,
            enabled=True,
            listed=0,
            tools=(),
            error=failure.error,
            detail=failure.detail,
            log=failure.log,
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
