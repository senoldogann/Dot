"""Value types of plugins: the manifest files as Claude Code writes them, and a plugin once read."""

from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from dotagent.config import McpServerConfig
from dotagent.core.types import FrozenModel

MANIFEST_DIR: str = ".claude-plugin"
MANIFEST_FILE: str = "plugin.json"
MCP_FILE: str = ".mcp.json"
SKILLS_DIR: str = "skills"

type JsonTransport = Literal["stdio", "http", "sse", "streamable-http"]


class McpServerJson(FrozenModel):
    """One entry of ``mcpServers``: a command with ``args`` and ``env``, or a ``url`` with ``headers``; ``type`` names
    the transport. Keys Dot does not use are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    transport: JsonTransport | None = Field(default=None, alias="type")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class McpServersFile(FrozenModel):
    """The ``.mcp.json`` of a plugin: an object with ``mcpServers`` in it, as Claude Code requires."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    mcp_servers: dict[str, McpServerJson] = Field(alias="mcpServers")


class PluginManifest(FrozenModel):
    """``.claude-plugin/plugin.json``: only ``name`` is required. ``skills`` adds skill directories (one path or a
    list) to ``skills/``; ``mcpServers`` adds servers (an object, or the path of a file like ``.mcp.json``) to those of
    ``.mcp.json``. Hooks, commands, agents and the descriptive keys are not read."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    version: str | None = None
    skills: str | list[str] = Field(default_factory=list)
    mcp_servers: str | dict[str, McpServerJson] = Field(default_factory=dict, alias="mcpServers")


class Plugin(FrozenModel):
    """A plugin once read: the skill directories of it that exist, and its MCP servers as Dot configures them
    (named ``<plugin>-<server>``)."""

    name: str
    root: Path
    description: str | None
    version: str | None
    skill_dirs: tuple[Path, ...]
    servers: tuple[McpServerConfig, ...]


class PluginProblem(FrozenModel):
    """A folder or an entry taken for a plugin, or part of one, that cannot be used, and why."""

    path: Path
    reason: str


class PluginCatalog(FrozenModel):
    """The usable plugins, each name once, and what was left out."""

    plugins: tuple[Plugin, ...]
    problems: tuple[PluginProblem, ...]

    def servers(self) -> tuple[McpServerConfig, ...]:
        return tuple(server for plugin in self.plugins for server in plugin.servers)
