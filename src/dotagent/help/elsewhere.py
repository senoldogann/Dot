"""What the Mac's other agents have set up (item 164), read from their own files without changing anything: the skills
of Claude Code, of its plugins and of Codex, and the MCP servers of Claude Code (its own, for every project), its
plugins, Claude Desktop, Cursor, VS Code and Codex. Dot does not use them until the owner adds them.

Only what names a thing is kept: a skill's name, description and folder; a server's name, transport and what it
reaches, the base name of its program or the host of its URL, each only when it is a plain name. A server's arguments,
environment and headers, and the rest of its URL, can hold keys and tokens: they are never kept, logged or told, not
even in the reason a server is left out. A file that cannot be read is told by its path and the kind of its error, a
server entry that cannot be read by the keys at fault.
"""

import json
import re
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from dotagent.core.errors import PluginError, SkillError
from dotagent.core.types import FrozenModel
from dotagent.plugins.manifest import server_name
from dotagent.plugins.types import MCP_FILE, SKILLS_DIR, McpServerJson
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import SKILL_FILE, Skill

type FoundTransport = Literal["stdio", "http", "sse"]
type _DotName = Callable[[str], str | None]

CLAUDE_CODE: str = "Claude Code"
_PLUGINS_FILE: tuple[str, ...] = (".claude", "plugins", "installed_plugins.json")
_SUPPORT: tuple[str, ...] = ("Library", "Application Support")
# The one word told of a server: the name of a program, or a host name or address (an IPv6 one without brackets).
_PLAIN_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_PLAIN_HOST: re.Pattern[str] = re.compile(r"[a-z0-9._:-]+")
_VARIABLE: re.Pattern[str] = re.compile(r"\$\{[^}]*\}")
# A file of JSON or TOML that cannot be read: an error of the disk, text that is not UTF-8 or not of its language and
# an integer of too many digits are ValueErrors; brackets nested too deep end in a RecursionError.
_UNREADABLE: tuple[type[Exception], ...] = (OSError, ValueError, RecursionError)


class FoundSkill(FrozenModel):
    """A skill of another agent; ``used`` when Dot already reads its folder."""

    agent: str
    name: str
    description: str
    folder: Path
    used: bool


class FoundServer(FrozenModel):
    """An MCP server of another agent: ``reaches`` is the base name of the program it starts, or the host of its URL;
    ``used`` when Dot already has a server under the name it would give it (``<plugin>-<server>`` for a plugin's)."""

    agent: str
    name: str
    transport: FoundTransport
    reaches: str
    used: bool


class FoundProblem(FrozenModel):
    """A file of another agent that could not be read, or a skill or a server in it that Dot could not use as it is
    written, and why."""

    path: Path
    reason: str


class FoundElsewhere(FrozenModel):
    skills: tuple[FoundSkill, ...]
    servers: tuple[FoundServer, ...]
    problems: tuple[FoundProblem, ...]


class _Found(FrozenModel):
    """What one source holds."""

    skills: tuple[FoundSkill, ...]
    servers: tuple[FoundServer, ...]
    problems: tuple[FoundProblem, ...]


_NOTHING: _Found = _Found(skills=(), servers=(), problems=())


def _unreadable(path: Path, error: Exception) -> FoundProblem:
    return FoundProblem(path=path, reason=f"cannot be read ({type(error).__name__})")


def _skills(agent: str, folder: Path, used: frozenset[Path]) -> _Found:
    """The skills in ``folder``, one ``<name>/SKILL.md`` each."""
    if not folder.is_dir():
        return _NOTHING
    skills: list[FoundSkill] = []
    problems: list[FoundProblem] = []
    for file in sorted(folder.glob(f"*/{SKILL_FILE}")):
        if not file.is_file():
            # A pipe or a device by that name would hold the reading forever.
            problems.append(FoundProblem(path=file, reason="it is not a file"))
            continue
        try:
            skill: Skill = parse_skill(file, file.read_text(encoding="utf-8"), agent)
        except (OSError, UnicodeDecodeError) as error:
            problems.append(_unreadable(file, error))
            continue
        except SkillError as error:
            problems.append(FoundProblem(path=file, reason=error.message))
            continue
        skills.append(
            FoundSkill(
                agent=agent,
                name=skill.name,
                description=skill.description,
                folder=file.parent,
                used=file.parent.resolve() in used,
            )
        )
    return _Found(skills=tuple(skills), servers=(), problems=tuple(problems))


