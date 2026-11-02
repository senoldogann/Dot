"""A run whose tools run no command (an agent the owner gave no shell and no terminal) is told so in its system prompt
(item 936): the prompt's ways of working name the shell first, and three runs of agents without one called `shell`,
which their tool list did not have, and lost a step each."""

from pathlib import Path

import pytest

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.config import PromptStyle
from dotagent.harness.components import NO_HARNESS

_HEADING: str = "# No commands in this run"


def _prompt(tools: list[str], style: PromptStyle) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "team",
            "aggressive",
            "yolo",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            tools,
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            None,
            style,
            NO_HARNESS,
        )
    )


@pytest.mark.parametrize("style", ["full", "lean"])
def test_a_run_without_shell_or_terminal_is_told_no_command_runs(style: PromptStyle) -> None:
    told: str = _prompt(["web_fetch", "browser_open", "read_file", "finish"], style)
    assert _HEADING in told
    assert "no shell and no terminal" in told
    # Said after the ways of working that name the shell, so that it is read as their exception.
    assert told.index(_HEADING) > told.index("shell")


@pytest.mark.parametrize("tools", [["shell", "web_fetch"], ["terminal_open", "terminal_send", "web_fetch"]])
def test_a_run_that_can_run_a_command_is_not_told_otherwise(tools: list[str]) -> None:
    assert _HEADING not in _prompt(tools, "full")
