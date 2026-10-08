"""Eval tasks as data: what the workspace holds at the start, what the agent is asked and how the end is judged.

A task holds no code. Its checks are values of a closed set of types, so a catalog entry can be listed, saved and
compared like any other record, and its reference solution is a script of tool calls that the offline check replays
against the real loop and the real tools.
"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.models import RunMode

type ExpectedEnding = Literal["done", "failed"]
type TaskCategory = Literal["files", "shell", "coding", "recovery", "multi_step", "research", "honesty", "safety"]


class FileSeed(FrozenModel):
    """A file the workspace holds when the task starts. ``path`` is relative to the workspace."""

    path: str
    content: str


class FileExists(FrozenModel):
    kind: Literal["file_exists"]
    path: str


class FileAbsent(FrozenModel):
    kind: Literal["file_absent"]
    path: str


class FileContains(FrozenModel):
    kind: Literal["file_contains"]
    path: str
    text: str


class FileEquals(FrozenModel):
    """The file holds exactly ``text``; one newline more or less at its end does not matter."""

    kind: Literal["file_equals"]
    path: str
    text: str


class JsonEquals(FrozenModel):
    """The file is JSON and equals ``expected``; the order of keys and the spacing do not matter."""

    kind: Literal["json_equals"]
    path: str
    expected: JsonValue


class CommandSucceeds(FrozenModel):
    """The command exits with 0 when it runs in the workspace after the run. ``argv`` is started directly, without a
    shell."""

    kind: Literal["command_succeeds"]
    argv: tuple[str, ...]
    timeout_s: float


class AnswerContains(FrozenModel):
    """The final answer holds ``text``; case, accents and the Turkish dotted and dotless i do not matter."""

    kind: Literal["answer_contains"]
    text: str


class AnswerMatches(FrozenModel):
    """A regular expression is found in the final answer, whatever the case."""

    kind: Literal["answer_matches"]
    pattern: str


type Check = Annotated[
    FileExists | FileAbsent | FileContains | FileEquals | JsonEquals | CommandSucceeds | AnswerContains | AnswerMatches,
    Field(discriminator="kind"),
]


class ScriptedCall(FrozenModel):
    """One tool call of a scripted reply."""

    tool: str
    arguments: JsonObject


class ScriptedTurn(FrozenModel):
    """One reply of a scripted model: some text and the tool calls it makes."""

    text: str
    calls: tuple[ScriptedCall, ...]


class EvalTask(FrozenModel):
    """One task: the workspace it starts from, the goal, how the end is judged, and a reference solution.

    ``ending`` is how the run has to end: ``done`` for a task that can be done, ``failed`` for one that cannot, where
    the right answer is an honest report. A run passes when it ends that way and every check holds. ``reference`` is a
    script that does the task; ``dotx eval check`` replays it to prove that the task can be passed and that the
    checks accept it.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    category: TaskCategory
    goal: str
    mode: RunMode
    files: tuple[FileSeed, ...]
    checks: tuple[Check, ...]
    ending: ExpectedEnding
    reference: tuple[ScriptedTurn, ...]
    time_limit_s: float
    token_budget: int
