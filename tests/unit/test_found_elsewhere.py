"""What the Mac's other agents have set up (item 164), read from a home made here: the skills of Claude Code, of its
plugins and of Codex, and the MCP servers of Claude Code, its plugins, Claude Desktop, Cursor, VS Code and Codex. Only
names are kept: no key or token written in a server's arguments, environment, headers or URL ever comes out. Each says
whether Dot has it, and why the owner cannot add it when they cannot (item 165)."""

import json
from pathlib import Path

from dotagent.help.elsewhere import SKILL_COPY_LIMIT, AddedServer, DotHas, FoundElsewhere, found_elsewhere
from dotagent.server.views import ElsewhereImport, ElsewhereItem

SECRETS: tuple[str, ...] = tuple(f"secret-{index}-7f3a9c" for index in range(8))


def _skill(folder: Path, name: str, description: str) -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8")


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _has(home: Path, folders: frozenset[Path], servers: frozenset[str], added: tuple[AddedServer, ...]) -> DotHas:
    """What Dot has: the skill folders it reads, the servers of its own, and the servers added from elsewhere."""
    return DotHas(
        skill_folders=frozenset(folder.resolve() for folder in folders),
        skill_names=frozenset(folder.name for folder in folders),
        servers=servers,
        skills_dir=home / "dot" / "skills",
        added_servers=added,
    )


def _home(home: Path) -> None:
    _skill(home / ".claude" / "skills" / "pdf-tools", "pdf-tools", "Fill and merge PDF forms.")
    # Dot finds skills as it finds its own: in container folders a few levels down too.
    _skill(home / ".claude" / "skills" / "bundle" / "skills" / "csv-clean", "csv-clean", "Clean a CSV file.")
    _json(
        home / ".claude.json",
        {
            "oauthAccount": {"accessToken": SECRETS[0]},
            "mcpServers": {
                "github": {
                    "type": "stdio",
                    "command": "/usr/local/bin/npx",
                    "args": ["-y", "@example/server-github", "--token", SECRETS[1]],
                    "env": {"GITHUB_TOKEN": SECRETS[2]},
                }
            },
            "projects": {"/Users/someone/work": {"mcpServers": {"local-db": {"command": "node", "args": []}}}},
        },
    )
    plugin: Path = home / ".claude" / "plugins" / "cache" / "market" / "notes" / "1.0.0"
    _json(plugin / ".claude-plugin" / "plugin.json", {"name": "notes"})
    _skill(plugin / "skills" / "notes-search", "notes-search", "Search the notes of the week.")
    _json(
        plugin / ".mcp.json",
        {"mcpServers": {"Notes_DB": {"command": "node", "args": ["server.js"], "env": {"DB_PASSWORD": SECRETS[3]}}}},
    )
    _json(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {"notes@market": [{"scope": "user", "installPath": str(plugin), "version": "1.0.0"}]},
        },
    )
    _json(
        home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        {"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@example/server-filesystem", "/Users"]}}},
    )
    _json(
        home / ".cursor" / "mcp.json",
        {
            "mcpServers": {
                "docs": {
                    "url": f"https://someone:{SECRETS[4]}@mcp.example.com/mcp?key={SECRETS[5]}",
                    "headers": {"Authorization": f"Bearer {SECRETS[6]}"},
                }
            }
        },
    )
    _json(
        home / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
        # VS Code fills in ${env:HOME} itself.
        {
            "servers": {"playwright": {"type": "stdio", "command": "${env:HOME}/bin/playwright-mcp", "args": []}},
            "inputs": [],
        },
    )
    _skill(home / ".codex" / "skills" / "release-notes", "release-notes", "Write release notes from the commits.")
    # A program whose path holds a space is named by its file, not by the first word of its path.
    program: Path = home / "Library" / "Application Support" / "search" / "bin" / "search-mcp"
    program.parent.mkdir(parents=True)
    program.touch()
    (home / ".codex" / "config.toml").write_text(
        f'[mcp_servers.search]\ncommand = {json.dumps(str(program))}\nargs = []\nenv = {{ "KEY" = "{SECRETS[7]}" }}\n',
        encoding="utf-8",
    )


