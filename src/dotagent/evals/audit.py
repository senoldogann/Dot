"""The structure of the catalog: what can be told about a task without running it."""

import re
from collections.abc import Sequence
from pathlib import Path

from dotagent.core.errors import EvalError
from dotagent.evals.checks import workspace_path
from dotagent.evals.types import AnswerMatches, Check, EvalTask, ScriptedCall
from dotagent.tools.meta import FINISH_TOOL

_ROOT: Path = Path("workspace")


def _path_problem(path: str) -> str | None:
    try:
        workspace_path(_ROOT, path)
    except EvalError:
        return f"the path {path!r} is not inside the workspace"
    return None


def _final_call(task: EvalTask) -> ScriptedCall | None:
    """The last tool call of the reference solution."""
    for turn in reversed(task.reference):
        if turn.calls:
            return turn.calls[-1]
    return None


def _ending_problems(task: EvalTask) -> list[str]:
    final: ScriptedCall | None = _final_call(task)
    if final is None or final.tool != FINISH_TOOL:
        return ["the reference solution does not end with finish"]
    wants_success: bool = task.ending == "done"
    if final.arguments.get("success") is not wants_success:
        return [f"the task ends as {task.ending}, but the reference finishes with success={not wants_success}"]
    return []


def task_problems(task: EvalTask) -> tuple[str, ...]:
    """What is wrong with the way a task is written, found without running anything."""
    problems: list[str] = []
    if task.mode == "chat":
        problems.append("a chat has no finish tool, so a task cannot be one")
    if task.time_limit_s <= 0 or task.token_budget <= 0:
        problems.append("the time limit and the token budget must be positive")
    if not task.checks:
        problems.append("the task has no check")
    seeded: list[str] = [seed.path for seed in task.files]
    if len(set(seeded)) != len(seeded):
        problems.append("a file is seeded twice")
    every_check: list[Check] = [*task.checks, *task.injection_checks]
    checked: list[str] = [check.path for check in every_check if hasattr(check, "path")]
    for path in [*seeded, *checked]:
        problem: str | None = _path_problem(path)
        if problem is not None:
            problems.append(problem)
    for check in every_check:
        if isinstance(check, AnswerMatches):
            try:
                re.compile(check.pattern)
            except re.error as error:
                problems.append(f"the pattern {check.pattern!r} is not a regular expression ({error})")
    problems.extend(_ending_problems(task))
    return tuple(problems)


def catalog_problems(tasks: Sequence[EvalTask]) -> tuple[str, ...]:
    """What is wrong with the catalog as a whole: names that repeat, and the problems of each task."""
    ids: list[str] = [task.id for task in tasks]
    repeated: list[str] = sorted({name for name in ids if ids.count(name) > 1})
    found: list[str] = [f"the task id {name!r} is used twice" for name in repeated]
    for task in tasks:
        found.extend(f"{task.id}: {problem}" for problem in task_problems(task))
    return tuple(found)
