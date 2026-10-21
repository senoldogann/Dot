"""What the Mac's other agents have set up (item 164), read from their own files without changing anything: the skills
of Claude Code, of its plugins and of Codex, and the MCP servers of Claude Code (its own, for every project), its
plugins, Claude Desktop, Cursor, VS Code and Codex. Dot does not use them until the owner adds them (item 165,
:mod:`dotagent.help.added`): each says whether Dot has it already, was given it from here, or why it cannot be added,
and what was added from here that its agent no longer has is told apart.

Only what names a thing is kept: a skill's name, description and folder; a server's name, transport and what it
reaches, the base name of its program or the host of its URL, each only when it is a plain name. A server's arguments,
environment and headers, and the rest of its URL, can hold keys and tokens: they are never kept, logged or told, not
even in the reason a server is left out or cannot be added. A file that cannot be read is told by its path and the kind
of its error, a server entry that cannot be read by the keys at fault.
"""

import hashlib
import json
import os
import re
import stat
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from dotagent.config import MCP_NAME_PATTERN, McpServerConfig, McpTransport
from dotagent.core.errors import ElsewhereError, PluginError, SkillError
from dotagent.core.types import FrozenModel
from dotagent.mcp.launch import SECRET_REFERENCE
from dotagent.plugins.manifest import server_name
from dotagent.plugins.types import MCP_FILE, SKILLS_DIR, JsonTransport, McpServerJson
from dotagent.skills.catalog import HOME_SOURCE
from dotagent.skills.discover import find_skill_files
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import Skill

type FoundTransport = Literal["stdio", "http", "sse"]
type _Language = Literal["json", "toml"]
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
_TRANSPORTS: dict[JsonTransport, McpTransport] = {
    "stdio": "stdio",
    "http": "http",
    "sse": "sse",
    "streamable-http": "http",
}
_SERVER_NAME_RULE: str = "a lower-case letter, then lower-case letters, digits and hyphens, at most 24 characters"
_COMES_WITH_ITS_PLUGIN: str = "it comes with its plugin, which Dot reads when the plugin's folder is in [plugins] dirs"
# The file Dot writes into its copy of a skill it added from another agent (item 165); hidden, so neither the catalog
# nor the skill tool counts it among the skill's files.
ADDED_MARKER: str = ".added-elsewhere.json"
_CHANGED: str = "its entry changed after it was added; remove it and add it again"


class ServerFile(FrozenModel):
    """Where an agent keeps the MCP servers it starts for every project: the file under the home, the key of the table
    that names them, and the language of the file."""

    agent: str
    path: tuple[str, ...]
    key: str
    language: _Language


CLAUDE_CODE_SERVERS: ServerFile = ServerFile(
    agent=CLAUDE_CODE, path=(".claude.json",), key="mcpServers", language="json"
)
_OTHER_SERVERS: tuple[ServerFile, ...] = (
    ServerFile(
        agent="Claude Desktop",
        path=(*_SUPPORT, "Claude", "claude_desktop_config.json"),
        key="mcpServers",
        language="json",
    ),
    ServerFile(agent="Cursor", path=(".cursor", "mcp.json"), key="mcpServers", language="json"),
    ServerFile(agent="VS Code", path=(*_SUPPORT, "Code", "User", "mcp.json"), key="servers", language="json"),
    ServerFile(agent="Codex", path=(".codex", "config.toml"), key="mcp_servers", language="toml"),
)
# The agents whose servers can be added to Dot one by one; a plugin's come with the plugin.
SERVER_FILES: dict[str, ServerFile] = {file.agent: file for file in (CLAUDE_CODE_SERVERS, *_OTHER_SERVERS)}


class CopyLimit(FrozenModel):
    """How big a skill folder Dot copies at most when it adds the skill: its files, folders not counted, and their
    bytes."""

    files: int
    bytes: int


# A skill's instructions with their scripts, references and assets fit well under it; a whole project does not.
SKILL_COPY_LIMIT: CopyLimit = CopyLimit(files=2000, bytes=50 * 1024 * 1024)


