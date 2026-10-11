"""The palette of the composer's slash menu (item 64): the skills a run can use and the MCP servers with their tools,
as the web UI lists them when the owner types ``/``. The commands of the menu are the web UI's own: they act on the
composer, not on the daemon."""

from collections.abc import Sequence

from dotagent.mcp.types import McpTool
from dotagent.server.views import PaletteServer, PaletteSkill, PaletteTool, PaletteView
from dotagent.skills.types import Skill


def palette_view(skills: Sequence[Skill], tools: Sequence[McpTool]) -> PaletteView:
    """The skills by name and the servers by name, each with its tools by the name the model calls them by."""
    servers: dict[str, list[PaletteTool]] = {}
    for tool in sorted(tools, key=lambda tool: (tool.server, tool.exposed_name)):
        servers.setdefault(tool.server, []).append(
            PaletteTool(name=tool.exposed_name, description=tool.description.strip().split("\n", 1)[0])
        )
    return PaletteView(
        skills=tuple(
            PaletteSkill(name=skill.name, description=skill.description, source=skill.source)
            for skill in sorted(skills, key=lambda skill: skill.name)
        ),
        servers=tuple(PaletteServer(name=name, tools=tuple(listed)) for name, listed in servers.items()),
    )