def _program(command: str) -> str | None:
    """The base name of the program that ``command`` starts: the whole command's when it is the path of a file (a path
    can hold spaces), else its first word's (a command written with its arguments, which can hold keys). None when
    that word is not a plain name: a URL, or a variable set before the program (``KEY=value npx``), can hold a key."""
    whole: Path = Path(command)
    if whole.is_absolute() and whole.is_file():
        return whole.name
    words: list[str] = command.split()
    # A variable the agent fills in, as VS Code's ${env:HOME}, is no part of the program's name.
    word: str = _VARIABLE.sub("", words[0]) if words else ""
    if ":" in word or "=" in word:
        return None
    name: str = Path(word).name
    return name if _PLAIN_NAME.fullmatch(name) else None


def _host(url: str) -> str | None:
    """The host of ``url``, a plain host name or address; None when it names none or is no URL at all (an IPv6
    address left open)."""
    try:
        host: str | None = urlsplit(url).hostname
    except ValueError:
        return None
    return host if host is not None and _PLAIN_HOST.fullmatch(host) else None


def _reach(server: McpServerJson) -> tuple[FoundTransport, str] | str:
    """How a server is reached and the one word kept of it, the base name of its program or the host of its URL; or
    why no such word can be told."""
    if server.command is not None and server.transport in (None, "stdio"):
        program: str | None = _program(server.command)
        if program is None:
            return "has a command that does not start with the name of a program"
        return "stdio", program
    if server.url is not None and server.transport != "stdio":
        host: str | None = _host(server.url)
        if host is None:
            return "has a URL that names no host"
        return ("sse" if server.transport == "sse" else "http"), host
    return "names no command or URL to use"


def _misfit(name: str, error: ValidationError) -> str:
    """Why the entry of the server ``name`` cannot be read: the keys whose values are not of the kind Dot reads, never
    the values, which can be keys and tokens."""
    keys: list[str] = sorted({str(detail["loc"][0]) for detail in error.errors() if detail["loc"]})
    if not keys:
        return f"the MCP server {name} is not an object of keys and values"
    return f"the MCP server {name} has values Dot cannot read under {', '.join(keys)}"


def _as_written(server: str) -> str | None:
    """The name Dot would have a server under that the owner adds by hand: the one written."""
    return server


def _of_plugin(plugin: str) -> _DotName:
    """The name Dot gives a server of the plugin ``plugin`` when it reads the plugin; None when that name does not fit
    Dot's rule for server names, as Dot then cannot have the server."""

    def named(server: str) -> str | None:
        try:
            return server_name(plugin, server)
        except PluginError:
            return None

    return named


def _servers(agent: str, path: Path, table: object, used: frozenset[str], dot_name: _DotName) -> _Found:
    """The servers of ``table``, the object of names in ``path`` that holds them; one is ``used`` when ``dot_name``
    gives it a name Dot has a server under."""
    if table is None:
        return _NOTHING
    if not isinstance(table, dict):
        return _Found(
            skills=(), servers=(), problems=(FoundProblem(path=path, reason="its MCP servers are not named"),)
        )
    servers: list[FoundServer] = []
    problems: list[FoundProblem] = []
    for name, entry in table.items():
        try:
            server: McpServerJson = McpServerJson.model_validate(entry)
        except ValidationError as error:
            problems.append(FoundProblem(path=path, reason=_misfit(str(name), error)))
            continue
        reached: tuple[FoundTransport, str] | str = _reach(server)
        if isinstance(reached, str):
            problems.append(FoundProblem(path=path, reason=f"the MCP server {name} {reached}"))
            continue
        transport, reaches = reached
        servers.append(
            FoundServer(
                agent=agent, name=str(name), transport=transport, reaches=reaches, used=dot_name(str(name)) in used
            )
        )
    return _Found(skills=(), servers=tuple(servers), problems=tuple(problems))