class AddedServer(FrozenModel):
    """An MCP server of another agent that the owner added to Dot (item 165): its agent and its name, as found, and the
    fingerprint of its entry as it was then (:func:`entry_fingerprint`), from which nothing of the entry can be read
    back."""

    agent: str
    name: str
    entry: str


class SkillCopy(FrozenModel):
    """What Dot's copy of a skill it added from another agent says of the skill: its agent, its name and the folder it
    was copied from."""

    agent: str
    name: str
    folder: Path


class DotHas(FrozenModel):
    """What Dot has already, to mark what was found: the skill folders it reads (resolved) and the names of its skills,
    the names of its own MCP servers (``[[mcp]]`` and plugins'), the folder it copies an added skill into, and the
    servers added from elsewhere."""

    skill_folders: frozenset[Path]
    skill_names: frozenset[str]
    servers: frozenset[str]
    skills_dir: Path
    added_servers: tuple[AddedServer, ...]


class FoundSkill(FrozenModel):
    """A skill of another agent: ``used`` when Dot already reads its folder, ``added`` when the owner added it from here
    (Dot's skills folder holds the copy Dot made of it), ``refusal`` why it cannot be added when it cannot."""

    agent: str
    name: str
    description: str
    folder: Path
    used: bool
    added: bool
    refusal: str | None


class FoundServer(FrozenModel):
    """An MCP server of another agent: ``reaches`` is the base name of the program it starts, or the host of its URL;
    ``used`` when Dot has a server of its own under the name it would give it (``<plugin>-<server>`` for a plugin's),
    ``added`` when the owner added it from here, ``refusal`` why Dot cannot take it as it is written now, when it
    cannot: why it cannot be added, or, once added, why Dot leaves it out when it starts."""

    agent: str
    name: str
    transport: FoundTransport
    reaches: str
    used: bool
    added: bool
    refusal: str | None


class FoundProblem(FrozenModel):
    """A file of another agent that could not be read, or a skill or a server in it that Dot could not use as it is
    written, and why."""

    path: Path
    reason: str


class GoneItem(FrozenModel):
    """A skill or an MCP server that the owner added to Dot and its agent no longer has: Dot keeps its copy of a skill,
    and leaves such a server out when it starts, until the owner removes it."""

    kind: Literal["skill", "server"]
    agent: str
    name: str


class FoundElsewhere(FrozenModel):
    skills: tuple[FoundSkill, ...]
    servers: tuple[FoundServer, ...]
    problems: tuple[FoundProblem, ...]
    gone: tuple[GoneItem, ...]


class _Found(FrozenModel):
    """What one source holds."""

    skills: tuple[FoundSkill, ...]
    servers: tuple[FoundServer, ...]
    problems: tuple[FoundProblem, ...]


_NOTHING: _Found = _Found(skills=(), servers=(), problems=())


def _unreadable(path: Path, error: Exception) -> FoundProblem:
    return FoundProblem(path=path, reason=f"cannot be read ({type(error).__name__})")


def _only(problem: FoundProblem) -> _Found:
    return _Found(skills=(), servers=(), problems=(problem,))


def read_table(path: Path, key: str, language: _Language) -> object:
    """The value under ``key`` in the JSON or TOML file ``path``; None when the file or the key is not there. Raises
    ElsewhereError when the file cannot be read, told by the kind of the error alone."""
    if not path.is_file():
        return None
    try:
        text: str = path.read_text(encoding="utf-8")
        loaded: object = json.loads(text) if language == "json" else tomllib.loads(text)
    except _UNREADABLE as error:
        raise ElsewhereError(f"cannot be read ({type(error).__name__})", {"path": str(path)}) from error
    return loaded.get(key) if isinstance(loaded, dict) else None


