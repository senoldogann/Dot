"""A connection to one MCP server: start it (a command, over stdio) or reach it (a URL, over streamable HTTP or SSE),
list its tools, call them, and connect again after the connection died.

The client library wants its session entered and left from one task, so every server gets a task of its own that holds
the session open; callers use the session from any task. The standard error of a server Dot starts goes to a log file
next to Dot's own logs, never to the terminal.
"""

import asyncio
import os
import time
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

import httpx2
from mcp import Client, MCPDeprecationWarning, MCPError, StdioServerParameters, stdio_client
from mcp.client.auth import OAuthFlowError
from mcp.client.sse import sse_client
from mcp.client.streamable_http import StreamableHTTPError, streamable_http_client
from mcp.types import (
    CONNECTION_CLOSED,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    Prompt,
    ReadResourceResult,
    Resource,
    Tool,
    ToolAnnotations,
)
from pydantic import JsonValue, ValidationError

from dotagent.config import McpServerConfig, McpTransport
from dotagent.core.errors import McpError, McpRejected
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.mcp.naming import exposed_name
from dotagent.mcp.types import McpCatalogue, McpPrompt, McpPromptArgument, McpResource, McpTool, ToolHints

# The protocol versions without a ping, from the mcp SDK's own types package (installed with it, not a dependency of
# Dot's): where a later SDK moves it, every session is pinged as before rather than the daemon failing to start.
MODERN_PROTOCOL_VERSIONS: tuple[str, ...]
try:
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS as _MODERN
except ImportError:  # pragma: no cover - the SDK this was written for has it
    MODERN_PROTOCOL_VERSIONS = ()
else:
    MODERN_PROTOCOL_VERSIONS = tuple(_MODERN)

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
# A session reached at a URL that was quiet this long is asked whether it is still there before it is used (item 513):
# the server or the network may have dropped it meanwhile, and the calls sent on it would all fail at once.
_QUIET_CHECK_S: float = 60.0
_PING_TIMEOUT_S: float = 10.0


class _Page(Protocol):
    @property
    def next_cursor(self) -> str | None: ...


class _ListRequest[P: _Page](Protocol):
    def __call__(self, *, cursor: str | None) -> Awaitable[P]: ...


class _Slot:
    """Where the owner task leaves the connected client for the code that waits for it."""

    def __init__(self) -> None:
        self.client: Client | None = None
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.stop: asyncio.Event = asyncio.Event()


@dataclass
class _Live:
    """A running server: its session, the task that holds the session open, whether the connection broke, and when
    (monotonic seconds) the session last answered."""

    client: Client
    owner: asyncio.Task[None]
    stop: asyncio.Event
    broken: bool
    answered_at: float


class _ConnectionLost(McpError):
    """The connection to the server broke while a request was out: the next request connects again."""


def _session_gone(error: MCPError) -> bool:
    """Whether ``error`` says the session cannot be used any more, so that the next request connects again rather than
    fail on the same session (item 513): its connection closed, or its server refused the request as one of no session
    it knows (``INVALID_REQUEST``). Servers word that last in their own ways ("Session not found", "Not Found: Session
    has been terminated", the client's own "Session terminated" for a 404 with no body), so the code alone decides; a
    request Dot sends whole is not invalid otherwise, and taking one for a gone session costs one connection more."""
    return error.code in (CONNECTION_CLOSED, INVALID_REQUEST)


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
    if any(isinstance(leaf, OAuthFlowError) for leaf in leaves):
        return "signing in to the MCP server failed"
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


def _page_tools(page: ListToolsResult) -> list[Tool]:
    return page.tools


def _page_resources(page: ListResourcesResult) -> list[Resource]:
    return page.resources


def _page_prompts(page: ListPromptsResult) -> list[Prompt]:
    return page.prompts


