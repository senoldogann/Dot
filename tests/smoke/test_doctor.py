"""The doctor rows that look at this Mac for real: the commands and secrets of MCP servers, and whether macOS can capture
the screen. Nothing is started and nothing is mocked: the login shell, PATH, ``.env`` file, Keychain and ScreenCaptureKit
are all asked."""

import platform
import sys
from pathlib import Path

import pytest

from dotagent.config import McpServerConfig
from dotagent.desktop.capture import rect_capture_supported
from dotagent.doctor import CheckResult, mcp_checks

LOGIN_TIMEOUT_S: float = 15.0
FIRST_MACOS_WITH_RECT_CAPTURE: tuple[int, int] = (15, 2)
UNSET_SECRET: str = "DOT_TEST_SURELY_UNSET_SECRET"


async def test_without_servers_the_mcp_row_says_where_to_add_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    (row,) = await mcp_checks([], LOGIN_TIMEOUT_S)
    assert (row.name, row.ok, row.required) == ("mcp", True, False)
    assert "[[mcp]]" in row.detail


@pytest.fixture
def local(tmp_path: Path) -> Path:
    """An executable ``bin/server`` in the workspace of a throwaway Dot home, where a relative command starts from."""
    server: Path = tmp_path / "workspace" / "bin" / "server"
    server.parent.mkdir(parents=True)
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    return server


async def test_each_server_gets_a_row_that_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local: Path
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    servers: list[McpServerConfig] = [
        McpServerConfig(name="absolute", command=sys.executable),
        McpServerConfig(name="onpath", command="ls"),
        McpServerConfig(name="relative", command="bin/server"),
        McpServerConfig(name="ghost", command="dot-no-such-server-binary"),
        McpServerConfig(name="pathghost", command="/no/such/dir/server"),
        McpServerConfig(name="needy", command="ls", env={"TOKEN": f"${{{UNSET_SECRET}}}"}),
        McpServerConfig(name="sleeping", command="dot-no-such-server-binary", enabled=False),
    ]
    rows: list[CheckResult] = await mcp_checks(servers, LOGIN_TIMEOUT_S)
    assert [row.name for row in rows] == [f"mcp {server.name}" for server in servers]
    absolute, onpath, relative, ghost, pathghost, needy, sleeping = rows

    assert (absolute.ok, absolute.required) == (True, False)
    assert absolute.detail.startswith(sys.executable)
    assert (onpath.ok, onpath.required) == (True, False)
    assert onpath.detail.split(";")[0].endswith("/ls")
    assert (relative.ok, relative.required) == (True, False)
    assert relative.detail.startswith(str(local))

    assert (ghost.ok, ghost.required) == (False, False)
    assert "dot-no-such-server-binary is not installed" in ghost.detail
    assert "Dot starts without its tools while it fails" in ghost.detail
    assert (pathghost.ok, pathghost.required) == (False, False)
    assert "/no/such/dir/server is not installed" in pathghost.detail

    assert (needy.ok, needy.required) == (False, False)
    assert UNSET_SECRET in needy.detail
    assert "dotx auth set" in needy.detail
    assert "not installed" not in needy.detail

    assert (sleeping.ok, sleeping.required) == (True, False)
    assert "switched off" in sleeping.detail


def test_the_screen_capture_check_follows_the_macos_version() -> None:
    numbers: list[int] = [int(part) for part in platform.mac_ver()[0].split(".")]
    assert rect_capture_supported() is (tuple(numbers[:2]) >= FIRST_MACOS_WITH_RECT_CAPTURE)
