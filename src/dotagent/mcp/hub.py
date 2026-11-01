"""Every configured MCP server, connected: the tools they offer, the way to call them, and those that could not start.
A server added on Customize › Connectors joins while Dot runs, and one removed there leaves (item 279)."""

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import httpx2
from mcp.types import CallToolResult
from pydantic import JsonValue

from dotagent.config import McpServerConfig
from dotagent.core.errors import DotError, McpError
from dotagent.core.logs import get_logger
from dotagent.core.paths import dot_home, log_dir, workspace_dir
from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.mcp.connection import McpConnection
from dotagent.mcp.launch import resolve_server_secrets, server_environment, server_headers
from dotagent.mcp.masking import masked_context
from dotagent.mcp.naming import PREFIX, require_distinct_names
from dotagent.mcp.purpose import READ_CHARS, purpose_of
from dotagent.mcp.signin import kept_sign_in
from dotagent.mcp.types import McpFailure, McpPurpose, McpTool
from dotagent.secrets import known_secrets
from dotagent.tools.schema import flatten_json_schema

log = get_logger(__name__)

_SHOWN_TOOL_NAMES: int = 60


@dataclass(frozen=True)
class _Connected:
    """A server that answered with its tools: the connection, how many tools it listed, those the model gets, and what
    it said of itself when it connected (its ``instructions``, the start of them; empty when it said nothing)."""

    connection: McpConnection
    listed: int
    tools: tuple[McpTool, ...]
    instructions: str


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
    """The tools of one server that the model gets: those on the allow list, or every one it lists without one, however
    many (with ``[agent] tool_surface = "grouped"`` a run turns the server's group on when it needs it)."""
    if not config.allow:
        return listed
    offered: dict[str, McpTool] = {tool.remote_name: tool for tool in listed}
    wanted: list[str] = list(dict.fromkeys(config.allow))
    missing: list[JsonValue] = [name for name in wanted if name not in offered]
    if missing:
        raise McpError(
            "the allow list names tools that the MCP server does not offer",
            {
                "server": config.name,
                "missing": missing,
                "offered": [name for name in sorted(offered)[:_SHOWN_TOOL_NAMES]],
            },
        )
    return tuple(offered[name] for name in wanted)


def tool_parameters(tool: McpTool) -> JsonObject:
    """The input schema of ``tool``, flattened as every provider takes it: a part that refers to itself again, nests
    deeper than Dot follows, or refers to nothing is cut to a loose one (item 671). ``McpError`` names a tool whose
    arguments are no object at all, or whose schema grows too large once flattened."""
    try:
        return flatten_json_schema(tool.input_schema)
    except ValueError as exc:
        raise McpError(
            "the input schema of an MCP tool cannot be used",
            {"server": tool.server, "tool": tool.remote_name, "error": str(exc)},
        ) from exc


def usable_tools(config: McpServerConfig, tools: tuple[McpTool, ...]) -> tuple[McpTool, ...]:
    """``tools`` but those whose schema no model takes (:func:`tool_parameters`), each left out alone and logged: the
    server's other tools serve (item 671; before, one such tool left its whole server out). A tool the ``allow`` list
    names, which the owner asked for, leaves its server out with the reason instead, and so does a server left with no
    tool: neither would serve as the owner set it up."""
    kept: list[McpTool] = []
    for tool in tools:
        try:
            tool_parameters(tool)
        except McpError as error:
            if tool.remote_name in config.allow:
                raise
            log.warning(
                "mcp_tool_left_out", server=tool.server, tool=tool.remote_name, error=error.context.get("error")
            )
            continue
        kept.append(tool)
    if tools and not kept:
        raise McpError(
            "no input schema of the MCP server's tools can be used",
            {"server": config.name, "tools": [tool.remote_name for tool in tools[:_SHOWN_TOOL_NAMES]]},
        )
    return tuple(kept)


def server_folder(config: McpServerConfig) -> Path:
    """The folder the server of ``config`` starts in: its own (``cwd``, item 637), else the workspace. A folder of Dot's
    own home is made when it is not there yet (the web-research server's data folder, which the server makes only when
    it first needs it); any other must be there already, or the server does not start, saying so."""
    if config.cwd is None:
        return workspace_dir()
    folder: Path = Path(config.cwd)
    # Judged where the folder really is: a ".." or a link out of Dot's home is not a folder of Dot's home.
    if folder.resolve().is_relative_to(dot_home().resolve()):
        folder.mkdir(parents=True, exist_ok=True)
    return folder


