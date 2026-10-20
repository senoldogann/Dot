"""What tells a process that a tool of a Dot run started it (item 441): the shell and terminal tools set it in the
environment of every command (``tools.shell.child_env``), and the commands that would restart the daemon at once
refuse to run under it, since the restart would cut the run itself off."""

from collections.abc import Mapping

RUN_SHELL_MARKER: str = "DOT_RUN_SHELL"


def started_by_a_run(environ: Mapping[str, str]) -> bool:
    """Whether ``environ`` is the environment of a command a run's tool started."""
    return environ.get(RUN_SHELL_MARKER) == "1"