def _json_servers(agent: str, path: Path, key: str, used: frozenset[str], dot_name: _DotName) -> _Found:
    """The servers that the JSON file ``path`` holds under ``key``; a file that does not exist holds none."""
    if not path.is_file():
        return _NOTHING
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except _UNREADABLE as error:
        return _Found(skills=(), servers=(), problems=(_unreadable(path, error),))
    return _servers(agent, path, loaded.get(key) if isinstance(loaded, dict) else None, used, dot_name)


def _codex_servers(path: Path, used: frozenset[str]) -> _Found:
    if not path.is_file():
        return _NOTHING
    try:
        loaded: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    except _UNREADABLE as error:
        return _Found(skills=(), servers=(), problems=(_unreadable(path, error),))
    return _servers("Codex", path, loaded.get("mcp_servers"), used, _as_written)


def _plugin_roots(path: Path) -> tuple[tuple[tuple[str, Path], ...], tuple[FoundProblem, ...]]:
    """The folders of the plugins installed in Claude Code, each with the plugin's name, from ``installed_plugins.json``
    (``{"plugins": {"<name>@<marketplace>": [{"installPath": ...}]}}``; an entry may be one object rather than a list)."""
    if not path.is_file():
        return (), ()
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except _UNREADABLE as error:
        return (), (_unreadable(path, error),)
    plugins: object = loaded.get("plugins") if isinstance(loaded, dict) else None
    if not isinstance(plugins, dict):
        return (), (FoundProblem(path=path, reason="it names no plugins"),)
    roots: dict[Path, str] = {}
    problems: list[FoundProblem] = []
    for key, value in plugins.items():
        for entry in value if isinstance(value, list) else [value]:
            folder: object = entry.get("installPath") if isinstance(entry, dict) else None
            if not isinstance(folder, str) or not Path(folder).is_dir():
                problems.append(FoundProblem(path=path, reason=f"the folder of the plugin {key} is not there"))
                continue
            roots.setdefault(Path(folder), str(key).split("@", 1)[0])
    return tuple((name, root) for root, name in roots.items()), tuple(problems)


def _sum(parts: Sequence[_Found]) -> FoundElsewhere:
    return FoundElsewhere(
        skills=tuple(skill for part in parts for skill in part.skills),
        servers=tuple(server for part in parts for server in part.servers),
        problems=tuple(problem for part in parts for problem in part.problems),
    )


def found_elsewhere(home: Path, used_folders: frozenset[Path], used_servers: frozenset[str]) -> FoundElsewhere:
    """The skills and MCP servers that the other agents of the user whose home is ``home`` have set up: the skills of
    Claude Code, its plugins and Codex, then the servers of Claude Code, its plugins, Claude Desktop, Cursor, VS Code
    and Codex, then what could not be read. ``used_folders`` are the skill folders Dot reads, ``used_servers`` the
    names of the MCP servers it has."""
    used: frozenset[Path] = frozenset(folder.resolve() for folder in used_folders)
    plugins, plugin_problems = _plugin_roots(home.joinpath(*_PLUGINS_FILE))
    support: Path = home.joinpath(*_SUPPORT)
    return _sum(
        [
            _skills(CLAUDE_CODE, home / ".claude" / "skills", used),
            *(_skills(f"{CLAUDE_CODE} plugin {name}", root / SKILLS_DIR, used) for name, root in plugins),
            _skills("Codex", home / ".codex" / "skills", used),
            _json_servers(CLAUDE_CODE, home / ".claude.json", "mcpServers", used_servers, _as_written),
            *(
                _json_servers(
                    f"{CLAUDE_CODE} plugin {name}", root / MCP_FILE, "mcpServers", used_servers, _of_plugin(name)
                )
                for name, root in plugins
            ),
            _json_servers(
                "Claude Desktop",
                support / "Claude" / "claude_desktop_config.json",
                "mcpServers",
                used_servers,
                _as_written,
            ),
            _json_servers("Cursor", home / ".cursor" / "mcp.json", "mcpServers", used_servers, _as_written),
            _json_servers("VS Code", support / "Code" / "User" / "mcp.json", "servers", used_servers, _as_written),
            _codex_servers(home / ".codex" / "config.toml", used_servers),
            _Found(skills=(), servers=(), problems=plugin_problems),
        ]
    )