async def _connect_server(
    config: McpServerConfig, login_env: Mapping[str, str], auth: httpx2.Auth | None
) -> _Connected:
    """Connect to ``config``, signing in with ``auth``, and take its tools; the connection is closed again when they
    cannot be used."""
    secrets: dict[str, str] = await resolve_server_secrets(config)
    env: dict[str, str] = server_environment(config, login_env, secrets)
    headers: dict[str, str] = server_headers(config, secrets)
    connection: McpConnection = McpConnection(
        config, env, headers, auth, server_folder(config), server_log_path(config)
    )
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(connection.close)
        listed: tuple[McpTool, ...] = await connection.list_tools()
        tools: tuple[McpTool, ...] = usable_tools(config, select_tools(config, listed))
        require_distinct_names(tools)
        instructions: str = (connection.instructions() or "")[:READ_CHARS]
        cleanup.pop_all()
    log.info("mcp_server_ready", server=config.name, tools=len(tools), offered=len(listed))
    return _Connected(connection=connection, listed=len(listed), tools=tools, instructions=instructions)


async def _connect_kept(config: McpServerConfig, login_env: Mapping[str, str]) -> _Connected:
    """Connect to ``config`` as runs do: one that signs in (``oauth``) with the sign-in kept for it, never a new one."""
    auth: httpx2.Auth | None = await asyncio.to_thread(kept_sign_in, config) if config.oauth else None
    return await _connect_server(config, login_env, auth)


async def _offered_tools(config: McpServerConfig, login_env: Mapping[str, str]) -> tuple[McpTool, ...]:
    """Every tool ``config`` lists, whatever its allow list, connecting as runs do and closing the connection again."""
    auth: httpx2.Auth | None = await asyncio.to_thread(kept_sign_in, config) if config.oauth else None
    secrets: dict[str, str] = await resolve_server_secrets(config)
    env: dict[str, str] = server_environment(config, login_env, secrets)
    headers: dict[str, str] = server_headers(config, secrets)
    connection: McpConnection = McpConnection(
        config, env, headers, auth, server_folder(config), server_log_path(config)
    )
    try:
        return await connection.list_tools()
    finally:
        await connection.close()


class McpHub:
    """The connected servers and the tools they offer to the model, and the enabled servers left out at the start. A
    server connected or removed while Dot runs (:meth:`connect`, :meth:`disconnect`) changes the tools of the runs
    that start after; a run keeps those it started with. What each server said of itself when it connected is kept
    (``instructions``), so that the instructions of a run can say what a server is for without reaching it
    (:meth:`purposes`, item 659)."""

    def __init__(
        self,
        connections: Mapping[str, McpConnection],
        tools: tuple[McpTool, ...],
        failures: tuple[McpFailure, ...],
        configs: Mapping[str, McpServerConfig],
        login_env: Mapping[str, str],
        instructions: Mapping[str, str],
    ) -> None:
        self._connections: dict[str, McpConnection] = dict(connections)
        self.tools: tuple[McpTool, ...] = tools
        self.failures: tuple[McpFailure, ...] = failures
        self._configs: dict[str, McpServerConfig] = dict(configs)
        self._login_env: dict[str, str] = dict(login_env)
        self._instructions: dict[str, str] = dict(instructions)
        self._changing: asyncio.Lock = asyncio.Lock()

    def connected(self) -> tuple[str, ...]:
        """The names of the servers connected now."""
        return tuple(self._connections)

    def configs(self) -> dict[str, McpServerConfig]:
        """Every enabled server the hub has, connected or left out, by name."""
        return dict(self._configs)

    def purposes(self, held: Sequence[str]) -> dict[str, McpPurpose]:
        """What the line of each connected server's tool group says it is for (item 659), by the group's name: its
        ``description`` (the owner's, or Dot's for a server that comes with it), else what it said of itself when it
        connected (:func:`dotagent.mcp.purpose.purpose_of`) with the secrets ``held`` masked. A server with neither has
        none. It reads what the hub holds and reaches no server, so a run's start waits for none."""
        found: dict[str, McpPurpose] = {}
        for server in self._connections:
            tools: tuple[McpTool, ...] = tuple(tool for tool in self.tools if tool.server == server)
            purpose: McpPurpose | None = purpose_of(self._configs[server], self._instructions.get(server), tools, held)
            if purpose is not None:
                found[PREFIX + server] = purpose
        return found

    async def call(self, tool: McpTool, arguments: JsonObject) -> CallToolResult:
        connection: McpConnection | None = self._connections.get(tool.server)
        if connection is None:
            raise McpError(
                "the MCP server was removed from Dot while the run worked",
                {"server": tool.server, "tool": tool.remote_name},
            )
        # A tool the server declares read-only is called again once when its connection broke (item 513).
        return await connection.call_tool(tool.remote_name, arguments, tool.hints.read_only is True)

    async def call_unoffered(self, server: str, remote_name: str, arguments: JsonObject) -> CallToolResult:
        """Call the tool ``remote_name`` of the connected server ``server`` that its allow list keeps from the model
        (item 655: the web-research server's search, which web_search asks); taken as read-only, so a broken
        connection is tried again once. It judges neither the server, the tool nor the arguments: its one caller
        (``tools.web.web_research_search``) names them as constants, after Dot's own rule judged the query; a new
        caller must judge what it sends as a run's call is judged."""
        connection: McpConnection | None = self._connections.get(server)
        if connection is None:
            raise McpError("the MCP server is not connected", {"server": server, "tool": remote_name})
        return await connection.call_tool(remote_name, arguments, True)

    async def attempt(self, config: McpServerConfig, auth: httpx2.Auth | None) -> tuple[McpTool, ...]:
        """Connect to ``config`` once, signing in with ``auth``, and close the connection again: the tools the model
        would get from it. Raises when it cannot be used, as :func:`open_hub` would leave it out."""
        done: _Connected = await _connect_server(config, self._login_env, auth)
        await done.connection.close()
        return done.tools

    async def offered(self, config: McpServerConfig) -> tuple[McpTool, ...]:
        """Every tool ``config`` lists, for the owner to choose its allow list from."""
        return await _offered_tools(config, self._login_env)

    async def connect(self, config: McpServerConfig) -> tuple[McpTool, ...]:
        """Connect ``config`` as runs connect to it, in the place of a server of the same name, and return the tools the
        model gets from it. One that cannot be connected raises, and the hub stays as it was."""
        async with self._changing:
            done: _Connected = await _connect_kept(config, self._login_env)
            previous: McpConnection | None = self._connections.pop(config.name, None)
            self._connections[config.name] = done.connection
            self._configs[config.name] = config
            self._instructions[config.name] = done.instructions
            self.tools = (*(tool for tool in self.tools if tool.server != config.name), *done.tools)
            self.failures = tuple(failure for failure in self.failures if failure.server != config.name)
        if previous is not None:
            await previous.close()
        return done.tools

    async def disconnect(self, server: str) -> None:
        """Take the server ``server`` out, with its tools; nothing happens for one that is not here."""
        async with self._changing:
            connection: McpConnection | None = self._connections.pop(server, None)
            self._configs.pop(server, None)
            self._instructions.pop(server, None)
            self.tools = tuple(tool for tool in self.tools if tool.server != server)
            self.failures = tuple(failure for failure in self.failures if failure.server != server)
        if connection is not None:
            await connection.close()

    async def close(self) -> None:
        await asyncio.gather(*(connection.close() for connection in self._connections.values()))


