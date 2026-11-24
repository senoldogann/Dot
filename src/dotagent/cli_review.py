"""``dotx review plan``: the models the pre-push review tries for a change, read from ``[code_review]``
(:mod:`dotagent.code_review`), so that the hook parses no TOML."""

import sys
from pathlib import Path

from dotagent.cli_commands import Command, ReviewPlanCommand
from dotagent.code_review import ReviewPlan, human_paths, plan_text, review_plan, touches_human_path
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError


def cmd_review_plan(command: ReviewPlanCommand) -> int:
    changed: list[str] = [line for line in Path(command.changed).read_text().splitlines() if line != ""]
    try:
        paths: tuple[str, ...] = human_paths(Path(command.guide).read_text())
    except ValueError as exc:
        raise InvalidValueError(str(exc), {"guide": command.guide}) from exc
    plan: ReviewPlan = review_plan(load_user_config().code_review, command.lines, touches_human_path(changed, paths))
    sys.stdout.write(plan_text(plan))
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case ReviewPlanCommand():
            return cmd_review_plan(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
