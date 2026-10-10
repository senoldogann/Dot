"""``dotx doctor``: the health checks of the setup, printed one per line."""

import sys

from dotagent.cli_commands import (
    Command,
    DoctorCommand,
)
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.doctor import CheckResult, run_checks


async def cmd_doctor(command: DoctorCommand) -> int:
    results: list[CheckResult] = await run_checks(load_user_config())
    for result in results:
        mark: str = "ok  " if result.ok else ("FAIL" if result.required else "warn")
        sys.stdout.write(f"{mark}  {result.name:<22} {result.detail}\n")
    return 1 if any(not result.ok and result.required for result in results) else 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case DoctorCommand():
            return await cmd_doctor(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
