"""Dot's own tools for OpenCode's agent (item 284): an MCP server of Dot's on a loopback port, which the OpenCode server
Dot starts (:mod:`dotagent.opencode.server`) reaches as a remote MCP server of its own.

Each chat with a run at work in OpenCode has a door of its own (:class:`Door`): a name OpenCode knows the chat's tools
by (``dot_`` and eight hex digits of the chat, so a tool reads ``dot_1a2b3c4d_browser_open``), a secret its requests
carry, the tools of Dot's the run may use, and how a call of one runs, through the run's gate as a call of Dot's own
loop does (:mod:`dotagent.opencode.run`). OpenCode hands every session the tools of every MCP server it has, so the
session of a chat keeps to its own door's by its permission rules (:func:`session_rules`): the doors of other chats are
hidden from its model, and their instructions with them (measured with OpenCode 1.19.1 and a Zen free model, 2026-10-16:
the model listed only its own door's tool and called it, and Zen's free tier answered as without the server).

A call waits as long as its tool does, the owner's answer to an approval included: OpenCode waits ``CALL_TIMEOUT_MS``
for an answer, and each call sends it progress every ``_PING_S`` meanwhile, which starts that wait again.
"""

import asyncio
import hashlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import uvicorn
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ContentBlock,
    ImageContent,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import JsonValue
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from dotagent.core.errors import OpenCodeError
from dotagent.core.jsonutil import json_object
from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolCall, ToolSpec
from dotagent.tools.base import ToolResult

log = get_logger(__name__)

# The start of every door's name: a session's rules hide the doors of the other chats by it.
DOOR_PREFIX: str = "dot_"
_DOOR_DIGITS: int = 8
_HOST: str = "127.0.0.1"
_PATH: str = "/mcp"
_BEARER: str = "Bearer "
# How often a call that is still at work tells OpenCode so, and how long OpenCode waits between two such words.
_PING_S: float = 20.0
CALL_TIMEOUT_MS: int = 120_000
_START_TIMEOUT_S: float = 10.0
_STOP_TIMEOUT_S: float = 5.0

# What OpenCode's agent reads about a door's tools, in its system prompt, while the door's tools are its own.
INSTRUCTIONS: str = (
    "You are working inside Dot, the owner's assistant app on their Mac, and this server's tools are Dot's own, given "
    "to you for this run besides your own: Dot's browser (browser_*: open pages, read them, click, type and fill "
    "forms in Dot's Chrome, where the sites the owner signed in to there stay signed in), the Mac's applications "
    "(desktop_* and apple_script) when the owner has allowed them, Dot's memory of earlier conversations (memory_*), "
    "schedules (schedule_*), a notification to the owner (notify), a page shown in the conversation (show_page), "
    "Dot's skills (skill), a question to the owner (ask_user) and the apps the owner connected to Dot. When a task "
    "needs a web page, a site the owner uses or an app on the Mac, use these tools rather than saying that you have "
    "no browser or no access to the Mac. Each call goes through Dot's rules and, where they say so, the owner's "
    "approval; a refused call says why, and you go on from there. Other chats of Dot may be at work at the same time: "
    "their tabs and apps are theirs."
)

type CallRunner = Callable[[ToolCall], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Door:
    """One chat's way in while a run of it works: the name OpenCode knows its tools by, the secret its requests carry,
    the tools of Dot's it offers, and how a call of one runs (the run's own gate, as in Dot's loop)."""

    name: str
    secret: str
    specs: tuple[ToolSpec, ...]
    run: CallRunner


@dataclass(frozen=True)
class _Serving:
    web: uvicorn.Server
    task: asyncio.Task[None]
    url: str


def door_name(thread_id: str) -> str:
    """The name of the chat ``thread_id``'s door: the same for each run of the chat, so that the tools its session
    used in earlier runs keep their names."""
    return f"{DOOR_PREFIX}{hashlib.sha256(thread_id.encode()).hexdigest()[:_DOOR_DIGITS]}"


def new_door(thread_id: str, specs: tuple[ToolSpec, ...], run: CallRunner) -> Door:
    return Door(name=door_name(thread_id), secret=secrets.token_urlsafe(32), specs=specs, run=run)


def session_rules(name: str) -> list[JsonValue]:
    """The permission rules of a session whose door is ``name``: the tools of every door denied, so hidden from its
    model, then its own allowed. OpenCode reads the last rule that matches a tool, so the second wins for its own."""
    return [
        {"permission": f"{DOOR_PREFIX}*", "pattern": "*", "action": "deny"},
        {"permission": f"{name}_*", "pattern": "*", "action": "allow"},
    ]


def server_config(url: str, door: Door) -> JsonObject:
    """The remote MCP server OpenCode adds for ``door``: the bridge's address, the door's secret, no OAuth."""
    return {
        "type": "remote",
        "url": url,
        "headers": {"Authorization": f"{_BEARER}{door.secret}"},
        "oauth": False,
        "timeout": CALL_TIMEOUT_MS,
    }


def mcp_tool(spec: ToolSpec) -> Tool:
    return Tool(name=spec.name, description=spec.description, input_schema=dict(spec.parameters))


def mcp_result(result: ToolResult) -> CallToolResult:
    """A result of Dot's tool as an MCP result: its text, then its pictures."""
    content: list[ContentBlock] = [TextContent(text=result.text)]
    content.extend(ImageContent(data=image.data_b64, mime_type=image.media_type) for image in result.images)
    return CallToolResult(content=content, is_error=result.is_error)


def _refused(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(text=text)], is_error=True)


