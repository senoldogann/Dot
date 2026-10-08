"""``dotx mcp``: start the configured MCP servers once and show the tools each offers, or why one does not start."""

import sys
from typing import Literal

from pydantic import JsonValue

from dotagent.config import AppConfig, load_user_config
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel
from dotagent.mcp.hub import ServerReport, inspect_servers
from dotagent.mcp.types import McpTool
from dotagent.policy.types import ActionInfo
from dotagent.tools.mcp import classify_mcp_call
from dotagent.tools.shell import read_login_env

_DESCRIPTION_CHARS: int = 70
_VALUE_CHARS: int = 400
_HIDDEN_DETAIL: frozenset[str] = frozenset({"server", "stderr_tail"})


class McpListCommand(FrozenModel):
    name: Literal["mcp-list"]


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _care(tool: McpTool) -> str:
    """How the policy treats a call of this tool: read-only tools run freely, the others are judged by their flags."""
    action: ActionInfo = classify_mcp_call(tool, {})
    if action.kind == "mcp_read":
        return "read-only"
    return f"acts: {', '.join(action.flags)}" if action.flags else "acts"


def render_tool(tool: McpTool) -> str:
    return f"  {tool.exposed_name}  [{_care(tool)}]  {_clip(tool.description, _DESCRIPTION_CHARS)}"


def _shown(value: JsonValue) -> str:
    return _clip(value if isinstance(value, str) else dumps_compact(value), _VALUE_CHARS)


def _failure_lines(report: ServerReport) -> list[str]:
    lines: list[str] = [f"  {report.error}"]
    lines.extend(f"    {key}: {_shown(value)}" for key, value in report.detail.items() if key not in _HIDDEN_DETAIL)
    tail: JsonValue = report.detail.get("stderr_tail")
    if isinstance(tail, str) and tail:
        lines.append("    what the server printed:")
        lines.extend(f"      {line}" for line in tail.splitlines())
    return lines


def render_report(report: ServerReport) -> str:
    """One server as text: its tools, or the reason it failed with everything that helps to fix it."""
    if not report.enabled:
        return f"{report.server}: disabled (enabled = false)"
    if report.error is not None:
        return "\n".join([f"{report.server}: FAILED", *_failure_lines(report)])
    header: str = f"{report.server}: ready, {len(report.tools)} of {report.listed} tools offered to the model"
    return "\n".join([header, *(render_tool(tool) for tool in report.tools), f"  server log: {report.log}"])


async def cmd_mcp_list(command: McpListCommand) -> int:
    config: AppConfig = load_user_config()
    if not config.mcp:
        sys.stdout.write(f"No MCP servers are configured. Add [[mcp]] tables to {config_path()}.\n")
        return 0
    starting: int = sum(1 for server in config.mcp if server.enabled)
    sys.stderr.write(f"Starting {starting} MCP server(s); the first start of a server can take a while...\n")
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s)
    reports: tuple[ServerReport, ...] = await inspect_servers(config.mcp, login_env)
    for report in reports:
        sys.stdout.write(f"{render_report(report)}\n")
    return 1 if any(report.error is not None for report in reports) else 0
