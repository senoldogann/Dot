"""The composer's palette (item 64): the skills by name, the MCP tools under their servers by the model's name, each
server with the mark its Customize tile wears and each skill saying whether it is an imported copy (item 477)."""

from pathlib import Path

from dotagent.connections.directory import ServiceMark, mark_of_service
from dotagent.help.elsewhere import ADDED_MARKER, SkillCopy, copied_skill_names
from dotagent.mcp.types import McpTool, ToolHints
from dotagent.oauth.tokens import Account
from dotagent.server.palette import palette_view
from dotagent.server.views import PaletteView
from dotagent.skills.types import Skill


def _skill(name: str, description: str) -> Skill:
    return _skill_at(Path("/tmp/skills") / name, name, description, "home")


def _skill_at(root: Path, name: str, description: str, source: str) -> Skill:
    return Skill(name=name, description=description, root=root, body="# Steps", source=source, icon="sparkle")


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
        frozenset(),
        [
            _tool("notes", "search", "Search the notes.\nSlow on large vaults."),
            _tool("calendar", "today", "The events of today."),
            _tool("notes", "create", "Create a note."),
        ],
        {},
        [],
    )
    assert [skill.name for skill in view.skills] == ["deploy-site", "release-notes"]
    assert view.skills[0].source == "home"
    assert [server.name for server in view.servers] == ["calendar", "notes"]
    assert [(tool.name, tool.description) for tool in view.servers[1].tools] == [
        ("notes_create", "Create a note."),
        ("notes_search", "Search the notes."),
    ]
    assert palette_view([], frozenset(), [], {}, []) == PaletteView(skills=(), servers=(), accounts=())


def test_a_server_of_the_palette_wears_the_mark_its_customize_tile_wears() -> None:
    """Items 447 and 477: the composer's menus draw each server as the Customize page draws its service's tile — its
    logo, else its icon, else its letters — and a server of no service with the plug (no mark)."""
    marks: dict[str, ServiceMark | None] = {
        name: mark_of_service(name) for name in ("deepwiki", "context7", "microsoft-learn")
    } | {"notes": None}
    view: PaletteView = palette_view(
        [],
        frozenset(),
        [_tool(name, "lookup", "Look something up.") for name in ("notes", "deepwiki", "context7", "microsoft-learn")],
        marks,
        [],
    )
    assert [(server.name, server.mark) for server in view.servers] == [
        ("context7", ServiceMark(logo=None, glyph=None, monogram="C7", colour=None)),
        ("deepwiki", ServiceMark(logo=None, glyph="search", monogram=None, colour=None)),
        ("microsoft-learn", ServiceMark(logo="microsoft-icon.svg", glyph=None, monogram=None, colour=None)),
        ("notes", None),
    ]


def test_a_skill_dot_copied_from_another_agent_is_marked_imported_and_no_other(tmp_path: Path) -> None:
    """Item 477: the slash menu marks an imported skill as its Customize card does, so the palette says which skills
    are Dot's copies: those of its own folder whose marker says so — not one of the folder without it, nor one found
    elsewhere with a marker in its folder."""
    copied, own, elsewhere = tmp_path / "copied", tmp_path / "own", tmp_path / "elsewhere"
    for folder in (copied, own, elsewhere):
        folder.mkdir()
    marker: str = SkillCopy(agent="Claude Code", name="copied", folder=tmp_path / "theirs").model_dump_json()
    (copied / ADDED_MARKER).write_text(marker, encoding="utf-8")
    (elsewhere / ADDED_MARKER).write_text(marker, encoding="utf-8")
    skills: list[Skill] = [
        _skill_at(copied, "copied", "A copy.", "home"),
        _skill_at(own, "own", "The owner's.", "home"),
        _skill_at(elsewhere, "elsewhere", "From a folder of the config.", "config"),
    ]
    imported: frozenset[str] = copied_skill_names(skills)
    assert imported == frozenset({"copied"})
    view: PaletteView = palette_view(skills, imported, [], {}, [])
    assert [(skill.name, skill.imported) for skill in view.skills] == [
        ("copied", True),
        ("elsewhere", False),
        ("own", False),
    ]


def test_the_palette_names_the_accounts_signed_in_with_their_providers_marks() -> None:
    """Item 496: the at menu offers each account signed in, with what of it a run reads and its provider's logo."""
    accounts = [
        Account(provider="google", address="me@gmail.com", connected_at=1.0),
        Account(provider="microsoft", address="me@outlook.com", connected_at=2.0),
    ]
    view: PaletteView = palette_view([], frozenset(), [], {}, accounts)
    assert [(account.address, account.mark.logo) for account in view.accounts] == [
        ("me@gmail.com", "google-icon.svg"),
        ("me@outlook.com", "microsoft-icon.svg"),
    ]
    assert view.accounts[0].reads.startswith("Gmail")
