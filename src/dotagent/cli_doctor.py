"""``dotx doctor``: the health checks of the setup, printed one per line."""

import sys

from dotagent.cli_commands import (
    Command,
    DoctorCommand,
)
from dotagent.cli_daemon import daemon_left_out
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import DotError, InvalidValueError
from dotagent.doctor import CheckResult, left_out_checks, run_checks
from dotagent.mcp.types import LeftOutTool


def check_line(result: CheckResult) -> str:
    """A check as a line: ``ok``, ``FAIL`` (one that is required) or ``warn``, its name and what it found."""
    mark: str = "ok  " if result.ok else ("FAIL" if result.required else "warn")
    return f"{mark}  {result.name:<22} {result.detail}\n"


def asking_failed(error: DotError) -> CheckResult:
    """The warn row of a running daemon that could not be asked for the tools it left out (item 1001)."""
    fix: object = error.context.get("fix")
    return CheckResult(
        name="mcp tools left out",
        ok=False,
        required=False,
        detail=f"the running daemon could not be asked: {error.message}" + ("" if fix is None else f" ({fix})"),
    )


async def cmd_doctor(command: DoctorCommand) -> int:
    """The checks of the setup, then the tools the running daemon left out of its connected servers (item 1001: the
    checks start no server, so only the daemon knows them); with no daemon running there are none to tell."""
    config: AppConfig = load_user_config()
    results: list[CheckResult] = await run_checks(config)
    for result in results:
        sys.stdout.write(check_line(result))
    # A daemon that cannot be asked is a warn row of its own, with why and its fix: the checks above still stand.
    try:
        left_out: list[LeftOutTool] | None = await daemon_left_out(config)
    except DotError as error:
        sys.stdout.write(check_line(asking_failed(error)))
        left_out = None
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
