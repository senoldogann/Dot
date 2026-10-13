"""``dotx eval``: tasks with checks that measure how well Dot finishes work with a given model.

``list`` shows the tasks. ``check`` needs no model: it proves that each task can be passed and cannot be passed by doing
nothing. ``run`` plays the tasks against a model, which spends that model's tokens, and prints the score; ``report``
shows the score of saved runs and compares them.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from dotagent.cli_commands import Command, EvalCheckCommand, EvalListCommand, EvalReportCommand, EvalRunCommand
from dotagent.config import AppConfig, load_default_config, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError
from dotagent.core.paths import evals_dir
from dotagent.evals.audit import catalog_problems, task_problems
from dotagent.evals.catalog import all_tasks, select_tasks
from dotagent.evals.reports import load_report, save_report
from dotagent.evals.results import EvalReport, EvalSettings, TaskOutcome
from dotagent.evals.runner import (
    do_nothing_passes,
    eval_config,
    lookup_secrets,
    play,
    replay_reference,
    required_secret_names,
    secret_names,
)
from dotagent.evals.scoring import (
    Scorecard,
    failure_reason,
    render_comparison,
    render_difference,
    render_scorecard,
    score_report,
)
from dotagent.evals.types import EvalTask
from dotagent.llm.router import parse_model_ref
from dotagent.tools.shell import child_env, read_login_env

_GOAL_PREVIEW_CHARS: int = 70


def _write(lines: Sequence[str]) -> None:
    sys.stdout.write("\n".join(lines) + "\n")


def _preview(text: str, chars: int) -> str:
    one_line: str = " ".join(text.split())
    return one_line if len(one_line) <= chars else f"{one_line[: chars - 1]}…"


def _checks(count: int) -> str:
    return f"{count} check" if count == 1 else f"{count} checks"


def render_task_list(tasks: Sequence[EvalTask]) -> list[str]:
    width: int = max(len(task.id) for task in tasks)
    return [
        f"{task.id:<{width}}  {task.category:<10}  {task.mode:<8}  ends {task.ending:<6}  "
        f"{_checks(len(task.checks) + len(task.injection_checks)):<9}  {_preview(task.goal, _GOAL_PREVIEW_CHARS)}"
        for task in tasks
    ]


async def cmd_eval_list(command: EvalListCommand) -> int:
    _write(render_task_list(all_tasks()))
    return 0


def _reference_problem(outcome: TaskOutcome) -> str:
    return f"the reference solution does not pass: {failure_reason(outcome)}"


async def _task_problems(
    task: EvalTask, config: AppConfig, login_env: dict[str, str], env: dict[str, str]
) -> list[str]:
    """Everything wrong with one task: how it is written, whether its reference passes through the real loop and tools
    and takes the easy way the task names, and whether doing nothing passes."""
    problems: list[str] = list(task_problems(task))
    if problems:
        return problems
    outcome: TaskOutcome = await replay_reference(task, config, login_env)
    if not outcome.passed:
        problems.append(_reference_problem(outcome))
    if task.easy_way is not None and outcome.easy_way_taken is not True:
        problems.append("the reference does not take the easy way the task names")
    if await do_nothing_passes(task, env):
        problems.append("a run that touches nothing and says it is done passes")
    return problems


async def cmd_eval_check(command: EvalCheckCommand) -> int:
    config: AppConfig = eval_config(load_default_config())
    tasks: tuple[EvalTask, ...] = all_tasks()
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    env: dict[str, str] = child_env(login_env, config.shell.pass_env)
    broken: list[str] = list(catalog_problems(tasks))
    sys.stdout.write(f"Checking {len(tasks)} tasks against their reference solutions. No model is called.\n")
    for task in tasks:
        problems: list[str] = await _task_problems(task, config, login_env, env)
        sys.stdout.write(f"  {'ok  ' if not problems else 'FAIL'} {task.id}\n")
        for problem in problems:
            sys.stdout.write(f"       {problem}\n")
        broken.extend(f"{task.id}: {problem}" for problem in problems)
    if broken:
        sys.stdout.write(f"{len(broken)} problems found.\n")
        return 1
    sys.stdout.write("Every task can be passed and cannot be passed by doing nothing.\n")
    return 0


def _progress_line(outcome: TaskOutcome) -> str:
    tokens: int = outcome.metrics.input_tokens + outcome.metrics.output_tokens
    line: str = (
        f"{'PASS' if outcome.passed else 'FAIL'}  {outcome.task_id} #{outcome.attempt}  "
        f"{outcome.metrics.steps} steps, {tokens} tokens, {outcome.seconds:.0f} s"
    )
    return line if outcome.passed else f"{line}\n      {failure_reason(outcome)}"


def _report_of(config: AppConfig, model: str, started_at: float, outcomes: Sequence[TaskOutcome]) -> EvalReport:
    return EvalReport(
        model=model,
        started_at=started_at,
        finished_at=time.time(),
        settings=EvalSettings(
            effort=config.models.effort,
            goal_mode=config.agent.goal_mode,
            context_tokens=config.agent.context_tokens,
            preset=config.policy.preset,
            stall_escalation=config.agent.stall_escalation,
            tier=config.agent.tiers.get(parse_model_ref(model).label, "standard"),
            tool_surface=config.agent.tool_surface,
            verify_answer_paths=config.agent.verify_answer_paths,
        ),
        outcomes=tuple(outcomes),
    )


async def cmd_eval_run(command: EvalRunCommand) -> int:
    config: AppConfig = load_user_config()
    model: str = command.model if command.model is not None else config.models.main
    parse_model_ref(model)
    tasks: tuple[EvalTask, ...] = select_tasks(command.tasks)
    # The review model checks the goal in goal mode and gives the second opinion of the progress watchdog.
    consults: bool = config.agent.goal_mode or config.agent.stall_escalation
    models: list[str] = [model, *([config.models.review] if consults else [])]
    secrets: dict[str, str] = await asyncio.to_thread(lookup_secrets, secret_names(config, models))
    missing: list[str] = [name for name in required_secret_names(config, models) if name not in secrets]
    if missing:
        raise ConfigError(
            "an API key the eval run needs is missing",
            {
                "names": [*missing],
                "hint": "export it, add it to the .env file of the Dot home, or run: dotx auth set NAME",
            },
        )
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    run_config: AppConfig = eval_config(config)
    ceiling: int = command.repeat * sum(task.token_budget for task in tasks)
    longest: float = max(task.time_limit_s for task in tasks)
    sys.stdout.write(
        f"Playing {len(tasks)} task(s), {command.repeat} time(s) each, against {model}. This spends that model's "
        f"tokens: an attempt is cut when it runs longer than {longest:.0f} s or uses more tokens than the budget of "
        f"its task, so the whole run uses at most {ceiling} tokens.\n"
    )
    started_at: float = time.time()
    outcomes: list[TaskOutcome] = []
    async for outcome in play(run_config, login_env, secrets, model, tasks, command.repeat):
        outcomes.append(outcome)
        sys.stdout.write(_progress_line(outcome) + "\n")
        await asyncio.to_thread(save_report, evals_dir(), _report_of(config, model, started_at, outcomes))
    report: EvalReport = _report_of(config, model, started_at, outcomes)
    path: Path = await asyncio.to_thread(save_report, evals_dir(), report)
    _write(["", *render_scorecard(score_report(report)), "", f"Saved: {path}"])
    return 0


def _report_files(files: Sequence[str]) -> list[Path]:
    if files:
        return [Path(name).expanduser() for name in files]
    return sorted(evals_dir().glob("*.json"))


async def cmd_eval_report(command: EvalReportCommand) -> int:
    paths: list[Path] = _report_files(command.files)
    if not paths:
        raise ConfigError("there is no saved eval run yet", {"folder": str(evals_dir())})
    cards: list[Scorecard] = [score_report(await asyncio.to_thread(load_report, path)) for path in paths]
    if command.files:
        for card in cards:
            _write([*render_scorecard(card), ""])
    if len(cards) > 1 or not command.files:
        _write(render_comparison(cards))
    if len(cards) == 2:
        # Two reports: the second against the first, the way a change is judged (the same model before and after).
        _write(["", *render_difference(cards[0], cards[1])])
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case EvalListCommand():
            return await cmd_eval_list(command)
        case EvalCheckCommand():
            return await cmd_eval_check(command)
        case EvalRunCommand():
            return await cmd_eval_run(command)
        case EvalReportCommand():
            return await cmd_eval_report(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
