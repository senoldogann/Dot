"""A connection to one MCP server: start it (a command, over stdio) or reach it (a URL, over streamable HTTP or SSE),
list its tools, call them, and connect again after the connection died.

The client library wants its session entered and left from one task, so every server gets a task of its own that holds
the session open; callers use the session from any task. The standard error of a server Dot starts goes to a log file
next to Dot's own logs, never to the terminal.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import httpx2
from mcp import Client, MCPError, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import StreamableHTTPError, streamable_http_client
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT, CallToolResult, ListToolsResult, Tool, ToolAnnotations
from pydantic import JsonValue

from dotagent.config import McpServerConfig, McpTransport
from dotagent.core.errors import McpError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.mcp.naming import exposed_name
from dotagent.mcp.types import McpTool, ToolHints

log = get_logger(__name__)

_MAX_LIST_PAGES: int = 20
_STOP_GRACE_S: float = 8.0
_LOG_ROTATE_BYTES: int = 2_000_000
_TAIL_BYTES: int = 1500
_SHOWN_CAUSE_CHARS: int = 300
_SHOWN_ARGUMENT_CHARS: int = 500
# Connect, write and pool timeouts of a server reached at a URL; the read timeout is at least the call timeout, because
# a tool call is answered on a stream that stays silent while the tool works.
_HTTP_TIMEOUT_S: float = 30.0
_HTTP_READ_TIMEOUT_S: float = 300.0


class _Slot:
    """Where the owner task leaves the connected client for the code that waits for it."""

    def __init__(self) -> None:
        self.client: Client | None = None
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.stop: asyncio.Event = asyncio.Event()


@dataclass
class _Live:
    """A running server: its session, the task that holds the session open, and whether the connection broke."""

    client: Client
    owner: asyncio.Task[None]
    stop: asyncio.Event
    broken: bool


def _prepare_log(path: Path) -> int:
    """Rotate a log that grew too large and return the offset where the output of the coming start begins."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return 0
    size: int = path.stat().st_size
    if size > _LOG_ROTATE_BYTES:
        os.replace(path, path.with_name(f"{path.name}.1"))
        return 0
    return size


def _open_log(path: Path) -> TextIO:
    return path.open("a", encoding="utf-8")


def _read_tail(path: Path, offset: int) -> str:
    """The last lines the server wrote to its log since ``offset``."""
    if not path.exists():
        return ""
    size: int = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(offset, size - _TAIL_BYTES))
        data: bytes = handle.read()
    return data.decode("utf-8", errors="replace").strip()


