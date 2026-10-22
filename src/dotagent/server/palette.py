"""The palette of the composer's menus (item 64): the skills a run can use and the MCP servers with their tools, as
the web UI lists them when the owner types ``/``, and the accounts signed in, which the at menu offers beside the
servers (item 496). The commands of the menu are the web UI's own: they act on the composer, not on the daemon."""

from collections.abc import Mapping, Sequence

from dotagent.connections.directory import ServiceMark
from dotagent.mcp.types import McpTool
from dotagent.oauth.tokens import Account
from dotagent.server.callback_page import PROVIDER_MARKS
from dotagent.server.views import PaletteAccount, PaletteServer, PaletteSkill, PaletteTool, PaletteView
from dotagent.skills.types import Skill
from dotagent.tools.mail_calendar import account_reads


def palette_view(
    skills: Sequence[Skill],
    imported: frozenset[str],
    tools: Sequence[McpTool],
    marks: Mapping[str, ServiceMark | None],
    accounts: Sequence[Account],
) -> PaletteView:
    """The skills by name, each saying whether it is Dot's copy of another agent's (its name in ``imported``), the
    servers by name, each with its tools by the name the model calls them by and the mark its Customize tile wears
    (items 408 and 477), when ``marks`` knows one, and the accounts signed in, in the order they were, each with its
    provider's mark (item 496)."""
    servers: dict[str, list[PaletteTool]] = {}
    for tool in sorted(tools, key=lambda tool: (tool.server, tool.exposed_name)):
        servers.setdefault(tool.server, []).append(
            PaletteTool(name=tool.exposed_name, description=tool.description.strip().split("\n", 1)[0])
        )
    return PaletteView(
        skills=tuple(
            PaletteSkill(
                name=skill.name,
                description=skill.description,
                source=skill.source,
                icon=skill.icon,
                imported=skill.name in imported,
            )
            for skill in sorted(skills, key=lambda skill: skill.name)
        ),
        servers=tuple(
            PaletteServer(name=name, tools=tuple(listed), mark=marks.get(name)) for name, listed in servers.items()
        ),
        accounts=tuple(
            PaletteAccount(
                address=account.address,
                provider=account.provider,
                reads=account_reads(account.provider),
                mark=ServiceMark(logo=PROVIDER_MARKS[account.provider], glyph=None, colour=None),
            )
            for account in accounts
        ),
    )