class McpConnection:
    """One configured MCP server. It connects on first use and connects again on the next call after it died. A server
    reached at a URL may sign in (``auth``, :mod:`dotagent.mcp.signin`), which adds its token to each request."""

    def __init__(
        self,
        config: McpServerConfig,
        env: dict[str, str],
        headers: dict[str, str],
        auth: httpx2.Auth | None,
        cwd: Path,
        log_path: Path,
    ) -> None:
        self._config: McpServerConfig = config
        self._env: dict[str, str] = env
        self._headers: dict[str, str] = headers
        self._auth: httpx2.Auth | None = auth
        self._cwd: Path = cwd
        self._log_path: Path = log_path
        self._lock: asyncio.Lock = asyncio.Lock()
        self._live: _Live | None = None
        self.catalogue: McpCatalogue = McpCatalogue()
        # Copied out under ``_lock`` when the session is replaced, so :meth:`instructions` does not read ``_live``
        # while a reconnect clears it.
        self._instructions: str | None = None

    @property
    def name(self) -> str:
        return self._config.name

    def _context(self) -> JsonObject:
        """What identifies the server in an error: never its environment or its headers, which may hold secrets."""
        context: JsonObject = {"server": self._config.name, "transport": self._config.transport_kind()}
        # A server reached at a URL writes no log of Dot's: only a server Dot starts has one to name.
        if self._config.command is not None:
            context["log"] = str(self._log_path)
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
            httpx2.AsyncClient(headers=self._headers, timeout=timeout, auth=self._auth) as http,
            Client(streamable_http_client(url, http_client=http), mode="auto") as client,
        ):
            await _serve(slot, client)

    async def _hold_sse(self, slot: _Slot, url: str) -> None:
        transport = sse_client(
            url,
            headers=self._headers,
            timeout=_HTTP_TIMEOUT_S,
            sse_read_timeout=self._read_timeout_s(),
            auth=self._auth,
        )
        async with Client(transport, mode="auto") as client:
            await _serve(slot, client)

    async def _start(self) -> _Live:
        offset: int = await asyncio.to_thread(_prepare_log, self._log_path)
        slot: _Slot = _Slot()
        owner: asyncio.Task[None] = asyncio.create_task(self._hold(slot), name=f"mcp-{self.name}")
        waiting: set[asyncio.Future[None]] = {owner, slot.ready}
        try:
            await asyncio.wait(waiting, timeout=self._config.startup_timeout_s, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            # A call cancelled while its server starts (a stop, a tool's time) takes the server down with it: kept
            # nowhere, it would run until the daemon ends (the review of every module).
            owner.cancel()
            await asyncio.wait({owner})
            raise
        if slot.client is not None:
            log.info("mcp_server_started", server=self.name, protocol=slot.client.protocol_version)
            return _Live(client=slot.client, owner=owner, stop=slot.stop, broken=False, answered_at=time.monotonic())
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
        except (
            OSError,
            ValueError,
            MCPError,
            StreamableHTTPError,
            httpx2.HTTPError,
            OAuthFlowError,
            McpError,
            BaseExceptionGroup,
        ) as exc:
            leaves: list[BaseException] = _leaf_errors(exc)
            causes: list[JsonValue] = [
                truncate_middle(f"{type(leaf).__name__}: {leaf}", _SHOWN_CAUSE_CHARS) for leaf in leaves
            ]
            context: JsonObject = {**self._context(), "causes": causes, **await self._tail_since(offset)}
            # Dot's own sign-in ended the connection (no sign-in kept, one the owner did not finish): its words say why.
            own: McpError | None = next((leaf for leaf in leaves if isinstance(leaf, McpError)), None)
            if own is not None:
                raise McpError(own.message, {**context, **own.context}) from exc
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
            if current is not None and not current.broken and not current.owner.done() and await self._answers(current):
                return current
            if current is not None:
                log.warning("mcp_server_restart", server=self.name)
                await _release(current, self.name)
                self._set_live(None)
            started: _Live = await self._start()
            self._set_live(started)
            return started

    async def _answers(self, live: _Live) -> bool:
        """Whether a session can be used: a server Dot started holds its session while it runs, and one reached at a URL
        that was quiet for :data:`_QUIET_CHECK_S` is pinged first (item 513). One that does not answer is connected
        again, so that the calls that come wait for the new session rather than fail on the old one."""
        if self._config.command is not None or time.monotonic() - live.answered_at < _QUIET_CHECK_S:
            return True
        # A session of the modern protocol holds nothing that goes stale, and has no ping (the library warns that it is
        # removed, and a server may refuse it, which read as a session gone): a call that fails reconnects instead (the
        # review of every module).
        if live.client.protocol_version in MODERN_PROTOCOL_VERSIONS:
            return True
        try:
            async with asyncio.timeout(_PING_TIMEOUT_S):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", MCPDeprecationWarning)
                    await live.client.send_ping()
        except TimeoutError:
            log.warning("mcp_server_quiet_session_gone", server=self.name, error="the ping was not answered in time")
            live.broken = True
            return False
        except MCPError as exc:
            # A server that does not know ping answered it, so the session is there; any other error (a session its
            # server ended, a connection that closed, a gateway's failure) is connected again.
            if exc.code == METHOD_NOT_FOUND:
                live.answered_at = time.monotonic()
                return True
            log.warning("mcp_server_quiet_session_gone", server=self.name, error=truncate_middle(repr(exc), 200))
            live.broken = True
            return False
        live.answered_at = time.monotonic()
        return True

    async def _lost(self, live: _Live, step: str, error: MCPError) -> _ConnectionLost:
        """Remember that the connection broke so the next call starts the server again. The error keeps the words of
        the server or the client library ("Session not found", a redirect that was not followed), which say why."""
        live.broken = True
        log.warning("mcp_server_connection_lost", server=self.name, step=step, error=error.message)
        return _ConnectionLost(
            f"the MCP server closed the connection while {step}; the next call {self._again()}",
            {**self._context(), "code": error.code, "error": error.message, **await self._tail_since(0)},
        )

    def _set_live(self, live: _Live | None) -> None:
        """Remember the session and the instructions it sent. Called with ``_lock`` held."""
        self._live = live
        self._instructions = None if live is None else live.client.instructions

    def instructions(self) -> str | None:
        """What the server said of itself when it connected: the ``instructions`` of its initialize result, ``None``
        when it sent none or is not connected. Third-party words (item 659). The string was copied under ``_lock``
        (:meth:`_set_live`), so a reconnect cannot hand back a session that is already gone."""
        return self._instructions

    async def _request[R](self, live: _Live, method: str, timeout_s: float, request: Awaitable[R]) -> R:
        """Run an MCP catalogue request on the existing transport, with the same session bookkeeping as tool calls."""
        try:
            async with asyncio.timeout(timeout_s):
                result: R = await request
        except TimeoutError as exc:
            raise McpError(
                "the MCP request timed out",
                {**self._context(), "method": method, "timeout_s": timeout_s},
            ) from exc
        except MCPError as exc:
            if _session_gone(exc):
                raise await self._lost(live, method, exc) from exc
            raise McpError(
                "the MCP server rejected the request",
                {**self._context(), "method": method, "code": exc.code, "error": exc.message},
            ) from exc
        except (ValidationError, OAuthFlowError, httpx2.HTTPError, StreamableHTTPError) as exc:
            raise McpError(
                "the MCP request could not be completed",
                {**self._context(), "method": method, "error": truncate_middle(str(exc), _SHOWN_CAUSE_CHARS)},
            ) from exc
        live.answered_at = time.monotonic()
        return result

    async def _list[P: _Page, T](
        self,
        live: _Live,
        method: str,
        request: _ListRequest[P],
        items: Callable[[P], Sequence[T]],
    ) -> tuple[T, ...]:
        listed: list[T] = []
        cursor: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            page: P = await self._request(live, method, self._config.startup_timeout_s, request(cursor=cursor))
            listed.extend(items(page))
            if page.next_cursor is None:
                return tuple(listed)
            cursor = page.next_cursor
        raise McpError(
            "the MCP server lists more pages than supported",
            {**self._context(), "method": method, "pages": _MAX_LIST_PAGES},
        )

    async def list_tools(self) -> tuple[McpTool, ...]:
        """Every tool the server offers, following its pagination."""
        live: _Live = await self._ensure_live()
        if live.client.server_capabilities.tools is None:
            return ()
        tools: tuple[Tool, ...] = await self._list(live, "tools/list", live.client.list_tools, _page_tools)
        return tuple(_to_tool(self._config, tool) for tool in tools)

    async def discover_catalogue(self) -> None:
        """Snapshot the listings of the resource and prompt capabilities the server advertised."""
        live: _Live = await self._ensure_live()
        resources: tuple[Resource, ...] = ()
        prompts: tuple[Prompt, ...] = ()
        if live.client.server_capabilities.resources is not None:
            resources = await self._list(live, "resources/list", live.client.list_resources, _page_resources)
        if live.client.server_capabilities.prompts is not None:
            prompts = await self._list(live, "prompts/list", live.client.list_prompts, _page_prompts)
        self.catalogue = McpCatalogue(
            resources=tuple(
                McpResource(name=resource.title or resource.name, uri=resource.uri) for resource in resources
            ),
            prompts=tuple(
                McpPrompt(
                    name=prompt.name,
                    description=prompt.description,
                    arguments=tuple(
                        McpPromptArgument(
                            name=argument.name, description=argument.description, required=argument.required is True
                        )
                        for argument in prompt.arguments or ()
                    ),
                )
                for prompt in prompts
            ),
        )

    async def read_resource(self, uri: str) -> ReadResourceResult:
        live: _Live = await self._ensure_live()
        if live.client.server_capabilities.resources is None:
            raise McpError("the MCP server no longer offers resources", {"server": self.name})
        return await self._request(live, "resources/read", self._config.call_timeout_s, live.client.read_resource(uri))

    async def get_prompt(self, name: str, arguments: dict[str, str]) -> GetPromptResult:
        live: _Live = await self._ensure_live()
        if live.client.server_capabilities.prompts is None:
            raise McpError("the MCP server no longer offers prompts", {"server": self.name})
        return await self._request(
            live, "prompts/get", self._config.call_timeout_s, live.client.get_prompt(name, arguments)
        )

    async def call_tool(self, name: str, arguments: JsonObject, read_only: bool) -> CallToolResult:
        """Run a tool. A call that failed is not repeated, since the tool may already have done part of its work, but
        for one the server declares ``read_only`` whose connection broke while it was out (item 513): it reads and
        changes nothing, so it is called once more, on a new connection, with a warning."""
        try:
            return await self._call_once(name, arguments)
        except _ConnectionLost as lost:
            if not read_only:
                raise
            log.warning("mcp_call_repeated", server=self.name, tool=name, error=lost.message)
            return await self._call_once(name, arguments)

    async def _call_once(self, name: str, arguments: JsonObject) -> CallToolResult:
        live: _Live = await self._ensure_live()
        try:
            result: CallToolResult = await live.client.call_tool(
                name, arguments, read_timeout_seconds=self._config.call_timeout_s
            )
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                raise McpError(
                    "the MCP tool call timed out",
                    {**self._context(), "tool": name, "timeout_s": self._config.call_timeout_s},
                ) from exc
            if _session_gone(exc):
                raise await self._lost(live, f"running {name}", exc) from exc
            shown: str = truncate_middle(redact_secrets(dumps_compact(arguments)), _SHOWN_ARGUMENT_CHARS)
            raise McpRejected(
                "the MCP server rejected the call",
                {**self._context(), "tool": name, "code": exc.code, "error": exc.message, "arguments": shown},
            ) from exc
        live.answered_at = time.monotonic()
        return result

    async def close(self) -> None:
        async with self._lock:
            live: _Live | None = self._live
            self._set_live(None)
        if live is not None:
            await _release(live, self.name)
