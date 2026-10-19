"""Dot's own files a run leaves alone (the outside audit of 2026-10-17, S2): what signing in to an MCP server gave
Dot is guarded as its key file is, and the record of the MCP servers the daemon starts and the home of the OpenCode
server it starts are Dot's own as its plugins are: read freely, changed only by the file tools once the owner agrees
(item 398)."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.paths import connected_servers_path, mcp_sign_ins_dir, opencode_dir
from dotagent.policy.paths import classify_path
from dotagent.runtime import _confined_paths


async def test_the_sign_ins_are_guarded_and_the_servers_and_opencode_are_dots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    protected, dot_paths = await _confined_paths(load_default_config())
    workspace: Path = tmp_path / "workspace"
    assert classify_path(mcp_sign_ins_dir() / "server.json", workspace, protected, dot_paths) == "protected"
    assert classify_path(connected_servers_path(), workspace, protected, dot_paths) == "dot"
    assert classify_path(opencode_dir() / "opencode.json", workspace, protected, dot_paths) == "dot"
