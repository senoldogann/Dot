"""The versions of the ``claude`` program Dot knows (item 1673): read from ``--version``, judged by the table, a run
refused on a broken one and an unmeasured one logged once. The ``claude`` here is a stand-in script; no model is
called."""

import os
import stat
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.claude_code.versions import (
    CLAUDE_VERSIONS,
    VersionRange,
    claude_version,
    parse_version,
    refusal,
    verdict_of,
)
from dotagent.config import load_default_config
from dotagent.core.errors import ClaudeCodeBrokenVersion
from dotagent.llm import claude_code
from dotagent.llm.claude_code import ClaudeCodeProvider
from dotagent.opencode.bridge import ToolBridge
from dotagent.repair.claude import ClaudeAccount

BROKEN: VersionRange = VersionRange(low=(2, 1, 400), high=(2, 1, 402), support="broken", why="its stream drops results")


def test_the_version_is_read_from_what_the_program_says() -> None:
    assert parse_version("2.1.331 (Claude Code)") == (2, 1, 331)
    assert parse_version("Claude Code") is None


def test_the_table_says_how_dot_fares_on_a_version() -> None:
    assert verdict_of((2, 1, 331), CLAUDE_VERSIONS).support == "supported"
    assert verdict_of((2, 1, 330), CLAUDE_VERSIONS).support == "supported"
    assert verdict_of((2, 1, 100), CLAUDE_VERSIONS).support == "graceful"
    assert verdict_of((3, 0, 0), CLAUDE_VERSIONS).support == "unknown"
    assert verdict_of(None, CLAUDE_VERSIONS).support == "unknown"
    broken = verdict_of((2, 1, 401), (BROKEN, *CLAUDE_VERSIONS))
    assert broken.support == "broken"
    assert refusal(broken).startswith("Claude Code 2.1.401 is known not to work with Dot: its stream drops results.")
    assert verdict_of((2, 1, 402), (BROKEN, *CLAUDE_VERSIONS)).support == "supported"


def _program(folder: Path, said: str) -> Path:
    program: Path = folder / "claude"
    program.write_text(f"#!/bin/sh\necho '{said}'\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return program


def _updated(program: Path) -> Path:
    """A self-update leaves a new file: a later time of change, which is asked again."""
    later: float = program.stat().st_mtime + 10.0
    os.utime(program, (later, later))
    return program


@pytest.mark.asyncio
async def test_a_run_on_a_broken_version_is_refused_and_an_unmeasured_one_is_logged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program: Path = _program(tmp_path, "2.1.401 (Claude Code)")
    assert await claude_version(program, dict(os.environ)) == "2.1.401 (Claude Code)"
    provider: ClaudeCodeProvider = ClaudeCodeProvider(
        load_default_config().providers.claude_code, {"PATH": str(tmp_path)}, ToolBridge()
    )

    async def signed_in() -> ClaudeAccount:
        return ClaudeAccount(email="owner@example.com", organization="Owner")

    monkeypatch.setattr(provider, "account", signed_in)
    monkeypatch.setattr(claude_code, "CLAUDE_VERSIONS", (BROKEN, *CLAUDE_VERSIONS))
    with pytest.raises(ClaudeCodeBrokenVersion, match=r"2\.1\.401 is known not to work"):
        await provider.program()
    monkeypatch.setattr(claude_code, "CLAUDE_VERSIONS", CLAUDE_VERSIONS)
    unmeasured: Path = _updated(_program(tmp_path, "3.0.0 (Claude Code)"))
    with capture_logs() as logs:
        assert await provider.program() == unmeasured
        assert await provider.program() == unmeasured
    assert [entry["version"] for entry in logs if entry["event"] == "claude_code_version_not_measured"] == ["3.0.0"]
