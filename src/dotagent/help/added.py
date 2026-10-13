"""Help found elsewhere, added to Dot with the owner's yes (item 165): a skill or an MCP server that another agent of
the Mac set up (:mod:`dotagent.help.elsewhere`) is used by Dot only once the owner adds it, and what Dot keeps of it is
in Dot's own home, never in ``config.toml`` or in the other agent's files.

A skill is added as a copy in Dot's skills folder (``<home>/skills/<name>``), which Dot reads for every run and a run
may read but never change: the copy is what the owner said yes to, and it does not follow the agent's later changes.
Its hidden entries are left out, and a hidden marker (:data:`dotagent.help.elsewhere.ADDED_MARKER`) says where it came
from; removing the skill deletes that copy and nothing else.

An MCP server is added as a line of Dot's record (``<home>/added-servers.json``), which a run may read but never change:
its agent, its name and the fingerprint of its entry (:func:`dotagent.help.elsewhere.entry_fingerprint`), nothing it is
started with. Each time Dot starts, it reads the server's entry from the agent's own file and starts it as the agent
writes it, named by Dot's rule (:func:`dotagent.help.elsewhere.server_config`), while the entry is still the one the
owner said yes to; its arguments, environment and headers stay in memory and are never logged. A server of a plugin
comes with its plugin and is not added alone.
"""

import json
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from dotagent.config import AppConfig, McpServerConfig
from dotagent.core.errors import ElsewhereError
from dotagent.core.paths import skills_dir
from dotagent.core.types import FrozenModel
from dotagent.help.elsewhere import (
    ADDED_MARKER,
    SERVER_FILES,
    AddedServer,
    CopyLimit,
    DotHas,
    FoundProblem,
    FoundServer,
    FoundSkill,
    ServerFile,
    SkillCopy,
    added_server,
    copied_from,
    copy_refusal,
    entry_fingerprint,
    read_table,
    server_config,
)
from dotagent.plugins.types import McpServerJson, Plugin
from dotagent.skills.types import SkillCatalog

_FILE_MODE: int = 0o600
# One change of the record at a time in this process: each reads it, changes it and replaces it.
_RECORD_LOCK: threading.Lock = threading.Lock()


class AddedServers(FrozenModel):
    """Dot's record of the MCP servers added from other agents, in the order they were added."""

    servers: tuple[AddedServer, ...]


def read_added_servers(path: Path) -> AddedServers:
    """The record at ``path``; none added when the file is not there. Raises ElsewhereError when it cannot be read."""
    if not path.exists():
        return AddedServers(servers=())
    try:
        return AddedServers.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise ElsewhereError(
            f"Dot's record of the MCP servers added from other agents cannot be read ({type(error).__name__})",
            {"path": str(path)},
        ) from error


def _write(path: Path, record: AddedServers) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), indent=2) + "\n")
        os.chmod(temporary, _FILE_MODE)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def dot_has(config: AppConfig, catalog: SkillCatalog, plugins: Sequence[Plugin], added: AddedServers) -> DotHas:
    """What Dot has, for :func:`dotagent.help.elsewhere.found_elsewhere`: the skills of ``catalog``, the servers of
    ``[[mcp]]`` and of ``plugins``, Dot's skills folder and the servers ``added``."""
    return DotHas(
        skill_folders=frozenset(skill.root.resolve() for skill in catalog.skills),
        skill_names=frozenset(skill.name for skill in catalog.skills),
        servers=frozenset([*(server.name for server in config.mcp), *(s.name for p in plugins for s in p.servers)]),
        skills_dir=skills_dir(),
        added_servers=added.servers,
    )


def _open_folders(tree: Path) -> None:
    """Let the owner write in every folder of ``tree``, a copy Dot made: a copy keeps the modes of what it copies, and
    a folder that cannot be written in could be neither marked nor deleted."""
    for root, _folders, _names in os.walk(tree):
        os.chmod(root, stat.S_IMODE(os.lstat(root).st_mode) | stat.S_IRWXU)


def _discard(tree: Path) -> None:
    """Delete ``tree``, a folder Dot made in its skills folder, whatever modes its copied folders have."""
    _open_folders(tree)
    shutil.rmtree(tree)


def _copy(source: Path, staging: Path, skill: FoundSkill, limit: CopyLimit) -> Path:
    """A copy of ``source`` in ``staging``, its hidden entries left out and the marker of ``skill`` written in it,
    checked again once made: the folder may have changed since it was looked at. Raises ElsewhereError when it did."""
    copy: Path = staging / skill.name
    shutil.copytree(source, copy, symlinks=True, ignore=shutil.ignore_patterns(".*"))
    _open_folders(copy)
    changed: str | None = copy_refusal(copy, limit)
    if changed is not None:
        raise ElsewhereError(f"its folder changed while Dot copied it: {changed}", {"skill": skill.name})
    made: SkillCopy = SkillCopy(agent=skill.agent, name=skill.name, folder=skill.folder)
    (copy / ADDED_MARKER).write_text(made.model_dump_json(), encoding="utf-8")
    return copy


