"""``dotx doctor``: the health checks of the setup, printed one per line."""

import sys

from dotagent.cli_commands import (
    Command,
    DoctorCommand,
)
from dotagent.cli_daemon import daemon_left_out
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.doctor import CheckResult, left_out_checks, run_checks
from dotagent.mcp.types import LeftOutTool


def check_line(result: CheckResult) -> str:
    """A check as a line: ``ok``, ``FAIL`` (one that is required) or ``warn``, its name and what it found."""
    mark: str = "ok  " if result.ok else ("FAIL" if result.required else "warn")
    return f"{mark}  {result.name:<22} {result.detail}\n"


async def cmd_doctor(command: DoctorCommand) -> int:
    """The checks of the setup, then the tools the running daemon left out of its connected servers (item 1001: the
    checks start no server, so only the daemon knows them); with no daemon running there are none to tell."""
    config: AppConfig = load_user_config()
    results: list[CheckResult] = await run_checks(config)
    for result in results:
        sys.stdout.write(check_line(result))
    # Printed first, so that a daemon that cannot be asked leaves the checks on the screen above its error.
    left_out: list[LeftOutTool] | None = await daemon_left_out(config)
    for result in left_out_checks([] if left_out is None else left_out):
        sys.stdout.write(check_line(result))
    return 1 if any(not result.ok and result.required for result in results) else 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case DoctorCommand():
            return await cmd_doctor(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
