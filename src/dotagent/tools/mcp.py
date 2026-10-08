"""Tools of MCP servers: each tool a connected server offers becomes a tool named ``mcp__<server>__<tool>``."""

import secrets

from dotagent.config import AppConfig
from dotagent.core.errors import McpError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import redact_secrets
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolSpec
from dotagent.mcp.hub import McpHub
from dotagent.mcp.types import McpTool
from dotagent.policy.types import ActionInfo, Flag
from dotagent.tools.base import ACTING_MODES, ALL_MODES, PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.mcp_result import FENCE_OVERHEAD_CHARS, render_call_result
from dotagent.tools.schema import flatten_json_schema

_NONCE_BYTES: int = 8
_DESCRIPTION_CHARS: int = 1200
_ARGUMENT_CHARS: int = 300
_MIN_TEXT_CHARS: int = 1000
_TIMEOUT_MARGIN_S: float = 15.0


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def classify_mcp_call(tool: McpTool, arguments: JsonObject) -> ActionInfo:
    """Tools that declare themselves read-only only look. Any other tool may change things and may reach out to the
    world, unless the server says it does not: servers that declare nothing get the careful treatment."""
    shown: str = _clip(redact_secrets(dumps_compact(arguments)), _ARGUMENT_CHARS)
    summary: str = f"{tool.server}: {tool.remote_name}({shown})"
    if tool.hints.read_only is True:
        return ActionInfo(
            tool=tool.exposed_name, kind="mcp_read", risk="low", summary=summary, target=tool.server, flags=()
        )
    flags: list[Flag] = []
    if tool.hints.destructive is not False:
        flags.append("destructive")
    if tool.hints.open_world is not False:
        flags.append("external_send")
    return ActionInfo(
        tool=tool.exposed_name, kind="mcp", risk="medium", summary=summary, target=tool.server, flags=tuple(flags)
    )


def _text_budget(config: AppConfig) -> int:
    return max(config.agent.tool_output_chars - FENCE_OVERHEAD_CHARS, _MIN_TEXT_CHARS)


def _register(hub: McpHub, tool: McpTool) -> RegisteredTool:
    read_only: bool = tool.hints.read_only is True
    try:
        parameters: JsonObject = flatten_json_schema(tool.input_schema)
    except ValueError as exc:
        raise McpError(
            "the input schema of an MCP tool cannot be used",
            {"server": tool.server, "tool": tool.remote_name, "error": str(exc), "hint": "leave it out with `allow`"},
        ) from exc

    async def prepare(raw: JsonObject, ctx: ToolContext) -> PreparedCall:
        action: ActionInfo = classify_mcp_call(tool, raw)

        async def run(context: ToolContext) -> ToolResult:
            config: AppConfig = context.runtime.config
            return await render_call_result(
                tool.server,
                tool.remote_name,
                await hub.call(tool, raw),
                _text_budget(config),
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