def add_skill(folder: Path, skill: FoundSkill, limit: CopyLimit) -> None:
    """Copy ``skill`` into Dot's skills folder ``folder``, with the marker of where it came from. Raises ElsewhereError
    when Dot has it already or cannot take it: its name is taken, or its folder holds a link or something that is not a
    file, or is over ``limit``, or cannot be read or copied."""
    context: dict[str, str] = {"agent": skill.agent, "skill": skill.name}
    if skill.added:
        raise ElsewhereError(f"the skill {skill.name} of {skill.agent} is added already", context)
    if skill.used:
        raise ElsewhereError(f"Dot reads the skill {skill.name} already", context)
    if skill.refusal is not None:
        raise ElsewhereError(skill.refusal, context)
    source: Path = skill.folder.resolve()
    try:
        refusal: str | None = copy_refusal(source, limit)
    except OSError as error:
        raise ElsewhereError(f"its folder cannot be read ({type(error).__name__})", context) from error
    if refusal is not None:
        raise ElsewhereError(refusal, context)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Copied first into a hidden folder beside the skills, which the catalog does not read, then moved into place.
    staging: Path = Path(tempfile.mkdtemp(prefix=".adding-", dir=folder))
    try:
        copy: Path = _copy(source, staging, skill, limit)
        target: Path = folder / skill.name
        if target.exists() or target.is_symlink():
            raise ElsewhereError(f"Dot's skills folder already holds {skill.name}", context)
        copy.rename(target)
    except OSError as error:
        raise ElsewhereError(f"it cannot be copied ({type(error).__name__})", context) from error
    finally:
        _discard(staging)


def remove_skill(folder: Path, agent: str, name: str) -> None:
    """Delete Dot's copy of the skill ``name`` of ``agent`` from its skills folder ``folder``: only a folder whose
    marker says Dot copied it from that skill, whether or not the agent still has it. Raises ElsewhereError when the
    skill was not added from here."""
    copy: Path = folder / name
    made: SkillCopy | None = copied_from(copy)
    if made is None or (made.agent, made.name) != (agent, name):
        raise ElsewhereError(
            f"the skill {name} of {agent} was not added to Dot from here", {"agent": agent, "skill": name}
        )
    _discard(copy)


def _entry(home: Path, source: ServerFile, name: str) -> McpServerJson:
    """The entry of the server ``name`` in the file of ``source`` under ``home``, as written. Raises ElsewhereError when
    the file cannot be read, or the entry is not there or cannot be read, told without a value."""
    table: object = read_table(home.joinpath(*source.path), source.key, source.language)
    entry: object = table.get(name) if isinstance(table, dict) else None
    if entry is None:
        raise ElsewhereError(f"the MCP server {name} is not there any more", {"agent": source.agent, "server": name})
    try:
        return McpServerJson.model_validate(entry)
    except ValidationError as error:
        raise ElsewhereError(
            f"the MCP server {name} cannot be read as it is written", {"agent": source.agent, "server": name}
        ) from error


def add_server(path: Path, home: Path, server: FoundServer) -> None:
    """Add ``server`` to the record at ``path``, with the fingerprint of its entry as it is now in its agent's file under
    ``home``. Raises ElsewhereError when Dot has it already or cannot use it."""
    context: dict[str, str] = {"agent": server.agent, "server": server.name}
    if server.added:
        raise ElsewhereError(f"the MCP server {server.name} of {server.agent} is added already", context)
    if server.used:
        raise ElsewhereError(f"Dot has the MCP server {server.name} already", context)
    if server.refusal is not None:
        raise ElsewhereError(server.refusal, context)
    source: ServerFile | None = SERVER_FILES.get(server.agent)
    if source is None:
        raise ElsewhereError(f"Dot does not add the MCP servers of {server.agent} one by one", context)
    entry: McpServerJson = _entry(home, source, server.name)
    item: AddedServer = AddedServer(agent=server.agent, name=server.name, entry=entry_fingerprint(entry))
    with _RECORD_LOCK:
        record: AddedServers = read_added_servers(path)
        _write(path, AddedServers(servers=(*record.servers, item)))


def remove_server(path: Path, agent: str, name: str) -> None:
    """Take the server ``name`` of ``agent`` out of the record at ``path``, whether or not the agent still has it.
    Raises ElsewhereError when it was not added from here."""
    with _RECORD_LOCK:
        record: AddedServers = read_added_servers(path)
        if added_server(record.servers, agent, name) is None:
            raise ElsewhereError(
                f"the MCP server {name} of {agent} was not added to Dot from here", {"agent": agent, "server": name}
            )
        _write(path, AddedServers(servers=tuple(i for i in record.servers if (i.agent, i.name) != (agent, name))))


def _added_config(
    home: Path, item: AddedServer, source: ServerFile, taken: frozenset[str]
) -> McpServerConfig | FoundProblem:
    path: Path = home.joinpath(*source.path)
    try:
        entry: McpServerJson = _entry(home, source, item.name)
    except ElsewhereError as error:
        return FoundProblem(path=path, reason=error.message)
    if entry_fingerprint(entry) != item.entry:
        return FoundProblem(path=path, reason=f"the MCP server {item.name} changed after it was added; add it again")
    try:
        return server_config(item.agent, item.name, entry, taken)
    except ElsewhereError as error:
        return FoundProblem(path=path, reason=f"the MCP server {item.name}: {error.message}")


def added_server_configs(
    home: Path, record: AddedServers, taken: frozenset[str]
) -> tuple[tuple[McpServerConfig, ...], tuple[FoundProblem, ...]]:
    """The servers of ``record`` as Dot starts them, read now from their agents' files under ``home``; ``taken`` are
    the names of Dot's other servers. One that is not there any more, that changed after it was added, or that Dot
    cannot use as it is written now, is a problem, and the others are still started."""
    configs: list[McpServerConfig] = []
    problems: list[FoundProblem] = []
    for item in record.servers:
        source: ServerFile | None = SERVER_FILES.get(item.agent)
        if source is None:
            problems.append(
                FoundProblem(path=home, reason=f"Dot does not add the MCP servers of {item.agent} one by one")
            )
            continue
        made: McpServerConfig | FoundProblem = _added_config(
            home, item, source, taken | {config.name for config in configs}
        )
        if isinstance(made, FoundProblem):
            problems.append(made)
            continue
        configs.append(made)
    return tuple(configs), tuple(problems)