def entry_fingerprint(server: McpServerJson) -> str:
    """A fingerprint of what Dot starts the server ``server`` with: its command, arguments, environment, URL, headers
    and transport, each as written. Dot starts a server added from another agent only while its entry still has the
    fingerprint it had when the owner added it."""
    written: dict[str, object] = {
        "command": server.command,
        "args": server.args,
        "env": server.env,
        "url": server.url,
        "headers": server.headers,
        "transport": server.transport,
    }
    canonical: str = json.dumps(written, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _raise(error: OSError) -> None:
    raise error


def copy_refusal(folder: Path, limit: CopyLimit) -> str | None:
    """Why Dot does not copy the skill folder ``folder``, its hidden entries left out: it holds a link, which could
    lead out of the copy, or something that is neither a file nor a folder (reading a pipe would never end), or it is
    over ``limit``; None when Dot copies it. Raises OSError when the folder cannot be read."""
    files: int = 0
    size: int = 0
    for root, folders, names in os.walk(folder, onerror=_raise):
        folders[:] = [name for name in folders if not name.startswith(".")]
        for name in [*folders, *(name for name in names if not name.startswith("."))]:
            status: os.stat_result = os.lstat(Path(root) / name)
            if stat.S_ISLNK(status.st_mode):
                return "it holds a link, which Dot does not copy"
            if stat.S_ISDIR(status.st_mode):
                continue
            if not stat.S_ISREG(status.st_mode):
                return "it holds something that is neither a file nor a folder"
            files += 1
            size += status.st_size
            if files > limit.files or size > limit.bytes:
                return f"it is too big to copy (at most {limit.files} files and {limit.bytes // 2**20} MB)"
    return None


def copied_from(copy: Path) -> SkillCopy | None:
    """What the skill folder ``copy`` in Dot's skills folder says of the skill it was copied from, when Dot copied it
    there from another agent; None when it did not, or when its marker cannot be read: then it is not Dot's to
    remove."""
    marker: Path = copy / ADDED_MARKER
    if copy.is_symlink() or marker.is_symlink() or not marker.is_file():
        return None
    try:
        return SkillCopy.model_validate_json(marker.read_bytes())
    except (OSError, ValidationError):
        return None


def copied_skill_names(skills: Sequence[Skill]) -> frozenset[str]:
    """The names of those of ``skills`` that are Dot's copies of another agent's: skills of its own folder whose marker
    says so (:func:`copied_from`). Their Customize card marks them as imported, and so does the composer's menu (item
    477)."""
    return frozenset(
        skill.name for skill in skills if skill.source == HOME_SOURCE and copied_from(skill.root) is not None
    )


def _skill_refusal(skill: Skill, copy: Path, has: DotHas, limit: CopyLimit) -> str | None:
    if skill.name in has.skill_names:
        return f"Dot already has a skill called {skill.name}"
    if copy.exists() or copy.is_symlink():
        return f"Dot's skills folder already holds {skill.name}"
    try:
        return copy_refusal(skill.root.resolve(), limit)
    except OSError as error:
        return f"its folder cannot be read ({type(error).__name__})"


def _found_skill(agent: str, skill: Skill, has: DotHas, limit: CopyLimit) -> FoundSkill:
    copy: Path = has.skills_dir / skill.name
    made: SkillCopy | None = copied_from(copy)
    added: bool = made is not None and (made.agent, made.name) == (agent, skill.name)
    used: bool = skill.root.resolve() in has.skill_folders
    return FoundSkill(
        agent=agent,
        name=skill.name,
        description=skill.description,
        folder=skill.root,
        used=used,
        added=added,
        refusal=None if used or added else _skill_refusal(skill, copy, has, limit),
    )


def _skills(agent: str, folder: Path, has: DotHas, limit: CopyLimit) -> _Found:
    """The skills in ``folder``, found as Dot finds its own: the skill folders in it, and those of container folders a
    few levels down (:func:`dotagent.skills.discover.find_skill_files`). As in Dot's own catalog, a file reached again
    through a link is read once, and a skill named as one before it is left out with the reason: an import names each
    skill by its agent and its name (item 236)."""
    if not folder.is_dir():
        return _NOTHING
    try:
        files: tuple[Path, ...] = find_skill_files(folder)
    except SkillError as error:
        return _only(FoundProblem(path=folder, reason=error.message))
    skills: dict[str, FoundSkill] = {}
    seen: set[Path] = set()
    problems: list[FoundProblem] = []
    for file in files:
        real: Path = file.resolve()
        if real in seen:
            continue
        seen.add(real)
        try:
            skill: Skill = parse_skill(file, file.read_text(encoding="utf-8"), agent)
        except (OSError, UnicodeDecodeError) as error:
            problems.append(_unreadable(file, error))
            continue
        except SkillError as error:
            problems.append(FoundProblem(path=file, reason=error.message))
            continue
        taken: FoundSkill | None = skills.get(skill.name)
        if taken is not None:
            problems.append(
                FoundProblem(path=file, reason=f"another skill is already called {skill.name}: {taken.folder}")
            )
            continue
        skills[skill.name] = _found_skill(agent, skill, has, limit)
    return _Found(skills=tuple(skills.values()), servers=(), problems=tuple(problems))


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


def _keys(error: ValidationError) -> list[str]:
    """The keys whose values are not of the kind asked for, never the values, which can be keys and tokens."""
    return sorted({str(detail["loc"][0]) for detail in error.errors() if detail["loc"]})


def _misfit(name: str, error: ValidationError) -> str:
    """Why the entry of the server ``name`` cannot be read."""
    keys: list[str] = _keys(error)
    if not keys:
        return f"the MCP server {name} is not an object of keys and values"
    return f"the MCP server {name} has values Dot cannot read under {', '.join(keys)}"


def dot_server_name(server: str) -> str | None:
    """The name Dot gives a server of another agent: the server's own in lower case with "-" for "_" and spaces, as for
    a plugin's; None when that does not fit Dot's rule for server names, as Dot then cannot have the server."""
    named: str = server.lower().replace("_", "-").replace(" ", "-")
    return named if re.fullmatch(MCP_NAME_PATTERN, named) else None


def _of_plugin(plugin: str) -> _DotName:
    """The name Dot gives a server of the plugin ``plugin`` when it reads the plugin; None when that name does not fit
    Dot's rule for server names."""

    def named(server: str) -> str | None:
        try:
            return server_name(plugin, server)
        except PluginError:
            return None

    return named


def _unfilled(server: McpServerJson) -> bool:
    """Whether the entry holds a variable that its agent fills in and Dot does not: any ``${...}`` in the command, the
    arguments or the URL, and one in the environment or the headers other than ``${NAME}``, which Dot looks up like an
    API key."""
    plain: list[str] = [server.command or "", *server.args, server.url or ""]
    secret: list[str] = [*server.env.values(), *server.headers.values()]
    return any("${" in value for value in plain) or any("${" in SECRET_REFERENCE.sub("", value) for value in secret)


def server_config(agent: str, name: str, server: McpServerJson, taken: frozenset[str]) -> McpServerConfig:
    """Dot's configuration of the server ``name`` of ``agent`` as the agent writes it: named by Dot's rule, started with
    the entry's command, arguments and environment, or reached at its URL with its headers. Raises ElsewhereError,
    telling why without a value, when Dot cannot use it so: its name does not fit Dot's rule or is in ``taken``, it
    holds a variable that Dot does not fill in, or a value Dot does not take."""
    context: dict[str, str] = {"agent": agent, "server": name}
    dot: str | None = dot_server_name(name)
    if dot is None:
        raise ElsewhereError(f"its name does not fit Dot's rule for server names ({_SERVER_NAME_RULE})", context)
    if dot in taken:
        raise ElsewhereError(f"Dot already has an MCP server called {dot}", context)
    if _unfilled(server):
        raise ElsewhereError(f"it uses a variable that {agent} fills in and Dot does not", context)
    try:
        return McpServerConfig(
            name=dot,
            command=server.command,
            args=server.args,
            env=server.env,
            url=server.url,
            headers=server.headers,
            transport=None if server.transport is None else _TRANSPORTS[server.transport],
        )
    except ValidationError as error:
        raise ElsewhereError(
            f"Dot cannot start it as it is written (under {', '.join(_keys(error))})", context
        ) from error


def added_server(added: Sequence[AddedServer], agent: str, name: str) -> AddedServer | None:
    """The server ``name`` of ``agent`` among those ``added``; None when it is not one of them."""
    return next((item for item in added if (item.agent, item.name) == (agent, name)), None)


def _taken(has: DotHas, agent: str, name: str) -> frozenset[str]:
    """The names of Dot's servers, with those of the servers added from elsewhere other than ``name`` of ``agent``."""
    added: list[str | None] = [
        dot_server_name(item.name) for item in has.added_servers if (item.agent, item.name) != (agent, name)
    ]
    return has.servers | frozenset(named for named in added if named is not None)


def _server_refusal(agent: str, name: str, server: McpServerJson, has: DotHas) -> str | None:
    """Why Dot cannot take the server as it is written now; for one added already, also that its entry changed since."""
    try:
        server_config(agent, name, server, _taken(has, agent, name))
    except ElsewhereError as error:
        return error.message
    added: AddedServer | None = added_server(has.added_servers, agent, name)
    if added is not None and added.entry != entry_fingerprint(server):
        return _CHANGED
    return None


def _servers(
    agent: str, path: Path, table: object, has: DotHas, dot_name: _DotName, refusal_of_all: str | None
) -> _Found:
    """The servers of ``table``, the object of names in ``path`` that holds them. One is ``used`` when ``dot_name``
    gives it a name Dot has a server under; ``refusal_of_all``, when given, is why none of them can be added."""
    if table is None:
        return _NOTHING
    if not isinstance(table, dict):
        return _only(FoundProblem(path=path, reason="its MCP servers are not named"))
    servers: list[FoundServer] = []
    problems: list[FoundProblem] = []
    for key, entry in table.items():
        name: str = str(key)
        try:
            server: McpServerJson = McpServerJson.model_validate(entry)
        except ValidationError as error:
            problems.append(FoundProblem(path=path, reason=_misfit(name, error)))
            continue
        reached: tuple[FoundTransport, str] | str = _reach(server)
        if isinstance(reached, str):
            problems.append(FoundProblem(path=path, reason=f"the MCP server {name} {reached}"))
            continue
        transport, reaches = reached
        used: bool = dot_name(name) in has.servers
        added: bool = added_server(has.added_servers, agent, name) is not None
        refusal: str | None = None
        if not used:
            refusal = refusal_of_all if refusal_of_all is not None else _server_refusal(agent, name, server, has)
        servers.append(
            FoundServer(
                agent=agent, name=name, transport=transport, reaches=reaches, used=used, added=added, refusal=refusal
            )
        )
    return _Found(skills=(), servers=tuple(servers), problems=tuple(problems))


def _file_servers(home: Path, source: ServerFile, has: DotHas) -> _Found:
    path: Path = home.joinpath(*source.path)
    try:
        table: object = read_table(path, source.key, source.language)
    except ElsewhereError as error:
        return _only(FoundProblem(path=path, reason=error.message))
    return _servers(source.agent, path, table, has, dot_server_name, None)


def _plugin_servers(name: str, root: Path, has: DotHas) -> _Found:
    path: Path = root / MCP_FILE
    try:
        table: object = read_table(path, "mcpServers", "json")
    except ElsewhereError as error:
        return _only(FoundProblem(path=path, reason=error.message))
    return _servers(f"{CLAUDE_CODE} plugin {name}", path, table, has, _of_plugin(name), _COMES_WITH_ITS_PLUGIN)


def _plugin_roots(path: Path) -> tuple[tuple[tuple[str, Path], ...], tuple[FoundProblem, ...]]:
    """The folder of each plugin installed in Claude Code, with the plugin's name, from ``installed_plugins.json``
    (``{"plugins": {"<name>@<marketplace>": [{"scope": "user", "installPath": ...}]}}``; an entry may be one object
    rather than a list). One folder a name, as an import names a plugin's skills and servers by its name (item 236):
    the install for the user, which Claude Code uses outside a project, or else the first listed; a plugin named as one
    of another marketplace before it is left out with the reason."""
    if not path.is_file():
        return (), ()
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except _UNREADABLE as error:
        return (), (_unreadable(path, error),)
    plugins: object = loaded.get("plugins") if isinstance(loaded, dict) else None
    if not isinstance(plugins, dict):
        return (), (FoundProblem(path=path, reason="it names no plugins"),)
    roots: dict[str, Path] = {}
    keys: dict[str, str] = {}
    problems: list[FoundProblem] = []
    for key, value in plugins.items():
        name: str = str(key).split("@", 1)[0]
        installs: list[tuple[bool, Path]] = []
        for entry in value if isinstance(value, list) else [value]:
            folder: object = entry.get("installPath") if isinstance(entry, dict) else None
            if not isinstance(entry, dict) or not isinstance(folder, str) or not Path(folder).is_dir():
                problems.append(FoundProblem(path=path, reason=f"the folder of the plugin {key} is not there"))
                continue
            installs.append((entry.get("scope") == "user", Path(folder)))
        if not installs:
            continue
        if name in roots:
            problems.append(FoundProblem(path=path, reason=f"another plugin is already called {name}: {keys[name]}"))
            continue
        roots[name] = next((root for for_user, root in installs if for_user), installs[0][1])
        keys[name] = str(key)
    return tuple(roots.items()), tuple(problems)


def _copies(folder: Path) -> tuple[SkillCopy, ...]:
    """What the copies of skills that Dot added from other agents to its skills folder ``folder`` say of them."""
    if not folder.is_dir():
        return ()
    marked: list[SkillCopy | None] = [copied_from(child) for child in sorted(folder.iterdir())]
    return tuple(made for made in marked if made is not None)


def _gone(parts: Sequence[_Found], has: DotHas) -> tuple[GoneItem, ...]:
    """What the owner added that its agent no longer has: the copies of skills, then the servers of Dot's record, that
    none of ``parts`` holds."""
    skills: set[tuple[str, str]] = {(skill.agent, skill.name) for part in parts for skill in part.skills}
    servers: set[tuple[str, str]] = {(server.agent, server.name) for part in parts for server in part.servers}
    return (
        *(
            GoneItem(kind="skill", agent=made.agent, name=made.name)
            for made in _copies(has.skills_dir)
            if (made.agent, made.name) not in skills
        ),
        *(
            GoneItem(kind="server", agent=item.agent, name=item.name)
            for item in has.added_servers
            if (item.agent, item.name) not in servers
        ),
    )


def _sum(parts: Sequence[_Found], has: DotHas) -> FoundElsewhere:
    return FoundElsewhere(
        skills=tuple(skill for part in parts for skill in part.skills),
        servers=tuple(server for part in parts for server in part.servers),
        problems=tuple(problem for part in parts for problem in part.problems),
        gone=_gone(parts, has),
    )


def found_elsewhere(home: Path, has: DotHas, limit: CopyLimit) -> FoundElsewhere:
    """The skills and MCP servers that the other agents of the user whose home is ``home`` have set up: the skills of
    Claude Code, its plugins and Codex, then the servers of Claude Code, its plugins, Claude Desktop, Cursor, VS Code
    and Codex, then what could not be read; each marked by what Dot ``has`` (a skill folder over ``limit`` cannot be
    added); then what the owner added that its agent no longer has."""
    plugins, plugin_problems = _plugin_roots(home.joinpath(*_PLUGINS_FILE))
    return _sum(
        [
            _skills(CLAUDE_CODE, home / ".claude" / "skills", has, limit),
            *(_skills(f"{CLAUDE_CODE} plugin {name}", root / SKILLS_DIR, has, limit) for name, root in plugins),
            _skills("Codex", home / ".codex" / "skills", has, limit),
            _file_servers(home, CLAUDE_CODE_SERVERS, has),
            *(_plugin_servers(name, root, has) for name, root in plugins),
            *(_file_servers(home, source, has) for source in _OTHER_SERVERS),
            _Found(skills=(), servers=(), problems=plugin_problems),
        ],
        has,
    )
