"""Judging the end of an eval run: each check is looked at once the run is over, in the workspace it left behind."""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue

from dotagent.core.errors import EvalError
from dotagent.core.text import fold_search, truncate_middle
from dotagent.evals.results import CheckOutcome
from dotagent.evals.types import (
    AnswerContains,
    AnswerMatches,
    Check,
    CommandSucceeds,
    FileAbsent,
    FileContains,
    FileEquals,
    FileExists,
    JsonEquals,
)

# How much of what was found is kept in the outcome of a check that did not hold.
_SHOWN_CHARS: int = 300
_PATH_PREVIEW_CHARS: int = 80


def workspace_path(workspace: Path, relative: str) -> Path:
    """A path of a task inside ``workspace``. One that is absolute or climbs out of it is a mistake of the catalog."""
    path: Path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise EvalError("a path of an eval task must stay inside the workspace", {"path": relative})
    return workspace / path


def describe_check(check: Check) -> str:
    """One line that says what the check asks for."""
    match check:
        case FileExists():
            return f"{check.path} exists"
        case FileAbsent():
            return f"{check.path} does not exist"
        case FileContains():
            return f"{check.path} contains {truncate_middle(check.text, _PATH_PREVIEW_CHARS)!r}"
        case FileEquals():
            return f"{check.path} holds exactly {truncate_middle(check.text, _PATH_PREVIEW_CHARS)!r}"
        case JsonEquals():
            return f"{check.path} is the JSON {truncate_middle(_dump(check.expected), _PATH_PREVIEW_CHARS)}"
        case CommandSucceeds():
            return f"`{' '.join(check.argv)}` exits with 0"
        case AnswerContains():
            return f"the answer contains {check.text!r}"
        case AnswerMatches():
            return f"the answer matches /{check.pattern}/"


def _dump(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _outcome(check: Check, passed: bool, detail: str) -> CheckOutcome:
    return CheckOutcome(check=describe_check(check), passed=passed, detail="" if passed else detail)


def _read(path: Path) -> str | None:
    """The text of a file, or ``None`` when there is no such file. Blocking."""
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _shown(text: str) -> str:
    return truncate_middle(text, _SHOWN_CHARS)


def _judge_file(
    check: FileExists | FileAbsent | FileContains | FileEquals | JsonEquals, workspace: Path
) -> CheckOutcome:
    path: Path = workspace_path(workspace, check.path)
    found: str | None = _read(path)
    match check:
        case FileExists():
            return _outcome(check, path.is_file(), "there is no such file")
        case FileAbsent():
            return _outcome(check, not path.exists(), "it exists")
        case FileContains():
            if found is None:
                return _outcome(check, False, "there is no such file")
            return _outcome(check, check.text in found, f"the file holds: {_shown(found)!r}")
        case FileEquals():
            if found is None:
                return _outcome(check, False, "there is no such file")
            same: bool = found.rstrip("\r\n") == check.text.rstrip("\r\n")
            return _outcome(check, same, f"the file holds: {_shown(found)!r}")
        case JsonEquals():
            if found is None:
                return _outcome(check, False, "there is no such file")
            try:
                parsed: JsonValue = json.loads(found)
            except json.JSONDecodeError as error:
                return _outcome(check, False, f"the file is not valid JSON ({error}): {_shown(found)!r}")
            return _outcome(check, parsed == check.expected, f"the file holds: {_shown(_dump(parsed))}")


def _judge_answer(check: AnswerContains | AnswerMatches, answer: str) -> CheckOutcome:
    match check:
        case AnswerContains():
            return _outcome(check, fold_search(check.text) in fold_search(answer), f"the answer is: {_shown(answer)!r}")
        case AnswerMatches():
            try:
                found: bool = re.search(check.pattern, answer, re.IGNORECASE) is not None
            except re.error as error:
                raise EvalError(
                    "the pattern of an eval check is not a regular expression",
                    {"pattern": check.pattern, "error": str(error)},
                ) from error
            return _outcome(check, found, f"the answer is: {_shown(answer)!r}")


async def _judge_command(check: CommandSucceeds, workspace: Path, env: Mapping[str, str]) -> CheckOutcome:
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *check.argv,
            cwd=workspace,
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return _outcome(check, False, f"the command could not be started: {error}")
    try:
        async with asyncio.timeout(check.timeout_s):
            output, _ = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        return _outcome(check, False, f"the command did not end within {check.timeout_s:g} s")
    printed: str = output.decode("utf-8", errors="replace")[-_SHOWN_CHARS:]
    return _outcome(check, process.returncode == 0, f"exit code {process.returncode}, output ends: {printed!r}")


async def evaluate_check(check: Check, workspace: Path, answer: str, env: Mapping[str, str]) -> CheckOutcome:
    """Whether one check holds for the workspace a run left and the answer it gave. ``env`` is the environment of the
    commands of the checks."""
    match check:
        case CommandSucceeds():
            return await _judge_command(check, workspace, env)
        case AnswerContains() | AnswerMatches():
            return _judge_answer(check, answer)
        case FileExists() | FileAbsent() | FileContains() | FileEquals() | JsonEquals():
            return await asyncio.to_thread(_judge_file, check, workspace)


async def evaluate_checks(
    checks: Sequence[Check], workspace: Path, answer: str, env: Mapping[str, str]
) -> tuple[CheckOutcome, ...]:
    """Every check of a task, in order. None is skipped when an earlier one fails, so a report shows all that is wrong."""
    return tuple([await evaluate_check(check, workspace, answer, env) for check in checks])
