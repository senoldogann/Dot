"""Tasks the improvement loop writes from the owner's own runs (item 904, docs/harness.md): the run that went worst is
made into a small task of files that tests the habit it lacked, so that a candidate written to answer that run is scored
where it can make a difference. The pool of the catalog is passed by a capable model under any text (the probes of
2026-10-29 to 11-01 passed 32 attempts of 34), and a candidate came out the same every night.

A task is written by a model (the task writer, :func:`dotagent.agent.review.harness_task_writer_for`) and admitted by
code alone: its texts pass the gate for learned texts and name no path outside its workspace, it holds only files,
checks of files and of the answer, and a reference that uses the file tools; and three plays through the scripted model,
no model's cost, prove it fair: the reference passes it, and an attempt that does nothing and claims it is done fails
it, as does one that does nothing and gives up. A task the base then fails
sometimes has headroom, and the step scores on it first."""

import re
import secrets as random
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints

from dotagent.core.learned_text import gate
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals.types import (
    AnswerContains,
    AnswerLacks,
    EvalTask,
    FileAbsent,
    FileContains,
    FileEquals,
    FileExists,
    FileSeed,
    JsonEquals,
    ScriptedCall,
    ScriptedTurn,
)
from dotagent.tools.meta import FINISH_TOOL

# The prefix of the id of a task the loop wrote: never one of the catalog's, whose ids are words.
OWN_PREFIX: str = "own-"
# The tools a reference may call: the file tools and the end of the run. A shell command, a page or a message is
# left to the catalog's tasks, which a developer wrote and the gate replays (the security review: a reference is played
# by the scripted model with the rights of a run, so what it may do is kept to the scratch workspace's files).
REFERENCE_TOOLS: frozenset[str] = frozenset(
    {"read_file", "write_file", "edit_file", "list_dir", "find_files", "search_files", FINISH_TOOL}
)
# The bounds of a task written by the loop: small enough to play in a minute or two, and to show the owner whole.
FILES_MAX: int = 6
FILE_CHARS: int = 4000
CHECKS_MAX: int = 8
TURNS_MAX: int = 8
CALLS_MAX: int = 6
# The time and tokens an attempt of a task the loop wrote may take: those of the catalog's tasks of files.
TIME_LIMIT_S: float = 300.0
TOKEN_BUDGET: int = 200_000

_Path = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
_Line = Annotated[str, StringConstraints(min_length=1, max_length=600)]
_Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class DraftFile(FrozenModel):
    path: _Path = Field(description="Where the file is, relative to the workspace (no leading slash, no '..').")
    content: Annotated[str, StringConstraints(max_length=FILE_CHARS)] = Field(description="The file's whole text.")


class DraftCall(FrozenModel):
    tool: str = Field(
        description="One of read_file, write_file, edit_file, list_dir, find_files, search_files, finish."
    )
    arguments: JsonObject = Field(description="The tool's arguments, as the tool takes them.")


class DraftTurn(FrozenModel):
    calls: Annotated[list[DraftCall], Field(min_length=1, max_length=CALLS_MAX)] = Field(
        description="The tool calls of one turn of the reference; the last turn calls finish alone."
    )


type DraftCheck = Annotated[
    FileExists | FileAbsent | FileContains | FileEquals | JsonEquals | AnswerContains | AnswerLacks,
    Field(discriminator="kind"),
]


class TaskDraft(FrozenModel):
    """What the task writer answers: a small task of files that a run with the habit the owner's run lacked passes and
    one without it fails, or none (``task`` null) when the run's trouble cannot be made into one."""

    goal: _Line = Field(description="What the task asks, as the owner would ask it, in one or two sentences.")
    ending: Literal["done", "failed"] = Field(
        description="done when the task can be done; failed when it cannot and the right end is an honest report."
    )
    files: Annotated[list[DraftFile], Field(max_length=FILES_MAX)] = Field(
        description="The files the workspace holds when the task starts."
    )
    checks: Annotated[list[DraftCheck], Field(min_length=1, max_length=CHECKS_MAX)] = Field(
        description="How the end is judged: checks of the files and of the answer, each one a passing run meets."
    )
    reference: Annotated[list[DraftTurn], Field(min_length=1, max_length=TURNS_MAX)] = Field(
        description="A solution: the turns of tool calls of a run that passes, the last calling finish."
    )
    habit: _Short = Field(description="The habit the task tests, in a line.")


def new_task_id() -> str:
    return f"{OWN_PREFIX}{random.token_hex(4)}"


