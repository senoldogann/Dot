"""Help found elsewhere, added with the owner's yes (item 165), in a home and a Dot home made here: a skill is added as a
copy in Dot's skills folder, which Dot's catalog then reads and which keeps to what was copied, and removing it deletes
that copy and nothing else, even once its agent no longer has it; an MCP server is added as a line of Dot's record, by
its agent, its name and a fingerprint of its entry with no secret of it, and is read from its agent's file when Dot
starts, as long as its entry is the one the owner said yes to. What Dot cannot take is refused, with why."""

import json
import shutil
from pathlib import Path

import pytest

from dotagent.config import AppConfig, McpServerConfig, SkillsConfig, load_default_config
from dotagent.core.errors import ElsewhereError
from dotagent.core.paths import added_servers_path, skills_dir
from dotagent.help.added import (
    AddedServers,
    add_server,
    add_skill,
    added_server_configs,
    dot_has,
    read_added_servers,
    remove_server,
    remove_skill,
)
from dotagent.help.elsewhere import (
    SKILL_COPY_LIMIT,
    CopyLimit,
    FoundElsewhere,
    FoundServer,
    FoundSkill,
    GoneItem,
    found_elsewhere,
)
from dotagent.skills.catalog import load_skills
from dotagent.skills.types import SkillCatalog

SECRETS: tuple[str, ...] = tuple(f"secret-{index}-4b8e1d" for index in range(3))
_READ_ONLY: int = 0o555


def _skill(folder: Path, name: str) -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\nDo it.\n", encoding="utf-8"
    )


def _desktop(home: Path, servers: dict[str, object]) -> Path:
    path: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _found(home: Path, config: AppConfig) -> FoundElsewhere:
    catalog: SkillCatalog = load_skills(config.skills, (), None)
    added: AddedServers = read_added_servers(added_servers_path())
    return found_elsewhere(home, dot_has(config, catalog, (), added), SKILL_COPY_LIMIT)


def _skill_of(found: FoundElsewhere, name: str) -> FoundSkill:
    return next(skill for skill in found.skills if skill.name == name)


def _server_of(found: FoundElsewhere, name: str) -> FoundServer:
    return next(server for server in found.servers if server.name == name)


def _writable(tree: Path) -> None:
    for folder in [tree, *(path for path in tree.rglob("*") if path.is_dir())]:
        folder.chmod(0o755)


def test_a_skill_of_another_agent_may_take_the_name_of_one_that_comes_with_dot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home: Path = tmp_path / "user"
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    _skill(home / ".claude" / "skills" / "excel", "excel")
    config: AppConfig = load_default_config().model_copy(update={"skills": SkillsConfig(dirs=[])})
    # Added, it is used instead of Dot's own excel (item 196).
    assert _skill_of(_found(home, config), "excel").refusal is None


