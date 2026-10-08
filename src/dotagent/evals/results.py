"""What an eval run leaves behind: the outcome of each check and of each attempt, and the report that holds them."""

from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.evals.types import TaskCategory
from dotagent.store.models import RunStatus

# Why the harness cut a run short: it took longer than the task allows, or it used more tokens than the task allows.
type Cut = Literal["none", "time_limit", "token_budget"]


class CheckOutcome(FrozenModel):
    """One check after the run: what it asked for, whether it held and, when it did not, what was found instead."""

    check: str
    passed: bool
    detail: str


class RunMetrics(FrozenModel):
    """What a run did, counted from its events and its record. ``unanswered_questions`` are the questions the policy
    or the agent raised that nobody could answer, because an eval run is unattended."""

    steps: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    stalls: int
    compactions: int
    unanswered_questions: int


class TaskOutcome(FrozenModel):
    """One attempt at one task. It passed when the run ended the way the task asks and every check held."""

    task_id: str
    category: TaskCategory
    attempt: int
    passed: bool
    status: RunStatus
    cut: Cut
    answer: str
    checks: tuple[CheckOutcome, ...]
    metrics: RunMetrics
    seconds: float


class EvalSettings(FrozenModel):
    """The settings of the run that change how a model fares, kept with the report so that two reports can be compared
    knowing whether they ran the same way."""

    effort: str
    goal_mode: bool
    context_tokens: int
    preset: str


class EvalReport(FrozenModel):
    """Every attempt of one eval run against one model."""

    model: str
    started_at: float
    finished_at: float
    settings: EvalSettings
    outcomes: tuple[TaskOutcome, ...]
