"""Dot's own files a run does not change by itself (the outside audit of 2026-10-17, S2): what signing in to an MCP
server gave Dot is guarded as its key file is, and the record of the MCP servers the daemon starts and the home of the
OpenCode server it starts are Dot's own as its plugins are: read freely, changed only by the file tools once the owner
agrees (item 398)."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.paths import connected_servers_path, mcp_sign_ins_dir, opencode_dir
from dotagent.policy.paths import classify_path
from dotagent.runtime import _confined_paths
from dotagent.store.backup import partial_folder


async def test_the_sign_ins_are_guarded_and_the_servers_and_opencode_are_dots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    protected, dot_paths = await _confined_paths(load_default_config())
    workspace: Path = tmp_path / "workspace"
    assert classify_path(mcp_sign_ins_dir() / "server.json", workspace, protected, dot_paths) == "protected"
    assert classify_path(connected_servers_path(), workspace, protected, dot_paths) == "dot"
    assert classify_path(opencode_dir() / "opencode.json", workspace, protected, dot_paths) == "dot"


async def test_the_copies_of_the_database_and_a_copy_under_way_are_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 526: a copy is the whole database, and one planted in their folder would be what the owner goes back to,
    so a run reads and changes neither, wherever ``[store] backup_dir`` puts them."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    workspace: Path = tmp_path / "workspace"
    protected, dot_paths = await _confined_paths(load_default_config())
    copy: Path = tmp_path / "dot" / "backups" / "dot-20270115-080000.sqlite3.gz"
    assert classify_path(copy, workspace, protected, dot_paths) == "protected"
    assert classify_path(partial_folder() / ".backup-1.partial", workspace, protected, dot_paths) == "protected"
    elsewhere: Path = tmp_path / "disk" / "Dot Backups"
    config = load_default_config()
    chosen = config.model_copy(update={"store": config.store.model_copy(update={"backup_dir": str(elsewhere)})})
    protected, dot_paths = await _confined_paths(chosen)
    assert classify_path(elsewhere / "dot-20270115-080000.sqlite3.gz", workspace, protected, dot_paths) == "protected"
