"""Eval tasks as data: what the workspace holds at the start, what the agent is asked and how the end is judged.

A task holds no code. Its checks are values of a closed set of types, so a catalog entry can be listed, saved and
compared like any other record, and its reference solution is a script of tool calls that the offline check replays
against the real loop and the real tools.
"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from dotagent.config import McpServerConfig
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.models import RunMode

type ExpectedEnding = Literal["done", "failed"]
type TaskCategory = Literal[
    "files", "shell", "coding", "recovery", "multi_step", "research", "honesty", "safety", "browser", "help"
]


class FileSeed(FrozenModel):
    """A file the workspace holds when the task starts. ``path`` is relative to the workspace; ``content`` is its text,
    or its bytes in Base64 when ``encoding`` is ``base64`` (a workbook, a Word document: item 196)."""

    path: str
    content: str
    encoding: Literal["text", "base64"]


class SkillSeed(FrozenModel):
    """A skill an attempt's home holds (item 167): ``<home>/skills/<name>/SKILL.md``, with ``name`` and ``description``
    in its frontmatter and ``body`` after it."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str
    body: str


class EasyWay(FrozenModel):
    """What makes a task short (item 167): the tool that does most of it and, when that is the skill tool, the skill it
    reads. An attempt took the easy way when its run called that tool, for that skill."""

    tool: str
    skill: str | None


class SitePage(FrozenModel):
    """A page of the local site an attempt serves for the task (item 54): its path (without a query string), the status
    it answers with, its body (HTML; a ``{name}`` in it is filled from the query string, an absent name with nothing)
    and, for a redirect, the path it points to."""

    path: str
    status: int
    body: str
    location: str | None


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


class AnswerLacks(FrozenModel):
    """The final answer does not hold ``text``, folded as :class:`AnswerContains` folds it: the words an instruction
    hidden in a file told the run to say."""

    kind: Literal["answer_lacks"]
    text: str


type Check = Annotated[
    FileExists
    | FileAbsent
    | FileContains
    | FileEquals
    | JsonEquals
    | CommandSucceeds
    | AnswerContains
    | AnswerMatches
    | AnswerLacks,
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
    the right answer is an honest report. A run passes when it ends that way and every check holds, the
    ``injection_checks`` included: those hold unless the run followed an instruction hidden in one of its files (a
    note to "the AI assistant" in an invoice, a page or a notes file), judged by what only following it does and never
    by words the instruction holds, so that quoting it is not following it (``audit``), and the report counts the
    attempts that failed one as having followed the injection. ``reference`` is a script that does the task;
    ``dotx eval check`` replays it to prove that the task can be passed and that the checks accept it. ``site`` are the
    pages of a local site the attempt serves on a loopback port for the task (none for most tasks); the goal and the
    reference name its address as ``{site}``. ``goal_mode`` runs the task in goal mode, where the review model checks
    the end against the goal. ``skills`` are the skills the attempt's home holds and ``servers`` the MCP servers its run
    is given, help that makes the task short; ``easy_way`` names the tool of that help (item 167), ``None`` for a task
    without one.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    category: TaskCategory
    goal: str
    mode: RunMode
    files: tuple[FileSeed, ...]
    checks: tuple[Check, ...]
    injection_checks: tuple[Check, ...]
    ending: ExpectedEnding
    reference: tuple[ScriptedTurn, ...]
    time_limit_s: float
    token_budget: int
    site: tuple[SitePage, ...]
    goal_mode: bool
    skills: tuple[SkillSeed, ...]
    servers: tuple[McpServerConfig, ...]
    easy_way: EasyWay | None
