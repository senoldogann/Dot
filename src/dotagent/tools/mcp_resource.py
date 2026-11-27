"""Read resources advertised by connected MCP servers."""

import secrets
from typing import Literal

from mcp.types import CallToolResult, EmbeddedResource, ReadResourceResult
from pydantic import Field

from dotagent.core.errors import McpError, ToolInputError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import truncate_tail
from dotagent.core.types import JsonObject
from dotagent.mcp.hub import McpHub
from dotagent.mcp.types import RESOURCE_TOOL, McpResource
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.mcp_result import FENCE_OVERHEAD_CHARS, SERVER_WORD_FIELDS, fence_mcp, render_call_result

_DESCRIPTION: str = "List MCP resources. Omit server to list servers. Read a listed URI."
_PAGE_SIZE: int = 10


class ResourceArgs(ToolArgs):
    operation: Literal["list", "read"]
    server: str = ""
    uri: str = ""
    offset: int = Field(default=0, ge=0)


def _classify(args: ResourceArgs, ctx: ToolContext) -> ActionInfo:
    resources: tuple[McpResource, ...] = ctx.runtime.mcp.catalogue(args.server).resources
    if args.operation == "read":
        if not args.server or not args.uri:
            raise ToolInputError("reading an MCP resource requires its server and URI", {})
        if not any(resource.uri == args.uri for resource in resources):
            raise ToolInputError("the MCP server has not listed this resource", {"server": args.server})
    elif args.server and not resources:
        raise ToolInputError("the MCP server has no listed resources", {"server": args.server})
    return ActionInfo(
        tool=RESOURCE_TOOL,
        kind="mcp_read",
        risk="low",
        summary=f"{args.operation} MCP resources",
        target=args.server,
        flags=(),
    )


def _list(hub: McpHub, args: ResourceArgs) -> tuple[tuple[str, ...], int | None]:
    if not args.server:
        return tuple(f"{server} ({len(catalogue.resources)})" for server, catalogue in hub.resource_catalogues()), None
    resources: tuple[McpResource, ...] = hub.catalogue(args.server).resources
    end: int = args.offset + _PAGE_SIZE
    listed: tuple[str, ...] = tuple(f"{item.name[:200]}: {item.uri}" for item in resources[args.offset : end])
    return listed, end if end < len(resources) else None


def build_resource_tool(hub: McpHub) -> tuple[RegisteredTool, ...]:
    catalogues = hub.resource_catalogues()
    if not catalogues:
        return ()

    async def run(args: ResourceArgs, ctx: ToolContext) -> ToolResult:
        nonce: str = secrets.token_hex(8)
        budget: int = max(0, ctx.output_chars - FENCE_OVERHEAD_CHARS)
        if args.operation == "list":
            entries, next_offset = _list(hub, args)
            text: str = truncate_tail("\n".join(entries), budget)
            fenced: str = fence_mcp(args.server, RESOURCE_TOOL, text, nonce)
            continuation: str = "" if next_offset is None else f"\nNext offset: {next_offset}"
            return mark_untrusted(ok(fenced + continuation))
        try:
            answered: ReadResourceResult = await hub.read_resource(args.server, args.uri)
        except McpError as error:
            outside: JsonObject = {key: value for key, value in error.context.items() if key in SERVER_WORD_FIELDS}
            if not outside:
                raise
            detail: str = truncate_tail(dumps_compact(outside), budget)
            return mark_untrusted(fail(f"{error.message}\n{fence_mcp(args.server, RESOURCE_TOOL, detail, nonce)}"))
        return await render_call_result(
            args.server,
            RESOURCE_TOOL,
            CallToolResult(content=[EmbeddedResource(type="resource", resource=item) for item in answered.contents]),
            budget,
            ctx.runtime.config.desktop.screenshot_max_edge,
            ctx.runtime.config.desktop.jpeg_quality,
            nonce,
            ctx.workspace,
        )

    return (
        make_tool(
            RESOURCE_TOOL, _DESCRIPTION, ResourceArgs, ALL_MODES, True, hub.resource_timeout() + 15.0, _classify, run
        ),
    )