def _secret_of(headers: Mapping[str, str]) -> str | None:
    value: str = headers.get("authorization", "")
    return value.removeprefix(_BEARER) if value.startswith(_BEARER) else None


class ToolBridge:
    """Dot's MCP server for OpenCode's agent, started when the first door opens and stopped with the daemon; a
    connector between OpenCode and the runs that work in it."""

    def __init__(self) -> None:
        self._doors: dict[str, Door] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._serving: _Serving | None = None

    async def url(self) -> str:
        """The address OpenCode reaches the bridge at, the bridge started first when it is not up."""
        async with self._lock:
            if self._serving is None or self._serving.task.done():
                self._serving = await self._start()
            return self._serving.url

    @asynccontextmanager
    async def opened(self, door: Door) -> AsyncIterator[str]:
        """Let ``door``'s requests in while the block runs, with the bridge's address; closed after it, a call still
        under way of it then answered as refused."""
        address: str = await self.url()
        self._doors[door.secret] = door
        try:
            yield address
        finally:
            self._doors.pop(door.secret, None)

    def _door_of(self, request: object) -> Door | None:
        if not isinstance(request, Request):
            return None
        secret: str | None = _secret_of(request.headers)
        return None if secret is None else self._doors.get(secret)

    async def _list(self, ctx: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListToolsResult:
        door: Door | None = self._door_of(ctx.request)
        return ListToolsResult(tools=[] if door is None else [mcp_tool(spec) for spec in door.specs])

    async def _call(self, ctx: ServerRequestContext[object], params: CallToolRequestParams) -> CallToolResult:
        door: Door | None = self._door_of(ctx.request)
        if door is None:
            return _refused(
                "The run these tools belonged to has ended: Dot's tools are offered only while a run works."
            )
        if all(spec.name != params.name for spec in door.specs):
            names: str = ", ".join(spec.name for spec in door.specs)
            return _refused(f"Dot has no tool {params.name!r} for this run. Its tools here: {names}.")
        call: ToolCall = ToolCall(
            id=f"mcp-{secrets.token_hex(6)}",
            name=params.name,
            arguments=json_object(params.arguments or {}, f"arguments of {params.name}"),
        )
        token: str | int | None = None if ctx.meta is None else ctx.meta.get("progress_token")
        pinging: asyncio.Task[None] | None = (
            None if token is None else asyncio.create_task(_ping(ctx, token), name=f"bridge-ping-{call.id}")
        )
        try:
            return mcp_result(await door.run(call))
        finally:
            if pinging is not None:
                pinging.cancel()
                with suppress(asyncio.CancelledError):
                    await pinging

    async def _start(self) -> _Serving:
        server: Server[object] = Server(
            "dot", instructions=INSTRUCTIONS, on_list_tools=self._list, on_call_tool=self._call
        )
        manager: StreamableHTTPSessionManager = StreamableHTTPSessionManager(
            app=server,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=[f"{_HOST}:*"], allowed_origins=[]
            ),
        )

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                return
            headers: dict[str, str] = {
                key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]
            }
            secret: str | None = _secret_of(headers)
            if scope["path"] != _PATH or secret is None or secret not in self._doors:
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            await manager.handle_request(scope, receive, send)

        web: uvicorn.Server = uvicorn.Server(
            uvicorn.Config(app, host=_HOST, port=0, lifespan="off", log_level="warning", access_log=False)
        )

        async def serve() -> None:
            async with manager.run():
                await web.serve()

        task: asyncio.Task[None] = asyncio.create_task(serve(), name="opencode-bridge")
        try:
            async with asyncio.timeout(_START_TIMEOUT_S):
                while not web.started:
                    if task.done():
                        raise OpenCodeError("Dot's tool bridge for OpenCode stopped while starting", {})
                    await asyncio.sleep(0.02)
        except TimeoutError:
            task.cancel()
            raise OpenCodeError("Dot's tool bridge for OpenCode did not start in time", {}) from None
        port: int = web.servers[0].sockets[0].getsockname()[1]
        log.info("opencode_bridge_started", port=port)
        return _Serving(web=web, task=task, url=f"http://{_HOST}:{port}{_PATH}")

    async def aclose(self) -> None:
        async with self._lock:
            serving: _Serving | None = self._serving
            self._serving = None
        self._doors.clear()
        if serving is None:
            return
        serving.web.should_exit = True
        try:
            async with asyncio.timeout(_STOP_TIMEOUT_S):
                await serving.task
        except TimeoutError:
            serving.task.cancel()
            with suppress(asyncio.CancelledError):
                await serving.task


async def _ping(ctx: ServerRequestContext[object], token: str | int) -> None:
    """Tell OpenCode now and then, on the call's own stream, that the call is still at work, so that its wait for the
    answer starts again. A word to a stream that has closed is dropped by the MCP library, which raises nothing."""
    related: str | None = None if ctx.request_id is None else str(ctx.request_id)
    beats: int = 0
    while True:
        await asyncio.sleep(_PING_S)
        beats += 1
        await ctx.session.send_progress_notification(token, float(beats), None, "still at work", related)
