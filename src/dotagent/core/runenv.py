"""What tells a process that a tool of a Dot run started it (item 441): the shell and terminal tools set it in the
environment of every command (``tools.shell.child_env``), and the commands that would restart the daemon at once
refuse to run under it, since the restart would cut the run itself off."""

from collections.abc import Mapping

RUN_SHELL_MARKER: str = "DOT_RUN_SHELL"
# The id of the run whose tool started the command (item 1310): ``dotx service restart --when-idle`` names it as the
# run that asked, in the job it loads (``RESTART_ASKER_MARKER``).
RUN_ID_MARKER: str = "DOT_RUN_ID"
RESTART_ASKER_MARKER: str = "DOT_RESTART_ASKER"


def started_by_a_run(environ: Mapping[str, str]) -> bool:
    """Whether ``environ`` is the environment of a command a run's tool started."""
    return environ.get(RUN_SHELL_MARKER) == "1"


def asking_run(environ: Mapping[str, str]) -> str | None:
    """The id of the run whose tool started the command whose environment is ``environ``, ``None`` for a command a
    person started."""
    run_id: str = environ.get(RUN_ID_MARKER, "")
    return run_id if started_by_a_run(environ) and run_id != "" else None
