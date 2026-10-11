"""``dotx reflect``: one reflection now (:mod:`dotagent.agent.reflection`), as the daemon does it at
``[agent] reflection_time``, with what it did printed."""

import sys
import time

from dotagent.agent.manager import RunManager
from dotagent.agent.reflection import Reflection, summary_line
from dotagent.cli_commands import Command, ReflectCommand
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.runtime import Runtime, build_runtime, close_runtime


def render_reflection(reflection: Reflection) -> list[str]:
    """The summary line, then where the journal note is."""
    lines: list[str] = [summary_line(reflection)]
    if reflection.journal is not None:
        lines.append(f"Journal note: {reflection.journal} (dotx memory list shows it)")
    else:
        lines.append("Nothing happened since the last reflection, so no journal note was written.")
    return lines


async def cmd_reflect(command: ReflectCommand) -> int:
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        reflection: Reflection = await RunManager(runtime).reflect(time.time())
    finally:
        await close_runtime(runtime)
    sys.stdout.write("\n".join(render_reflection(reflection)) + "\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case ReflectCommand():
            return await cmd_reflect(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