def _failure(config: McpServerConfig, error: Exception) -> McpFailure:
    """The record of a server that could not start. An error that is not one of Dot's own is a fault in Dot or in the
    MCP library, logged with its traceback."""
    log_file: str = str(server_log_path(config))
    if isinstance(error, DotError):
        # The context holds the server's URL and arguments and the tail of what it printed, where a key may be (item
        # 558): the log, the status and the page say where it is and why it failed without it.
        detail: JsonObject = masked_context(error.context)
        log.warning("mcp_server_left_out", server=config.name, error=error.message, context=detail)
        return McpFailure(server=config.name, error=error.message, detail=detail, log=log_file)
    log.error("mcp_server_crashed", server=config.name, exc_info=error)
    words: str = redact_known(f"{type(error).__name__}: {error}", known_secrets(filled=True))
    return McpFailure(server=config.name, error=words, detail={}, log=log_file)


async def open_hub(configs: Sequence[McpServerConfig], login_env: Mapping[str, str]) -> McpHub:
    """Start every enabled server at once. One that cannot start is left out with the reason, and the others serve
    their tools, so Dot starts without the tools of a broken server. A cancellation stops every server again."""
    enabled: list[McpServerConfig] = [config for config in configs if config.enabled]
    tasks: list[asyncio.Task[_Connected]] = [
        asyncio.create_task(_connect_kept(config, login_env)) for config in enabled
    ]
    try:
        outcomes: list[_Connected | BaseException] = list(await asyncio.gather(*tasks, return_exceptions=True))
    except BaseException:
        # Cancelled while they start (the daemon stopping): the servers that had connected are stopped too, as the
        # others are by the cancellation (the review of 2026-11-01).
        for task in tasks:
            task.cancel()
        finished: list[_Connected] = [
            task.result() for task in tasks if task.done() and not task.cancelled() and task.exception() is None
        ]
        await asyncio.gather(*(done.connection.close() for done in finished), return_exceptions=True)
        raise
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
    return McpHub(
        {name: done.connection for name, done in connected.items()},
        tools,
        failures,
        {config.name: config for config in enabled},
        login_env,
        {name: done.instructions for name, done in connected.items()},
    )


async def _inspect(config: McpServerConfig, login_env: Mapping[str, str]) -> ServerReport:
    log_file: str = str(server_log_path(config))
    if not config.enabled:
        return ServerReport(server=config.name, enabled=False, listed=0, tools=(), error=None, detail={}, log=log_file)
    try:
        connected: _Connected = await _connect_kept(config, login_env)
    except Exception as exc:  # noqa: BLE001 - _failure logs it, and the report shows it on the server's card
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
