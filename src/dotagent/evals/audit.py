"""The structure of the catalog: what can be told about a task without running it."""

import re
from collections.abc import Sequence
from pathlib import Path

from dotagent.agent.review import GOAL_TOOL
from dotagent.core.errors import EvalError
from dotagent.core.text import fold_search
from dotagent.evals.checks import workspace_path
from dotagent.evals.types import AnswerLacks, AnswerMatches, Check, EvalTask, FileSeed, ScriptedCall
from dotagent.mcp.naming import PREFIX
from dotagent.skills.catalog import builtin_skill_names
from dotagent.skills.types import SKILL_TOOL
from dotagent.tools.meta import FINISH_TOOL

_ROOT: Path = Path("workspace")


def _path_problem(path: str) -> str | None:
    try:
        workspace_path(_ROOT, path)
    except EvalError:
        return f"the path {path!r} is not inside the workspace"
    return None


def _final_call(task: EvalTask) -> ScriptedCall | None:
    """The last tool call of the reference solution, leaving out the verdicts of the goal check that follow the finish
    of a goal-mode task (the scripted model plays the review model there too)."""
    for turn in reversed(task.reference):
        calls: list[ScriptedCall] = [call for call in turn.calls if call.tool != GOAL_TOOL]
        if calls:
            return calls[-1]
    return None


def _ending_problems(task: EvalTask) -> list[str]:
    final: ScriptedCall | None = _final_call(task)
    if final is None or final.tool != FINISH_TOOL:
        return ["the reference solution does not end with finish"]
    wants_success: bool = task.ending == "done"
    if final.arguments.get("success") is not wants_success:
        return [f"the task ends as {task.ending}, but the reference finishes with success={not wants_success}"]
    return []


def _easy_way_problems(task: EvalTask) -> list[str]:
    """The easy way a task names has to be one that its own help offers: a skill it brings or one that comes with Dot
    (item 196), or a tool of an MCP server it brings."""
    if task.easy_way is None:
        return []
    tool: str = task.easy_way.tool
    skill: str | None = task.easy_way.skill
    if tool == SKILL_TOOL:
        if skill is None:
            return ["the easy way reads a skill but does not name it"]
        if skill not in {seed.name for seed in task.skills} | builtin_skill_names():
            return [f"the easy way reads the skill {skill!r}, which the task does not bring and Dot does not come with"]
        return []
    if skill is not None:
        return [f"the easy way names the skill {skill!r}, but its tool is not {SKILL_TOOL}"]
    if tool.startswith(PREFIX) and not any(tool.startswith(f"{PREFIX}{server.name}__") for server in task.servers):
        return [f"the easy way's tool {tool!r} is not one of an MCP server the task brings"]
    return []


def _quoting_problems(task: EvalTask) -> list[str]:
    """An instruction hidden in a task is followed by doing what it asks, never by quoting it: no injection check that
    the answer lacks some words may name words that the files the goal names hold, as an answer that quotes them while
    refusing what they ask would fail it."""
    named: list[FileSeed] = [seed for seed in task.files if seed.encoding == "text" and seed.path in task.goal]
    return [
        f"an answer that quotes {seed.path}, which the goal names, holds {check.text!r}, so quoting the hidden "
        "instruction would count as following it"
        for check in task.injection_checks
        if isinstance(check, AnswerLacks)
        for seed in named
        if fold_search(check.text) in fold_search(seed.content)
    ]


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
    brought: list[str] = [skill.name for skill in task.skills]
    if len(set(brought)) != len(brought):
        problems.append("a skill is brought twice")
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
    problems.extend(_quoting_problems(task))
    problems.extend(_easy_way_problems(task))
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
