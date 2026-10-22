"""The catalog of plugins: every plugins directory read, the usable plugins by name, and what was left out and why."""

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

from dotagent.config import McpServerConfig, PluginsConfig
from dotagent.core.errors import PluginError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.paths import plugins_data_dir, plugins_dir, workspace_dir
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.plugins.manifest import PluginVariables, parse_manifest, parse_servers_file, to_server_config
from dotagent.plugins.types import (
    MANIFEST_DIR,
    MANIFEST_FILE,
    MCP_FILE,
    SKILLS_DIR,
    McpServerJson,
    Plugin,
    PluginCatalog,
    PluginManifest,
    PluginProblem,
)

_DATA_VARIABLE: str = "${CLAUDE_PLUGIN_DATA}"


class PluginReading(FrozenModel):
    """One plugin read from disk, with the parts of it that had to be left out."""

    plugin: Plugin
    problems: tuple[PluginProblem, ...]


def plugin_roots(config: PluginsConfig) -> tuple[Path, ...]:
    """``<home>/plugins``, then the ``[plugins] dirs`` entries."""
    return (plugins_dir(), *(Path(entry).expanduser() for entry in config.dirs))


def _reason(error: PluginError) -> str:
    details: JsonObject = {key: value for key, value in error.context.items() if key != "path"}
    return error.message if not details else f"{error.message} ({dumps_compact(details)})"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PluginError("a plugin file cannot be read as UTF-8 text", {"path": str(path), "error": str(exc)}) from exc


def _is_plugin(folder: Path) -> bool:
    return (folder / MANIFEST_DIR / MANIFEST_FILE).is_file()


def _plugin_folders(root: Path) -> list[Path]:
    """``root`` itself when it is a plugin, else its visible child folders, each of which must be one."""
    if _is_plugin(root):
        return [root]
    try:
        return sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith("."))
    except OSError as exc:
        raise PluginError("a plugins directory cannot be read", {"path": str(root), "error": str(exc)}) from exc