def test_a_skill_is_added_as_a_copy_that_dot_reads_and_removing_it_deletes_that_copy_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home: Path = tmp_path / "user"
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    claude: Path = home / ".claude" / "skills"
    pdf: Path = claude / "pdf-tools"
    _skill(pdf, "pdf-tools")
    (pdf / "scripts").mkdir()
    (pdf / "scripts" / "fill.sh").write_text("pdftk\n", encoding="utf-8")
    (pdf / ".git").mkdir()
    (pdf / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    # Folders the owner cannot write in, as some installers leave them: the copy keeps their modes.
    (pdf / "scripts").chmod(_READ_ONLY)
    pdf.chmod(_READ_ONLY)
    _skill(claude / "notes", "notes")
    _skill(claude / "linked", "linked")
    (claude / "linked" / "data").symlink_to(home)
    _skill(claude / "big", "big")
    # Dot has a skill of its own by the name of one of Claude Code's.
    _skill(skills_dir() / "notes", "notes")
    config: AppConfig = load_default_config().model_copy(update={"skills": SkillsConfig(dirs=[])})

    found: FoundElsewhere = _found(home, config)
    assert [(skill.name, skill.used, skill.added, skill.refusal) for skill in found.skills] == [
        ("big", False, False, None),
        ("linked", False, False, "it holds a link, which Dot does not copy"),
        ("notes", False, False, "Dot already has a skill called notes"),
        ("pdf-tools", False, False, None),
    ]
    add_skill(skills_dir(), _skill_of(found, "pdf-tools"), SKILL_COPY_LIMIT)
    copy: Path = skills_dir() / "pdf-tools"
    # A folder of its own, without the hidden entries of Claude Code's, and with Dot's marker of where it came from.
    assert not copy.is_symlink()
    assert sorted(path.relative_to(copy).as_posix() for path in copy.rglob("*")) == [
        ".added-elsewhere.json",
        "SKILL.md",
        "scripts",
        "scripts/fill.sh",
    ]
    assert "pdf-tools" in {skill.name for skill in load_skills(config.skills, (), None).skills}
    # The copy is what the owner said yes to: a later change of Claude Code's skill does not reach Dot.
    (pdf / "SKILL.md").write_text("---\nname: pdf-tools\ndescription: Changed.\n---\nOther.\n", encoding="utf-8")
    assert "The pdf-tools skill." in (copy / "SKILL.md").read_text(encoding="utf-8")
    found = _found(home, config)
    assert (_skill_of(found, "pdf-tools").used, _skill_of(found, "pdf-tools").added, found.gone) == (False, True, ())

    with pytest.raises(ElsewhereError, match="it holds a link, which Dot does not copy"):
        add_skill(skills_dir(), _skill_of(found, "linked"), SKILL_COPY_LIMIT)
    with pytest.raises(ElsewhereError, match="it is too big to copy"):
        add_skill(skills_dir(), _skill_of(found, "big"), CopyLimit(files=0, bytes=1024))
    # Dot's own skill of that name is neither taken over nor removed.
    with pytest.raises(ElsewhereError, match="Dot already has a skill called notes"):
        add_skill(skills_dir(), _skill_of(found, "notes"), SKILL_COPY_LIMIT)
    with pytest.raises(ElsewhereError, match="was not added to Dot from here"):
        remove_skill(skills_dir(), "Claude Code", "notes")
    # What was refused left nothing in Dot's skills folder, hidden or not.
    assert sorted(path.name for path in skills_dir().iterdir()) == ["notes", "pdf-tools"]
    remove_skill(skills_dir(), "Claude Code", "pdf-tools")
    assert sorted(path.name for path in skills_dir().iterdir()) == ["notes"]
    assert (pdf / "scripts" / "fill.sh").is_file()
    assert _skill_of(_found(home, config), "pdf-tools").added is False

    # Added again, then gone from Claude Code: Dot keeps its copy, tells it apart, and still removes it.
    add_skill(skills_dir(), _skill_of(_found(home, config), "pdf-tools"), SKILL_COPY_LIMIT)
    _writable(pdf)
    shutil.rmtree(pdf)
    found = _found(home, config)
    assert found.gone == (GoneItem(kind="skill", agent="Claude Code", name="pdf-tools"),)
    assert "pdf-tools" in {skill.name for skill in load_skills(config.skills, (), None).skills}
    remove_skill(skills_dir(), "Claude Code", "pdf-tools")
    assert (sorted(path.name for path in skills_dir().iterdir()), _found(home, config).gone) == (["notes"], ())


def test_a_server_is_added_by_its_name_and_fingerprint_and_started_only_as_it_was_when_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home: Path = tmp_path / "user"
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    notion: dict[str, object] = {
        "command": "npx",
        "args": ["-y", "notion-mcp", "--token", SECRETS[0]],
        "env": {"KEY": SECRETS[1], "OTHER": "${NOTION_KEY}"},
    }
    desktop: Path = _desktop(
        home,
        {
            "Notion_API": notion,
            "asks": {"command": "npx", "env": {"KEY": "${input:notion_key}"}},
            "taken": {"command": "npx"},
        },
    )
    cursor: Path = home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {"notion-api": {"url": "https://mcp.example.com/mcp"}}}))
    own: McpServerConfig = McpServerConfig(name="taken", command="npx")
    config: AppConfig = load_default_config().model_copy(update={"mcp": [own], "skills": SkillsConfig(dirs=[])})

    found: FoundElsewhere = _found(home, config)
    assert [(server.agent, server.name, server.used, server.refusal) for server in found.servers] == [
        ("Claude Desktop", "Notion_API", False, None),
        ("Claude Desktop", "asks", False, "it uses a variable that Claude Desktop fills in and Dot does not"),
        ("Claude Desktop", "taken", True, None),
        ("Cursor", "notion-api", False, None),
    ]
    with pytest.raises(ElsewhereError, match="fills in and Dot does not"):
        add_server(added_servers_path(), home, _server_of(found, "asks"))
    add_server(added_servers_path(), home, _server_of(found, "Notion_API"))
    record: AddedServers = read_added_servers(added_servers_path())
    assert [(item.agent, item.name, len(item.entry)) for item in record.servers] == [
        ("Claude Desktop", "Notion_API", 64)
    ]
    written: str = added_servers_path().read_text(encoding="utf-8")
    assert not [secret for secret in SECRETS if secret in written]

    # Cursor's server would be named as the one just added.
    found = _found(home, config)
    assert (_server_of(found, "Notion_API").added, _server_of(found, "notion-api").refusal) == (
        True,
        "Dot already has an MCP server called notion-api",
    )

    # When Dot starts: the entry as Claude Desktop writes it, under Dot's name; ${NOTION_KEY} is Dot's to look up.
    configs, problems = added_server_configs(home, record, frozenset({own.name}))
    assert problems == ()
    assert [(made.name, made.command, made.args, made.env) for made in configs] == [
        (
            "notion-api",
            "npx",
            ["-y", "notion-mcp", "--token", SECRETS[0]],
            {"KEY": SECRETS[1], "OTHER": "${NOTION_KEY}"},
        )
    ]
    # Changed in Claude Desktop after the owner's yes: Dot leaves it out until it is added again, and says why.
    _desktop(home, {"Notion_API": {**notion, "args": ["-y", "other-mcp"]}, "taken": {"command": "npx"}})
    changed: FoundServer = _server_of(_found(home, config), "Notion_API")
    assert (changed.added, changed.refusal) == (
        True,
        "its entry changed after it was added; remove it and add it again",
    )
    configs, problems = added_server_configs(home, record, frozenset({own.name}))
    assert (configs, [(problem.path, problem.reason) for problem in problems]) == (
        (),
        [(desktop, "the MCP server Notion_API changed after it was added; add it again")],
    )
    _desktop(home, {"Notion_API": {**notion, "args": ["${input:token}"]}, "taken": {"command": "npx"}})
    asks: FoundServer = _server_of(_found(home, config), "Notion_API")
    assert (asks.added, asks.refusal) == (True, "it uses a variable that Claude Desktop fills in and Dot does not")

    # Gone from Claude Desktop: it is told apart and not started, and can still be removed.
    _desktop(home, {"taken": {"command": "npx"}})
    configs, problems = added_server_configs(home, record, frozenset({own.name}))
    assert (configs, [(problem.path, problem.reason) for problem in problems]) == (
        (),
        [(desktop, "the MCP server Notion_API is not there any more")],
    )
    assert _found(home, config).gone == (GoneItem(kind="server", agent="Claude Desktop", name="Notion_API"),)
    remove_server(added_servers_path(), "Claude Desktop", "Notion_API")
    assert (read_added_servers(added_servers_path()).servers, _found(home, config).gone) == ((), ())
    with pytest.raises(ElsewhereError, match="was not added to Dot from here"):
        remove_server(added_servers_path(), "Claude Desktop", "Notion_API")
