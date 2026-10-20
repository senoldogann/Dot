"""Tools of MCP servers: each tool a connected server offers becomes a tool named ``mcp__<server>__<tool>``."""

import re
import secrets

from dotagent.config import AppConfig
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import redact_secrets
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolSpec
from dotagent.mcp.hub import McpHub, tool_parameters
from dotagent.mcp.naming import PREFIX
from dotagent.mcp.types import McpTool
from dotagent.policy.types import ActionInfo, Flag
from dotagent.tools.base import ACTING_MODES, ALL_MODES, PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.mcp_result import FENCE_OVERHEAD_CHARS, render_call_result
from dotagent.tools.registry import ToolRegistry

_NONCE_BYTES: int = 8
_DESCRIPTION_CHARS: int = 1200
_ARGUMENT_CHARS: int = 300
_MIN_TEXT_CHARS: int = 1000
_TIMEOUT_MARGIN_S: float = 15.0


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


# What the name of a tool says it does with a message (item 427): the first verb of the name decides, after any word
# before it such as the name of the service (slack_post_message, gmail_send_email).
_SEND_VERBS: frozenset[str] = frozenset(
    {"send", "reply", "forward", "post", "publish", "tweet", "retweet", "comment", "dm"}
)
_MAKE_VERBS: frozenset[str] = frozenset({"create", "add", "new", "write"})
_READ_VERBS: frozenset[str] = frozenset(
    {"get", "list", "search", "read", "fetch", "find", "query", "describe", "view", "show", "count", "download"}
)
_MESSAGE_NOUNS: frozenset[str] = frozenset(
    {"message", "post", "tweet", "comment", "reply", "email", "mail", "dm", "chat"}
)
_NAME_WORD: re.Pattern[str] = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")


def sends_messages(name: str) -> bool:
    """Whether the name of an MCP tool says it sends a message or posts for the owner: its first verb sends, or makes
    a message, a post or a comment. A name with no verb this knows says nothing."""
    words: list[str] = [word.lower() for word in _NAME_WORD.findall(name)]
    for at, word in enumerate(words):
        if word in _SEND_VERBS:
            return True
        if word in _MAKE_VERBS:
            return any(noun in _MESSAGE_NOUNS for noun in words[at + 1 :])
        if word in _READ_VERBS:
            return False
    return False


def classify_mcp_call(tool: McpTool, arguments: JsonObject) -> ActionInfo:
    """A tool whose name says it sends a message or posts is a message for the owner whatever its server's hints say
    (item 427): a hint is the server's word, and a server may be wrong or hostile. Other tools that declare themselves
    read-only only look. Any other tool may change things and may reach out to the world, unless the server says it
    does not: servers that declare nothing get the careful treatment."""
    shown: str = _clip(redact_secrets(dumps_compact(arguments)), _ARGUMENT_CHARS)
    summary: str = f"{tool.server}: {tool.remote_name}({shown})"
    sends: bool = sends_messages(tool.remote_name)
    if tool.hints.read_only is True and not sends:
        return ActionInfo(
            tool=tool.exposed_name, kind="mcp_read", risk="low", summary=summary, target=tool.server, flags=()
        )
    flags: list[Flag] = []
    if tool.hints.destructive is not False and tool.hints.read_only is not True:
        flags.append("destructive")
    if tool.hints.open_world is not False or sends:
        flags.append("external_send")
    if sends:
        flags.append("message_send")
    return ActionInfo(
        tool=tool.exposed_name, kind="mcp", risk="medium", summary=summary, target=tool.server, flags=tuple(flags)
    )


def _text_budget(output_chars: int) -> int:
    return max(output_chars - FENCE_OVERHEAD_CHARS, _MIN_TEXT_CHARS)


def _register(hub: McpHub, tool: McpTool) -> RegisteredTool:
    read_only: bool = tool.hints.read_only is True
    parameters: JsonObject = tool_parameters(tool)

    async def prepare(raw: JsonObject, ctx: ToolContext) -> PreparedCall:
        action: ActionInfo = classify_mcp_call(tool, raw)

        async def run(context: ToolContext) -> ToolResult:
            config: AppConfig = context.runtime.config
            return await render_call_result(
                tool.server,
                tool.remote_name,
                await hub.call(tool, raw),
                _text_budget(context.output_chars),
                config.desktop.screenshot_max_edge,
                config.desktop.jpeg_quality,
                secrets.token_hex(_NONCE_BYTES),
            )

        return PreparedCall(action=action, run=run)

    return RegisteredTool(
        spec=ToolSpec(
            name=tool.exposed_name,
            description=f"[MCP server {tool.server}] {_clip(tool.description, _DESCRIPTION_CHARS)}",
            parameters=parameters,
        ),
        modes=ALL_MODES if read_only else ACTING_MODES,
        parallel_safe=read_only,
        timeout_s=tool.startup_timeout_s + tool.call_timeout_s + _TIMEOUT_MARGIN_S,
        prepare=prepare,
    )


def build_mcp_tools(hub: McpHub) -> tuple[RegisteredTool, ...]:
    return tuple(_register(hub, tool) for tool in hub.tools)


def refresh_mcp_tools(registry: ToolRegistry, hub: McpHub) -> None:
    """Make the registry's MCP tools those of ``hub`` now, after a server joined or left it (item 279): the runs that
    start after are offered them, and a run keeps the tools it started with."""
    for name in [name for name in registry.tools if name.startswith(PREFIX)]:
        del registry.tools[name]
    registry.tools.update({tool.exposed_name: _register(hub, tool) for tool in hub.tools})
