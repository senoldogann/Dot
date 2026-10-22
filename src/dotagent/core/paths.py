"""Filesystem layout. Everything Dot owns lives under one home directory (``DOT_HOME`` overrides)."""

import os
from pathlib import Path


def dot_home() -> Path:
    override: str | None = os.environ.get("DOT_HOME")
    if override is not None and override != "":
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Dot"


def log_dir() -> Path:
    override: str | None = os.environ.get("DOT_LOG_DIR")
    if override is not None and override != "":
        return Path(override).expanduser()
    return Path.home() / "Library" / "Logs" / "Dot"


def config_path() -> Path:
    return dot_home() / "config.toml"


def env_file_path() -> Path:
    return dot_home() / ".env"


def db_path() -> Path:
    return dot_home() / "dot.sqlite3"


def token_path() -> Path:
    return dot_home() / "api-token"


def vapid_path() -> Path:
    """The daemon's web push key pair (PEM), made on first start."""
    return dot_home() / "vapid-key.pem"


def workspace_dir() -> Path:
    """The agent's working directory: shell commands and file tools default to it."""
    return dot_home() / "workspace"


def screenshots_dir() -> Path:
    return dot_home() / "screenshots"


def downloads_dir() -> Path:
    return dot_home() / "downloads"


def browser_profile_dir() -> Path:
    return dot_home() / "browser-profile"


def browser_incognito_dir() -> Path:
    """The profile of Dot's own Chrome that keeps nothing (item 355): emptied before Chrome starts on it and after it
    closes."""
    return dot_home() / "browser-incognito"


def browser_profiles_dir() -> Path:
    """The profiles of Dot's own Chrome the owner added (item 355), a folder each."""
    return dot_home() / "browser-profiles"


def default_chrome_data_dir() -> Path:
    """Where Google Chrome keeps its profiles on this Mac."""
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def chrome_data_dir() -> Path:
    """Where the Chrome of the user keeps its profiles on this Mac (``DOT_CHROME_DIR`` overrides, so that tests point at
    a scratch Chrome and never at the user's). Dot reads it only when the user asks for it."""
    override: str | None = os.environ.get("DOT_CHROME_DIR")
    if override is not None and override != "":
        return Path(override).expanduser()
    return default_chrome_data_dir()


def evals_dir() -> Path:
    """Where the reports of eval runs are kept: one JSON file per run."""
    return dot_home() / "evals"


def undo_dir() -> Path:
    """Where the journals of the file changes of runs are kept, one folder per run, for ``dotx undo``."""
    return dot_home() / "undo"


def pid_path() -> Path:
    return dot_home() / "daemon.pid"


def running_mark_path() -> Path:
    """The mark a daemon leaves while it runs and removes when it ends without an error (``server/daemon.py``)."""
    return dot_home() / "daemon.running"


def run_owners_dir() -> Path:
    """The lock files of the processes that drive runs (the daemon, ``dotx run``), one per process, each held while its
    process lives (:mod:`dotagent.agent.owners`)."""
    return dot_home() / "run-owners"


def failsafe_path() -> Path:
    """Touching this file aborts every running desktop action (the emergency stop)."""
    return dot_home() / "STOP"


def skills_dir() -> Path:
    """Where the user's skills live: one ``<name>/SKILL.md`` folder per skill."""
    return dot_home() / "skills"


def playbooks_dir() -> Path:
    """Where Dot keeps its learned skills, one ``learned-<kind>/SKILL.md`` folder per kind of work, and the ones the
    owner taught it by showing the task once (item 423), ``taught-<kind>``; the retired learned ones under
    ``.retired``."""
    return dot_home() / "playbooks"


def themes_dir() -> Path:
    """Where the themes of one's own are kept: one ``<id>.json`` theme file per theme, made with the first theme saved
    (item 128)."""
    return dot_home() / "themes"


def plugins_dir() -> Path:
    """Where the user's plugins live: one folder per plugin in the Claude Code layout."""
    return dot_home() / "plugins"


def added_servers_path() -> Path:
    """Dot's record of the MCP servers of other agents that the owner added to it (item 165): each by its agent and its
    name, never what it is started with, which Dot reads from the agent's own file when it starts."""
    return dot_home() / "added-servers.json"


def opencode_command(configured: str) -> str:
    """The ``opencode`` program Dot starts (item 283): ``[providers.opencode] command``, unless ``DOT_OPENCODE`` names
    another, so that tests point at their own and never start the owner's."""
    override: str | None = os.environ.get("DOT_OPENCODE")
    return override if override is not None and override != "" else configured


def opencode_dir() -> Path:
    """The home of the OpenCode server Dot starts (item 283): its config, its data (the sessions of the runs on Zen's
    free models), its state and cache, the chats' sessions and the list of free models; none of the owner's own."""
    return dot_home() / "opencode"


def looks_dir() -> Path:
    """The drawings Quick Look made of the Office files the panel showed (item 268), emptied at the daemon's start."""
    return dot_home() / "looks"


def connected_servers_path() -> Path:
    """Dot's record of the MCP servers the owner added on Customize › Connectors (items 279 and 316): each with its
    address or command."""
    return dot_home() / "connected-servers.json"


def mcp_sign_ins_dir() -> Path:
    """Where Dot keeps what signing in to an MCP server gave it (item 279): one ``<server>.json`` per server, with its
    tokens and the client the server's authorization server registered, readable by the owner alone."""
    return dot_home() / "mcp-sign-ins"


def plugins_data_dir() -> Path:
    """Where a plugin keeps its own files: ``<name>`` below here is its ``${CLAUDE_PLUGIN_DATA}``."""
    return dot_home() / "plugins-data"


def ensure_layout() -> None:
    """Create the private directory tree (mode 0700) if it does not exist yet."""
    for directory in (
        dot_home(),
        log_dir(),
        workspace_dir(),
        screenshots_dir(),
        downloads_dir(),
        browser_profile_dir(),
        skills_dir(),
        plugins_dir(),
    ):
        directory.mkdir(parents=True, exist_ok=True)
    os.chmod(dot_home(), 0o700)