def _listed(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def _skill_dirs(folder: Path, manifest: PluginManifest) -> tuple[tuple[Path, ...], tuple[PluginProblem, ...]]:
    """``skills/`` when it exists, plus the directories the manifest names, which must exist."""
    found: list[Path] = []
    problems: list[PluginProblem] = []
    default: Path = folder / SKILLS_DIR
    if default.is_dir():
        found.append(default)
    for entry in _listed(manifest.skills):
        named: Path = folder / entry
        if named.is_dir():
            found.append(named)
        else:
            problems.append(PluginProblem(path=named, reason="a skills directory named in plugin.json does not exist"))
    return tuple(found), tuple(problems)


def _server_entries(folder: Path, manifest: PluginManifest) -> dict[str, tuple[McpServerJson, Path]]:
    """The servers of ``.mcp.json`` and those of the manifest, each with the file it came from."""
    entries: dict[str, tuple[McpServerJson, Path]] = {}
    mcp_file: Path = folder / MCP_FILE
    if mcp_file.is_file():
        for name, entry in parse_servers_file(mcp_file, _read_text(mcp_file)).mcp_servers.items():
            entries[name] = (entry, mcp_file)
    manifest_path: Path = folder / MANIFEST_DIR / MANIFEST_FILE
    inline: str | dict[str, McpServerJson] = manifest.mcp_servers
    if isinstance(inline, str):
        named: Path = folder / inline
        for name, entry in parse_servers_file(named, _read_text(named)).mcp_servers.items():
            entries.setdefault(name, (entry, named))
    else:
        for name, entry in inline.items():
            entries.setdefault(name, (entry, manifest_path))
    return entries


def _written_values(entry: McpServerJson) -> list[str]:
    command: list[str] = [] if entry.command is None else [entry.command]
    url: list[str] = [] if entry.url is None else [entry.url]
    return [*command, *entry.args, *entry.env.values(), *url, *entry.headers.values()]


def _data_dir(plugin: str, entries: dict[str, tuple[McpServerJson, Path]]) -> Path:
    """``${CLAUDE_PLUGIN_DATA}`` of the plugin, created when one of its servers refers to it."""
    data: Path = plugins_data_dir() / plugin
    if any(_DATA_VARIABLE in value for entry, _ in entries.values() for value in _written_values(entry)):
        data.mkdir(parents=True, exist_ok=True)
    return data


def read_plugin(folder: Path) -> PluginReading:
    """The plugin in ``folder``. A server that cannot be configured or a skills directory that is missing is reported
    and left out; the rest of the plugin is used."""
    manifest_path: Path = folder / MANIFEST_DIR / MANIFEST_FILE
    if not manifest_path.is_file():
        raise PluginError("the folder has no .claude-plugin/plugin.json, so it is not a plugin", {"path": str(folder)})
    manifest: PluginManifest = parse_manifest(manifest_path, _read_text(manifest_path))
    entries: dict[str, tuple[McpServerJson, Path]] = _server_entries(folder, manifest)
    variables: PluginVariables = PluginVariables(
        root=folder, data=_data_dir(manifest.name, entries), project=workspace_dir()
    )
    skill_dirs, problems = _skill_dirs(folder, manifest)
    servers: list[McpServerConfig] = []
    reported: list[PluginProblem] = list(problems)
    for name, (entry, path) in entries.items():
        try:
            servers.append(to_server_config(manifest.name, name, entry, variables, path))
        except PluginError as exc:
            reported.append(PluginProblem(path=path, reason=_reason(exc)))
    plugin: Plugin = Plugin(
        name=manifest.name,
        root=folder,
        description=manifest.description,
        version=manifest.version,
        skill_dirs=skill_dirs,
        servers=tuple(servers),
    )
    return PluginReading(plugin=plugin, problems=tuple(reported))


def _without_taken_servers(reading: PluginReading, taken: dict[str, str]) -> tuple[Plugin, tuple[PluginProblem, ...]]:
    """The plugin without the servers whose name another server already has; ``taken`` maps names to their owners."""
    kept: list[McpServerConfig] = []
    problems: list[PluginProblem] = []
    for server in reading.plugin.servers:
        owner: str | None = taken.get(server.name)
        if owner is not None:
            problems.append(
                PluginProblem(
                    path=reading.plugin.root,
                    reason=f"its MCP server {server.name} has the name of a server of {owner}, so it is left out",
                )
            )
            continue
        taken[server.name] = f"plugin {reading.plugin.name}"
        kept.append(server)
    return reading.plugin.model_copy(update={"servers": tuple(kept)}), tuple(problems)


def load_plugins(config: PluginsConfig, configured_servers: Sequence[McpServerConfig]) -> PluginCatalog:
    """Read every plugins directory. A folder that is not a plugin, a plugin name used twice, or a server whose name
    a ``[[mcp]]`` table or another plugin already uses is reported and left out; the other plugins still load."""
    plugins: dict[str, Plugin] = {}
    problems: list[PluginProblem] = []
    taken: dict[str, str] = {server.name: "config.toml" for server in configured_servers}
    seen: set[Path] = set()
    for root in plugin_roots(config):
        if not root.is_dir():
            problems.append(PluginProblem(path=root, reason="the plugins directory does not exist"))
            continue
        try:
            folders: list[Path] = _plugin_folders(root)
        except PluginError as exc:
            problems.append(PluginProblem(path=root, reason=_reason(exc)))
            continue
        for folder in folders:
            real: Path = folder.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                reading: PluginReading = read_plugin(folder)
            except PluginError as exc:
                problems.append(PluginProblem(path=folder, reason=_reason(exc)))
                continue
            if reading.plugin.name in plugins:
                other: Path = plugins[reading.plugin.name].root
                problems.append(
                    PluginProblem(
                        path=folder, reason=f"another plugin is already called {reading.plugin.name}: {other}"
                    )
                )
                continue
            problems.extend(reading.problems)
            plugin, clashes = _without_taken_servers(reading, taken)
            problems.extend(clashes)
            plugins[plugin.name] = plugin
    return PluginCatalog(plugins=tuple(plugins.values()), problems=tuple(problems))


class PluginShelf:
    """The plugins the runs use now (item 542): those Dot read as it started, read again from disk when a plugin is
    installed from the gallery or removed, so that the runs that start after are offered its skills without a restart.
    A plugin's MCP servers start with the daemon only: a plugin read again keeps the servers it had at the start, and
    one that came since has none until Dot starts again. A plugin whose folder went without Dot while servers it
    started run stays on the shelf, so that Remove plugin can still stop them (item 497); Remove plugin then lets go of
    it (:meth:`forget`)."""

    def __init__(self, catalog: PluginCatalog) -> None:
        self._started: dict[str, tuple[McpServerConfig, ...]] = {
            plugin.name: plugin.servers for plugin in catalog.plugins
        }
        self._catalog: PluginCatalog = catalog
        # One reading at a time: one that began before a plugin went to the Trash must not end after the reading that
        # began once it was there, or the plugin would come back on the shelf.
        self._reading: asyncio.Lock = asyncio.Lock()

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        return self._catalog.plugins

    @property
    def problems(self) -> tuple[PluginProblem, ...]:
        return self._catalog.problems

    def servers(self) -> tuple[McpServerConfig, ...]:
        return self._catalog.servers()

    def take(self, catalog: PluginCatalog) -> None:
        """Use ``catalog``, read anew, with the servers each plugin had when Dot started, and the plugins it no longer
        holds whose servers started with Dot and have not been stopped by Remove plugin."""
        read: tuple[Plugin, ...] = tuple(
            plugin.model_copy(update={"servers": self._started.get(plugin.name, ())}) for plugin in catalog.plugins
        )
        names: set[str] = {plugin.name for plugin in read}
        gone: tuple[Plugin, ...] = tuple(
            plugin for plugin in self._catalog.plugins if plugin.name not in names and self._started.get(plugin.name)
        )
        self._catalog = PluginCatalog(plugins=read + gone, problems=catalog.problems)

    async def refill(self, read: Callable[[], PluginCatalog]) -> None:
        """Take what ``read`` finds on disk (in a thread), after any reading under way: the last reading to begin is
        the one the shelf keeps."""
        async with self._reading:
            self.take(await asyncio.to_thread(read))

    def forget(self, name: str) -> None:
        """Let go of the servers the plugin ``name`` started with, once Remove plugin stopped them: a plugin whose
        folder went leaves the shelf at the next reading, and one of its name read later has no servers until Dot
        starts again."""
        self._started.pop(name, None)


async def read_again(shelf: PluginShelf, config: PluginsConfig, configured_servers: Sequence[McpServerConfig]) -> None:
    """Read every plugins directory again into ``shelf``, after any reading under way (:meth:`PluginShelf.refill`)."""
    await shelf.refill(lambda: load_plugins(config, configured_servers))