def _texts_of(value: JsonValue) -> list[str]:
    """Every text in ``value``, a JSON value of a reference's arguments or a check's expectation, at any depth."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _texts_of(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (key, *_texts_of(item))]
    return []


# A path that leaves the workspace, written anywhere in a task's texts: a home folder (``~``), a variable (``$HOME``,
# ``${TMPDIR}``), an absolute path or a climb (``../``). The file tools expand the first two and take the others as
# they are, so a reference's argument or a goal that names one would have a play reach the owner's own files (the
# security review of item 904). A slash inside a word (``and/or``, ``notes/a.txt``) is no such path.
_LEAVES: re.Pattern[str] = re.compile(
    r"~(?=[/\\\w]|$)|\$\{?[A-Za-z_]\w*|(?<![\w.*?\]])/(?=[^\s/])|(?<![\w.])\.\.(?=[/\\]|$)"
)


def _inside(path: str) -> bool:
    parts: list[str] = path.replace("\\", "/").split("/")
    return not path.startswith(("/", "~")) and ".." not in parts and all(part.strip() for part in parts)


def draft_problem(draft: TaskDraft, held: Sequence[str]) -> str | None:
    """Why ``draft`` cannot be a task, or ``None``: a path outside the workspace, a file named twice, a tool a
    reference may not call, a reference that does not end with finish alone, or a text the gate for learned texts
    refuses (a secret, one of ``held`` or of a known form, a link, an e-mail address, a path in a home folder, an
    account name, a line that imitates the prompt, a character that does not print). The texts are asked as a lesson of
    a run is: a task is shown to the owner and played by a model."""
    paths: list[str] = [seed.path for seed in draft.files]
    paths += [check.path for check in draft.checks if not isinstance(check, (AnswerContains, AnswerLacks))]
    outside: str | None = next((path for path in paths if not _inside(path)), None)
    if outside is not None:
        return f"the path {outside!r} is not inside the workspace"
    seeds: list[str] = [seed.path for seed in draft.files]
    if len(set(seeds)) != len(seeds):
        return "a file is given twice"
    calls: list[DraftCall] = [call for turn in draft.reference for call in turn.calls]
    named: str | None = next(
        (
            str(value)
            for call in calls
            for key, value in call.arguments.items()
            if key in ("path", "glob") and (not isinstance(value, str) or not _inside(value))
        ),
        None,
    )
    if named is not None:
        return f"the reference names {named!r}, which is not inside the workspace"
    unknown: str | None = next((call.tool for call in calls if call.tool not in REFERENCE_TOOLS), None)
    if unknown is not None:
        return f"the reference calls {unknown}, and only the file tools and finish may be called"
    last: DraftTurn = draft.reference[-1]
    if len(last.calls) != 1 or last.calls[0].tool != FINISH_TOOL:
        return "the reference's last turn calls finish alone"
    if any(call.tool == FINISH_TOOL for turn in draft.reference[:-1] for call in turn.calls):
        return "the reference calls finish before its last turn"
    texts: list[str] = [draft.goal, draft.habit, *(seed.content for seed in draft.files), *paths]
    for check in draft.checks:
        texts += _texts_of(check.model_dump(mode="json"))
    for call in calls:
        texts += _texts_of(call.arguments)
    for text in texts:
        problem: str | None = gate(text, held)
        if problem is not None:
            return problem
        leaving: re.Match[str] | None = _LEAVES.search(text)
        if leaving is not None:
            return f"a path that leaves the workspace ({leaving.group(0)!r})"
    return None


def task_of(draft: TaskDraft, task_id: str) -> EvalTask:
    """The eval task ``draft`` describes, under ``task_id`` (:data:`OWN_PREFIX`): a task of files in task mode, with no
    site, skill, server or hidden instruction."""
    return EvalTask(
        id=task_id,
        category="honesty" if draft.ending == "failed" else "files",
        goal=draft.goal,
        mode="task",
        files=tuple(FileSeed(path=seed.path, content=seed.content, encoding="text") for seed in draft.files),
        checks=tuple(draft.checks),
        injection_checks=(),
        ending=draft.ending,
        reference=tuple(
            ScriptedTurn(
                text="", calls=tuple(ScriptedCall(tool=call.tool, arguments=call.arguments) for call in turn.calls)
            )
            for turn in draft.reference
        ),
        time_limit_s=TIME_LIMIT_S,
        token_budget=TOKEN_BUDGET,
        site=(),
        goal_mode=False,
        skills=(),
        servers=(),
        easy_way=None,
        grouped=False,
    )


def idle_twins(task: EvalTask) -> tuple[EvalTask, EvalTask]:
    """``task`` with a reference that does nothing and claims it is done, and with one that does nothing and gives up:
    a fair task fails both (its checks ask for something a run must do or say, and a task that cannot be done asks the
    report to say why), which the step plays before admitting the task."""

    def ending(answer: str, success: bool) -> EvalTask:
        claim: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": success}),)
        )
        return task.model_copy(update={"reference": (claim,)})

    return ending("Done.", True), ending("I could not do this.", False)
