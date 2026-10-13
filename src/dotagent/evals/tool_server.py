"""The MCP server of the evals (item 167): a stdio server whose tool makes a task of the catalog short, so that an eval
can tell whether a run takes the easy way. A task that brings it starts it as ``python -m dotagent.evals.tool_server``
under the name :data:`SERVER_NAME`; importing this module starts nothing."""

from datetime import date, timedelta

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

SERVER_NAME: str = "evals"
WORKING_DAYS: str = "working_days"
# The holidays of the company of the working-days task; its holidays.txt lists the same days.
HOLIDAYS: tuple[str, ...] = (
    "2026-01-01",
    "2026-01-06",
    "2026-03-20",
    "2026-04-03",
    "2026-04-06",
    "2026-05-01",
    "2026-05-14",
    "2026-06-19",
    "2026-12-24",
    "2026-12-25",
)


def working_days(first: str, last: str) -> int:
    """Count the working days of the company from first to last, both included (dates as YYYY-MM-DD): Monday to
    Friday, not one of the company's holidays. A last day before the first is an error."""
    start: date = date.fromisoformat(first)
    end: date = date.fromisoformat(last)
    if end < start:
        raise ValueError(f"the last day {last} is before the first day {first}")
    off: frozenset[date] = frozenset(date.fromisoformat(day) for day in HOLIDAYS)
    days: list[date] = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    return sum(1 for day in days if day.weekday() < 5 and day not in off)


def build_server() -> MCPServer:
    """The server, offering :func:`working_days` as a tool that only reads."""
    server: MCPServer = MCPServer("dot-evals")
    server.add_tool(
        working_days, name=WORKING_DAYS, annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False)
    )
    return server


if __name__ == "__main__":
    build_server().run("stdio")