def test_the_skills_and_servers_of_other_agents_are_found_by_their_names_alone(tmp_path: Path) -> None:
    _home(tmp_path)
    used_folder: Path = tmp_path / ".claude" / "skills" / "pdf-tools"
    # Dot names a plugin's server <plugin>-<server>, in lower case with "-" for "_".
    used_servers: frozenset[str] = frozenset({"filesystem", "notes-notes-db"})
    found: FoundElsewhere = found_elsewhere(
        tmp_path, _has(tmp_path, frozenset({used_folder}), used_servers, ()), SKILL_COPY_LIMIT
    )
    assert [(skill.agent, skill.name, skill.used) for skill in found.skills] == [
        ("Claude Code", "csv-clean", False),
        ("Claude Code", "pdf-tools", True),
        ("Claude Code plugin notes", "notes-search", False),
        ("Codex", "release-notes", False),
    ]
    assert [(server.agent, server.name, server.transport, server.reaches, server.used) for server in found.servers] == [
        ("Claude Code", "github", "stdio", "npx", False),
        ("Claude Code plugin notes", "Notes_DB", "stdio", "node", True),
        ("Claude Desktop", "filesystem", "stdio", "npx", True),
        ("Cursor", "docs", "http", "mcp.example.com", False),
        ("VS Code", "playwright", "stdio", "playwright-mcp", False),
        ("Codex", "search", "stdio", "search-mcp", False),
    ]
    assert found.problems == ()
    assert [(skill.name, skill.added, skill.refusal) for skill in found.skills if not skill.used] == [
        ("csv-clean", False, None),
        ("notes-search", False, None),
        ("release-notes", False, None),
    ]
    # What Dot has needs no adding; a plugin's server comes with its plugin, and Dot does not fill in VS Code's
    # variables.
    assert [(server.name, server.refusal) for server in found.servers if not server.used] == [
        ("github", None),
        ("docs", None),
        ("playwright", "it uses a variable that VS Code fills in and Dot does not"),
        ("search", None),
    ]
    # Nothing that a key or a token could be written in comes out: not the arguments, the environment, the headers,
    # nor the user, path or query of a URL.
    written: str = found.model_dump_json()
    assert not [secret for secret in SECRETS if secret in written]


def test_what_cannot_be_read_is_told_without_its_keys_and_the_rest_is_still_found(tmp_path: Path) -> None:
    _home(tmp_path)
    (tmp_path / ".cursor" / "mcp.json").write_text("{not json", encoding="utf-8")
    # Brackets nested too deep for the parsers, and an integer of too many digits.
    (tmp_path / "Library" / "Application Support" / "Code" / "User" / "mcp.json").write_text("[" * 100_000)
    (tmp_path / ".codex" / "config.toml").write_text(f"depth = {'9' * 5000}\n", encoding="utf-8")
    (tmp_path / ".claude" / "skills" / "loose").mkdir()
    (tmp_path / ".claude" / "skills" / "loose" / "SKILL.md").write_text("No frontmatter here.\n", encoding="utf-8")
    _skill(tmp_path / ".codex" / "skills" / "deep", "deep", "x")
    (tmp_path / ".codex" / "skills" / "deep" / "SKILL.md").write_text(f"---\nname: {'[' * 100_000}\n---\nDo it.\n")
    _json(
        tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        {
            "mcpServers": {
                "filesystem": {"command": "npx"},
                "timer": {"command": "node", "args": ["--key", SECRETS[0], 7]},
                "web": {"url": "http://[mcp.example.com/sse"},
                # A command whose first word is a URL, or a variable set before the program, tells nothing of it.
                "remote": {"command": f"https://someone:{SECRETS[1]}@mcp.example.com/x?key={SECRETS[2]}"},
                "prefixed": {"command": f"API_KEY={SECRETS[3]} npx server"},
            }
        },
    )
    found: FoundElsewhere = found_elsewhere(tmp_path, _has(tmp_path, frozenset(), frozenset(), ()), SKILL_COPY_LIMIT)
    assert sorted((problem.path.name, problem.reason) for problem in found.problems) == [
        ("SKILL.md", "SKILL.md does not start with the --- line that opens the frontmatter"),
        ("SKILL.md", "the frontmatter of SKILL.md is nested too deeply to read"),
        (
            "claude_desktop_config.json",
            "the MCP server prefixed has a command that does not start with the name of a program",
        ),
        (
            "claude_desktop_config.json",
            "the MCP server remote has a command that does not start with the name of a program",
        ),
        ("claude_desktop_config.json", "the MCP server timer has values Dot cannot read under args"),
        ("claude_desktop_config.json", "the MCP server web has a URL that names no host"),
        ("config.toml", "cannot be read (ValueError)"),
        ("mcp.json", "cannot be read (JSONDecodeError)"),
        ("mcp.json", "cannot be read (RecursionError)"),
    ]
    written: str = found.model_dump_json()
    assert not [secret for secret in SECRETS if secret in written]
    assert [(server.agent, server.name) for server in found.servers] == [
        ("Claude Code", "github"),
        ("Claude Code plugin notes", "Notes_DB"),
        ("Claude Desktop", "filesystem"),
    ]
    assert [skill.name for skill in found.skills] == ["csv-clean", "pdf-tools", "notes-search", "release-notes"]


