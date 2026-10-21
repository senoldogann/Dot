"""The composer's palette (item 64): the skills by name, the MCP tools under their servers by the model's name."""

from pathlib import Path

from dotagent.mcp.types import McpTool, ToolHints
from dotagent.server.palette import palette_view
from dotagent.server.views import PaletteView
from dotagent.skills.types import Skill


def _skill(name: str, description: str) -> Skill:
    return Skill(
        name=name,
        description=description,
        root=Path("/tmp/skills") / name,
        body="# Steps",
        source="home",
        icon="sparkle",
    )


def _tool(server: str, name: str, description: str) -> McpTool:
    return McpTool(
        server=server,
        remote_name=name,
        exposed_name=f"{server}_{name}",
        description=description,
        input_schema={},
        hints=ToolHints(read_only=None, destructive=None, open_world=None),
        startup_timeout_s=10.0,
        call_timeout_s=60.0,
    )


def test_the_palette_lists_skills_by_name_and_tools_under_their_servers_with_one_line_each() -> None:
    view: PaletteView = palette_view(
        [_skill("release-notes", "Write the release notes."), _skill("deploy-site", "Deploy the site.")],
        [
            _tool("notes", "search", "Search the notes.\nSlow on large vaults."),
            _tool("calendar", "today", "The events of today."),
            _tool("notes", "create", "Create a note."),
        ],
        {},
    )
    assert [skill.name for skill in view.skills] == ["deploy-site", "release-notes"]
    assert view.skills[0].source == "home"
    assert [server.name for server in view.servers] == ["calendar", "notes"]
    assert [(tool.name, tool.description) for tool in view.servers[1].tools] == [
        ("notes_create", "Create a note."),
        ("notes_search", "Search the notes."),
    ]
    assert palette_view([], [], {}) == PaletteView(skills=(), servers=())


def test_a_server_of_the_palette_wears_the_logo_its_customize_tile_wears() -> None:
    """Item 446: the @ menu of the composer draws each server as the Customize page does, its own logo — and none for a
    server added by hand, which the menu draws with the plug."""
    view: PaletteView = palette_view(
        [],
        [_tool("notes", "search", "Search the notes."), _tool("context7", "lookup", "Look a library up.")],
        {"context7": "context7.svg"},
    )
    assert [(server.name, server.logo) for server in view.servers] == [
        ("context7", "context7.svg"),
        ("notes", None),
    ]
