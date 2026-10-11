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


def chrome_data_dir() -> Path:
    """Where the Chrome of the user keeps its profiles on this Mac (``DOT_CHROME_DIR`` overrides, so that tests point at
    a scratch Chrome and never at the user's). Dot reads it only when the user asks for it."""
    override: str | None = os.environ.get("DOT_CHROME_DIR")
    if override is not None and override != "":
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def evals_dir() -> Path:
    """Where the reports of eval runs are kept: one JSON file per run."""
    return dot_home() / "evals"


def undo_dir() -> Path:
    """Where the journals of the file changes of runs are kept, one folder per run, for ``dotx undo``."""
    return dot_home() / "undo"


def pid_path() -> Path:
    return dot_home() / "daemon.pid"


def failsafe_path() -> Path:
    """Touching this file aborts every running desktop action (the emergency stop)."""
    return dot_home() / "STOP"


def skills_dir() -> Path:
    """Where the user's skills live: one ``<name>/SKILL.md`` folder per skill."""
    return dot_home() / "skills"


def playbooks_dir() -> Path:
    """Where Dot keeps the skills it learned from its own runs that went well: one ``learned-<kind>/SKILL.md`` folder
    per kind of work, the retired ones under ``.retired``."""
    return dot_home() / "playbooks"


def plugins_dir() -> Path:
    """Where the user's plugins live: one folder per plugin in the Claude Code layout."""
    return dot_home() / "plugins"


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
