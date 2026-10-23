"""What a script of a run starts has the environment its commands have, not the daemon's (item 555): osascript passes
its own environment to ``do shell script``, so a script of the AppleScript tool could sign with the owner's SSH agent
and print the keys exported in the shell that started Dot."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.config import load_default_config
from dotagent.desktop.script import run_osascript
from dotagent.tools.apple import build_apple_tool
from dotagent.tools.context import ToolContext

SCRIPT_TIMEOUT_S: float = 60.0
# A script that gives back three variables of the environment of the shell it starts.
_ECHO: str = 'do shell script "echo ${DOT_PROBE_TOKEN:-absent} ${SSH_AUTH_SOCK:-absent} ${DOT_PROBE_OPEN:-absent}"'


def _commands_env() -> dict[str, str]:
    """What the shell tool's commands have: no variable whose name looks like a secret."""
    return {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "DOT_PROBE_OPEN": "kept"}


async def test_a_script_starts_what_it_runs_with_the_environment_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOT_PROBE_TOKEN", "leaked")
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent-socket")
    given: str = await run_osascript("AppleScript", _ECHO, (), (), SCRIPT_TIMEOUT_S, _commands_env())
    assert given == "absent absent kept"
    # Dot's own scripts (the mail of a code, a draft) have no environment of the run's to carry: the daemon's.
    assert await run_osascript("AppleScript", _ECHO, (), (), SCRIPT_TIMEOUT_S) == "leaked agent-socket absent"


async def test_the_apple_script_tool_gives_its_script_the_environment_of_a_runs_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOT_PROBE_TOKEN", "leaked")
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent-socket")
    tool = build_apple_tool()
    ctx: ToolContext = cast(
        ToolContext,
        SimpleNamespace(
            runtime=SimpleNamespace(
                config=load_default_config(), shell_env=_commands_env(), protected=(), dot_paths=(), sandbox=None
            ),
            workspace=Path("/nonexistent-workspace"),
            approvals="yolo",
        ),
    )
    prepared = await tool.prepare({"script": _ECHO}, ctx)
    result = await prepared.run(ctx)
    assert "absent absent kept" in result.text
    assert "leaked" not in result.text and "agent-socket" not in result.text
