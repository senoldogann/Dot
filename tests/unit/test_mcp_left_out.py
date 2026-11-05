"""A tool whose schema cannot be flattened is logged, named by doctor, and not registered. A good tool still is."""

from structlog.testing import capture_logs

from dotagent.cli_doctor import check_line
from dotagent.config import McpServerConfig
from dotagent.doctor import left_out_checks
from dotagent.mcp.hub import McpHub, usable_tools
from dotagent.mcp.naming import exposed_name
from dotagent.mcp.types import LeftOutTool, McpTool, ToolHints
from dotagent.tools.mcp import build_mcp_tools


def _tool(name: str, schema: dict[str, object]) -> McpTool:
    return McpTool(
        server="fake",
        remote_name=name,
        exposed_name=exposed_name("fake", name),
        description=name,
        input_schema=schema,
        hints=ToolHints(read_only=True, destructive=False, open_world=False),
        startup_timeout_s=10.0,
        call_timeout_s=10.0,
    )


def test_doctor_names_a_tool_whose_schema_cannot_be_used_and_the_good_tool_is_registered() -> None:
    good: McpTool = _tool("list_notes", {"type": "object", "properties": {"q": {"type": "string"}}})
    odd: McpTool = _tool("odd_filter", {"type": "string"})
    config: McpServerConfig = McpServerConfig(name="fake", command="fake-mcp")
    omitted: list[LeftOutTool] = []
    with capture_logs() as logs:
        kept: tuple[McpTool, ...] = usable_tools(config, (good, odd), omitted)
    assert [tool.remote_name for tool in kept] == ["list_notes"]
    assert [(item.tool, "schema" in item.reason or "cannot be used" in item.reason) for item in omitted] == [
        ("odd_filter", True)
    ]
    assert any(entry.get("event") == "mcp_tool_left_out" and entry.get("tool") == "odd_filter" for entry in logs)
    line: str = check_line(left_out_checks(omitted)[0])
    assert "odd_filter" in line and "left out" in line
    hub: McpHub = McpHub({}, kept, (), {config.name: config}, {}, {})
    registered: set[str] = {tool.spec.name for tool in build_mcp_tools(hub)}
    assert exposed_name("fake", "list_notes") in registered
    assert exposed_name("fake", "odd_filter") not in registered