def _leaf_errors(error: BaseException) -> list[BaseException]:
    """The client reports a failed start as exception groups inside exception groups; these are the actual errors."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaf_errors(inner)]
    return [error]


def _missing_directory(leaves: list[BaseException], cwd: Path) -> bool:
    """Whether the server did not start because its working directory is not there: the error of the change into the
    directory names the directory, that of a command not found names the command."""
    return any(
        isinstance(leaf, FileNotFoundError) and leaf.filename is not None and Path(os.fsdecode(leaf.filename)) == cwd
        for leaf in leaves
    )


def _startup_message(leaves: list[BaseException], cwd: Path) -> str:
    """Why a server Dot starts itself in ``cwd`` did not come up."""
    if _missing_directory(leaves, cwd):
        return "the working directory of the MCP server does not exist"
    if any(isinstance(leaf, FileNotFoundError) for leaf in leaves):
        return "the command of the MCP server was not found"
    if any(isinstance(leaf, PermissionError) for leaf in leaves):
        return "the command of the MCP server cannot be executed"
    if any(isinstance(leaf, MCPError) and leaf.code == CONNECTION_CLOSED for leaf in leaves):
        return "the MCP server closed the connection while starting; it probably exited"
    return "the MCP server could not be started"


def _connect_message(leaves: list[BaseException]) -> str:
    """Why a server reached at a URL did not accept the connection. The client library turns an HTTP error status into
    a protocol error without the status, so a refused connection also means: check the url and the headers."""
    if any(isinstance(leaf, httpx2.TransportError) for leaf in leaves):
        return "the MCP server could not be reached"
    if any(isinstance(leaf, StreamableHTTPError) for leaf in leaves):
        return "the MCP server did not answer as a streamable HTTP server"
    if any(isinstance(leaf, MCPError) and leaf.code == CONNECTION_CLOSED for leaf in leaves):
        return "the MCP server closed the connection while connecting"
    return "the MCP server refused the connection; check the url, the headers and the transport"


async def _serve(slot: _Slot, client: Client) -> None:
    """Hand the connected client to the code that waits for it, then keep the session open until asked to stop."""
    slot.client = client
    slot.ready.set_result(None)
    await slot.stop.wait()


def _report_teardown(owner: asyncio.Task[None], server: str) -> None:
    """Problems while a server shuts down do not change the outcome of the work; they go to the log."""
    if owner.cancelled():
        return
    error: BaseException | None = owner.exception()
    if error is not None:
        log.warning("mcp_server_teardown_failed", server=server, error=truncate_middle(repr(error), _SHOWN_CAUSE_CHARS))


async def _release(live: _Live, server: str) -> None:
    """Ask the owner task to leave the session, which ends the server; cancel the task if it does not finish."""
    live.stop.set()
    await asyncio.wait({live.owner}, timeout=_STOP_GRACE_S)
    if not live.owner.done():
        live.owner.cancel()
        await asyncio.wait({live.owner})
    _report_teardown(live.owner, server)


def _to_tool(config: McpServerConfig, tool: Tool) -> McpTool:
    annotations: ToolAnnotations | None = tool.annotations
    hints: ToolHints = ToolHints(
        read_only=None if annotations is None else annotations.read_only_hint,
        destructive=None if annotations is None else annotations.destructive_hint,
        open_world=None if annotations is None else annotations.open_world_hint,
    )
    return McpTool(
        server=config.name,
        remote_name=tool.name,
        exposed_name=exposed_name(config.name, tool.name),
        description=tool.description or tool.title or tool.name,
        input_schema=tool.input_schema,
        hints=hints,
        startup_timeout_s=config.startup_timeout_s,
        call_timeout_s=config.call_timeout_s,
    )


class McpConnection:
    """One configured MCP server. It connects on first use and connects again on the next call after it died."""

    def __init__(
        self, config: McpServerConfig, env: dict[str, str], headers: dict[str, str], cwd: Path, log_path: Path
    ) -> None:
        self._config: McpServerConfig = config
        self._env: dict[str, str] = env
        self._headers: dict[str, str] = headers
        self._cwd: Path = cwd
        self._log_path: Path = log_path
        self._lock: asyncio.Lock = asyncio.Lock()
        self._live: _Live | None = None

    @property
    def name(self) -> str:
        return self._config.name

    def _context(self) -> JsonObject:
        """What identifies the server in an error: never its environment or its headers, which may hold secrets."""
        context: JsonObject = {
            "server": self._config.name,
            "transport": self._config.transport_kind(),
            "log": str(self._log_path),
        }
        if self._config.command is not None:
            context["command"] = self._config.command
            context["args"] = list(self._config.args)
        if self._config.url is not None:
            context["url"] = self._config.url
        return context

    def _again(self) -> str:
        return "starts it again" if self._config.command is not None else "connects again"

    async def _tail_since(self, offset: int) -> JsonObject:
        """The last lines a server Dot started wrote to its error log; a server reached at a URL has none."""
        if self._config.command is None:
            return {}
        return {"stderr_tail": await asyncio.to_thread(_read_tail, self._log_path, offset)}

    async def _hold(self, slot: _Slot) -> None:
        """Owner task: open the session over the configured transport and keep it open until asked to stop."""
        kind: McpTransport = self._config.transport_kind()
        command: str | None = self._config.command
        url: str | None = self._config.url
        if kind == "stdio" and command is not None:
            await self._hold_stdio(slot, command)
        elif kind == "http" and url is not None:
            await self._hold_http(slot, url)
        elif kind == "sse" and url is not None:
            await self._hold_sse(slot, url)
        else:
            raise McpError("the MCP server configuration names no way to reach it", self._context())

    async def _hold_stdio(self, slot: _Slot, command: str) -> None:
        params: StdioServerParameters = StdioServerParameters(
            command=command, args=list(self._config.args), env=self._env, cwd=self._cwd
        )
        with await asyncio.to_thread(_open_log, self._log_path) as errlog:
            async with Client(stdio_client(params, errlog=errlog), mode="auto") as client:
                await _serve(slot, client)

    def _read_timeout_s(self) -> float:
        return max(_HTTP_READ_TIMEOUT_S, self._config.call_timeout_s)

    async def _hold_http(self, slot: _Slot, url: str) -> None:
        timeout: httpx2.Timeout = httpx2.Timeout(_HTTP_TIMEOUT_S, read=self._read_timeout_s())
        async with (
            httpx2.AsyncClient(headers=self._headers, timeout=timeout) as http,
            Client(streamable_http_client(url, http_client=http), mode="auto") as client,
        ):
            await _serve(slot, client)

    async def _hold_sse(self, slot: _Slot, url: str) -> None:
        transport = sse_client(
            url, headers=self._headers, timeout=_HTTP_TIMEOUT_S, sse_read_timeout=self._read_timeout_s()
        )
        async with Client(transport, mode="auto") as client:
            await _serve(slot, client)

    async def _start(self) -> _Live:
        offset: int = await asyncio.to_thread(_prepare_log, self._log_path)
        slot: _Slot = _Slot()
        owner: asyncio.Task[None] = asyncio.create_task(self._hold(slot), name=f"mcp-{self.name}")
        waiting: set[asyncio.Future[None]] = {owner, slot.ready}
        await asyncio.wait(waiting, timeout=self._config.startup_timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if slot.client is not None:
            log.info("mcp_server_started", server=self.name, protocol=slot.client.protocol_version)
            return _Live(client=slot.client, owner=owner, stop=slot.stop, broken=False)
        if not owner.done():
            owner.cancel()
            await asyncio.wait({owner})
            _report_teardown(owner, self.name)
            raise McpError(
                "the MCP server did not answer in time",
                {
                    **self._context(),
                    "startup_timeout_s": self._config.startup_timeout_s,
                    **await self._tail_since(offset),
                },
            )
        try:
            owner.result()
        except (OSError, ValueError, MCPError, StreamableHTTPError, httpx2.HTTPError, BaseExceptionGroup) as exc:
            leaves: list[BaseException] = _leaf_errors(exc)
            causes: list[JsonValue] = [
                truncate_middle(f"{type(leaf).__name__}: {leaf}", _SHOWN_CAUSE_CHARS) for leaf in leaves
            ]
            context: JsonObject = {**self._context(), "causes": causes, **await self._tail_since(offset)}
            if _missing_directory(leaves, self._cwd):
                context["cwd"] = str(self._cwd)
            elif any(isinstance(leaf, FileNotFoundError) for leaf in leaves):
                context["searched_path"] = self._env.get("PATH")
            message: str = (
                _startup_message(leaves, self._cwd) if self._config.command is not None else _connect_message(leaves)
            )
            raise McpError(message, context) from exc
        raise McpError("the MCP server task ended before it connected", self._context())

    async def _ensure_live(self) -> _Live:
        async with self._lock:
            current: _Live | None = self._live
            if current is not None and not current.broken and not current.owner.done():
                return current
            if current is not None:
                log.warning("mcp_server_restart", server=self.name)
                await _release(current, self.name)
                self._live = None
            started: _Live = await self._start()
            self._live = started
            return started

    async def _lost(self, live: _Live, step: str, error: MCPError) -> McpError:
        """Remember that the connection broke so the next call starts the server again."""
        live.broken = True
        log.warning("mcp_server_connection_lost", server=self.name, step=step)
        return McpError(
            f"the MCP server closed the connection while {step}; the next call {self._again()}",
            {**self._context(), "code": error.code, **await self._tail_since(0)},
        )

    async def list_tools(self) -> tuple[McpTool, ...]:
        """Every tool the server offers, following its pagination."""
        live: _Live = await self._ensure_live()
        listed: list[Tool] = []
        cursor: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            try:
                async with asyncio.timeout(self._config.startup_timeout_s):
                    page: ListToolsResult = await live.client.list_tools(cursor=cursor)
            except TimeoutError as exc:
                raise McpError(
                    "the MCP server did not list its tools in time",
                    {**self._context(), "timeout_s": self._config.startup_timeout_s},
                ) from exc
            except MCPError as exc:
                if exc.code == CONNECTION_CLOSED:
                    raise await self._lost(live, "listing its tools", exc) from exc
                raise McpError(
                    "the MCP server could not list its tools",
                    {**self._context(), "code": exc.code, "error": exc.message},
                ) from exc
            listed.extend(page.tools)
            if page.next_cursor is None:
                return tuple(_to_tool(self._config, tool) for tool in listed)
            cursor = page.next_cursor
        raise McpError(
            "the MCP server lists more pages of tools than supported", {**self._context(), "pages": _MAX_LIST_PAGES}
        )

    async def call_tool(self, name: str, arguments: JsonObject) -> CallToolResult:
        """Run a tool. A call that failed is never repeated: the tool may already have done part of its work."""
        live: _Live = await self._ensure_live()
        try:
            return await live.client.call_tool(name, arguments, read_timeout_seconds=self._config.call_timeout_s)
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                raise McpError(
                    "the MCP tool call timed out",
                    {**self._context(), "tool": name, "timeout_s": self._config.call_timeout_s},
                ) from exc
            if exc.code == CONNECTION_CLOSED:
                raise await self._lost(live, f"running {name}", exc) from exc
            shown: str = truncate_middle(redact_secrets(dumps_compact(arguments)), _SHOWN_ARGUMENT_CHARS)
            raise McpError(
                "the MCP server rejected the call",
                {**self._context(), "tool": name, "code": exc.code, "error": exc.message, "arguments": shown},
            ) from exc

    async def close(self) -> None:
        async with self._lock:
            live: _Live | None = self._live
            self._live = None
        if live is not None:
            await _release(live, self.name)
