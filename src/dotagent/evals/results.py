"""What an eval run leaves behind: the outcome of each check and of each attempt, and the report that holds them."""

from typing import Literal

from dotagent.config import PromptStyle, Tier, ToolSurface
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
    or the agent raised that nobody could answer, because an eval run is unattended. ``model_seconds`` is the time its
    model calls took (the steps and the side calls), ``tool_seconds`` the time its tool calls took; a report saved
    before they were measured has neither. ``acts`` are the browser and desktop act calls, ``single_acts`` the ones
    that carried one step, ``cached_tokens`` the input tokens the provider served from its prompt cache; a report
    saved before they were counted has none of the three. ``unevidenced_steps`` are the steps the run's last plan marks
    done without evidence (zero since the plan tools require it; a report saved before has none), ``unproven_steps``
    the done steps whose evidence the goal checker found nothing in the digest or the facts to bear out, summed over
    its checks (none in a report saved before the checker judged them, or in a run without goal mode).
    ``compaction_seconds`` is the time the summaries of its compactions took, ``not_found`` the web calls that met a
    page or host that is not there (an address the model guessed), ``rejected_arguments`` the calls refused because
    their arguments did not fit the tool (a turn lost each); a report saved before item 53 counted none of the three.
    ``help_calls`` are the calls of find_help, the search of the help the Mac offers (none in a report saved before
    item 167 counted them). ``plan_changes`` are the run's plan events, the plan it made and each change of it, the
    marks of its steps too (none in a report saved before item 252 counted them)."""

    steps: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    stalls: int
    compactions: int
    unanswered_questions: int
    model_seconds: float | None = None
    tool_seconds: float | None = None
    acts: int | None = None
    single_acts: int | None = None
    cached_tokens: int | None = None
    unevidenced_steps: int | None = None
    unproven_steps: int | None = None
    compaction_seconds: float | None = None
    not_found: int | None = None
    rejected_arguments: int | None = None
    help_calls: int | None = None
    plan_changes: int | None = None


class TaskOutcome(FrozenModel):
    """One attempt at one task. It passed when the run ended the way the task asks and every check held.
    ``injection_followed`` says whether the run followed an instruction hidden in one of the task's files (one of the
    task's injection checks did not hold); ``None`` for a task that hides none, and in a report saved before it was
    counted. ``easy_way_taken`` says whether the run took the easy way of its task, a call of the tool that makes it
    short that ran (item 167); ``None`` for a task without one, and in a report saved before it was counted."""

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
    injection_followed: bool | None = None
    easy_way_taken: bool | None = None


class EvalSettings(FrozenModel):
    """The settings of the run that change how a model fares, kept with the report so that two reports can be compared
    knowing whether they ran the same way. ``context_tokens`` is ``[agent] context_tokens``: a number of tokens, or
    ``"most"`` of each model's window (item 396). ``tier`` is the tier ``[agent] tiers`` set for the evaluated model,
    the only way to make it weak in an eval: an attempt's run history is empty. A report that does not record
    ``stall_escalation`` counts as run without it, one that does not record ``tier`` as run with the standard tier, one
    that does not record ``tool_surface`` as run with every tool offered at every step, one that does not record
    ``verify_answer_paths`` as run without the check of the paths a final answer names, one that does not record
    ``prompt`` as run with the full system prompt."""

    effort: str
    goal_mode: bool
    context_tokens: int | Literal["most"]
    preset: str
    stall_escalation: bool = False
    tier: Tier = "standard"
    tool_surface: ToolSurface = "full"
    verify_answer_paths: bool = False
    prompt: PromptStyle = "full"


class EvalReport(FrozenModel):
    """Every attempt of one eval run against one model."""

    model: str
    started_at: float
    finished_at: float
    settings: EvalSettings
    outcomes: tuple[TaskOutcome, ...]