def test_each_skill_and_server_is_found_once_by_its_agent_and_name_so_an_import_of_them_all_is_valid(
    tmp_path: Path,
) -> None:
    # Item 236: one name in three bundles of a skills folder, and one copy reached again through a link.
    skills: Path = tmp_path / ".claude" / "skills"
    for bundle in ("first", "second", "third"):
        _skill(skills / bundle / "skills" / "pdf-tools", "pdf-tools", f"Fill PDF forms ({bundle}).")
    (skills / "linked").symlink_to(skills / "first" / "skills" / "pdf-tools")
    # A plugin installed for a project at 0.9.0 and for the user at 1.0.0, and one of the same name elsewhere.
    cache: Path = tmp_path / ".claude" / "plugins" / "cache"
    for market, version in (("market", "0.9.0"), ("market", "1.0.0"), ("other", "2.0.0")):
        plugin: Path = cache / market / "notes" / version
        _skill(plugin / "skills" / "notes-search", "notes-search", f"Search the notes ({market} {version}).")
        _json(plugin / ".mcp.json", {"mcpServers": {"notes_db": {"command": "node", "args": ["server.js"]}}})
    _json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "notes@market": [
                    {
                        "scope": "project",
                        "projectPath": "/Users/someone/work",
                        "installPath": str(cache / "market" / "notes" / "0.9.0"),
                    },
                    {"scope": "user", "installPath": str(cache / "market" / "notes" / "1.0.0")},
                ],
                "notes@other": [{"scope": "user", "installPath": str(cache / "other" / "notes" / "2.0.0")}],
            },
        },
    )
    found: FoundElsewhere = found_elsewhere(tmp_path, _has(tmp_path, frozenset(), frozenset(), ()), SKILL_COPY_LIMIT)
    assert [(skill.agent, skill.name, skill.description) for skill in found.skills] == [
        ("Claude Code", "pdf-tools", "Fill PDF forms (first)."),
        ("Claude Code plugin notes", "notes-search", "Search the notes (market 1.0.0)."),
    ]
    assert [(server.agent, server.name) for server in found.servers] == [("Claude Code plugin notes", "notes_db")]
    first: Path = skills / "first" / "skills" / "pdf-tools"
    assert [(problem.path, problem.reason) for problem in found.problems] == [
        (
            skills / "second" / "skills" / "pdf-tools" / "SKILL.md",
            f"another skill is already called pdf-tools: {first}",
        ),
        (skills / "third" / "skills" / "pdf-tools" / "SKILL.md", f"another skill is already called pdf-tools: {first}"),
        (
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
            "another plugin is already called notes: notes@market",
        ),
    ]
    # What the import dialog sends when the owner chooses all: Dot accepts it.
    items: tuple[ElsewhereItem, ...] = (
        *(ElsewhereItem(kind="skill", agent=skill.agent, name=skill.name) for skill in found.skills),
        *(ElsewhereItem(kind="server", agent=server.agent, name=server.name) for server in found.servers),
    )
    assert len(ElsewhereImport(items=items).items) == 3
