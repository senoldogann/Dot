"""The environment the ``claude`` program runs in for Dot (items 1013 and 1017), a run's or a single call's."""

from collections.abc import Mapping
from pathlib import Path

from dotagent.repair.claude import worker_env

# The variables of the program's environment that start so are taken out: Dot's own (the program needs none of them)
# and git's (Self-repair's worker sets some to keep its sessions from GitHub; here the model has no command of its own).
_CLEARED_PREFIXES: tuple[str, ...] = ("DOT_", "GIT_")
# No updater, no telemetry and no error report: the program's only network is Anthropic's API.
# A call of Dot's tool may wait a day (a question the owner answers, an approval, a helper at work: item 1076), which
# Claude Code's own limit on an MCP call would cut short, the model going on without the answer: the limit is a day.
_PROGRAM_ENV: dict[str, str] = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "MCP_TOOL_TIMEOUT": str(24 * 3600 * 1000),
}


def claude_env(env: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """The program's environment: Self-repair's worker's of ``env`` (no API key, no secret-named variable; its folders
    in ``scratch``), without any variable of Dot's or git's own, with the updater, telemetry and error reports off. The
    owner's ``HOME`` stays: the program's sign-in is there."""
    kept: dict[str, str] = {
        name: value for name, value in worker_env(env, scratch).items() if not name.startswith(_CLEARED_PREFIXES)
    }
    kept.update(_PROGRAM_ENV)
    return kept
