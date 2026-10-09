"""Row types returned by the store."""

from typing import Literal

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode

RunStatus = Literal["queued", "running", "done", "blocked", "failed", "cancelled"]
# A run in one of these has not ended.
WORKING_STATUSES: frozenset[RunStatus] = frozenset({"queued", "running", "blocked"})
RunMode = Literal["task", "chat", "research"]
# ``pending``: a run waits for the answer; ``left``: a question a run left for the user and went on without (it is never
# expired; answering it carries the work on in its thread).
ApprovalStatus = Literal["pending", "left", "approved", "denied", "expired"]
Behavior = Literal["allow", "preapproved", "ask", "handoff"]
# ``active``: its sessions go on; ``achieved``: the goal checker accepted its evidence, or the owner said so;
# ``stopped``: the owner stopped it, or it ran the sessions ``[agent] campaign_max_sessions`` allows.
CampaignStatus = Literal["active", "achieved", "stopped"]
CampaignEntryKind = Literal["progress", "setback", "note", "strategy", "question"]


class ThreadRecord(FrozenModel):
    id: str
    title: str
    source: str
    created_at: float
    updated_at: float


class ThreadSummary(FrozenModel):
    """One chat of the sidebar: a thread with the runs a person started on it (``run_count`` of them) and the latest of
    those runs, which the chat opens on. ``updated_at`` is the later of the last message and the start of the latest
    run. Threads that only hold delegated runs are not chats and are never listed."""

    id: str
    title: str
    source: str
    created_at: float
    updated_at: float
    run_count: int
    latest_run_id: str
    latest_status: RunStatus
    latest_mode: RunMode


class RunRecord(FrozenModel):
    """One run. ``effort`` is the reasoning level it was started with (``None`` for runs stored before it was kept);
    ``approvals`` and ``goal_mode`` are the approval mode and the goal mode it ran with (runs stored before they were
    kept hold ``ask`` and ``False``, which is how those runs behaved). ``campaign_id`` is the campaign the run is a
    session of, ``None`` for any other run."""

    id: str
    thread_id: str
    goal: str
    mode: RunMode
    model: str
    status: RunStatus
    created_at: float
    finished_at: float | None
    result: str
    steps: int
    input_tokens: int
    output_tokens: int
    parent_run_id: str | None
    source: str
    effort: Effort | None
    approvals: ApprovalMode
    goal_mode: bool
    campaign_id: str | None


class EventRecord(FrozenModel):
    id: int
    run_id: str
    ts: float
    kind: str
    data: JsonObject


class ApprovalRecord(FrozenModel):
    id: str
    run_id: str
    created_at: float
    decided_at: float | None
    status: ApprovalStatus
    tool: str
    summary: str
    severity: str
    args: JsonObject
    decided_by: str | None
    note: str


class RuleRecord(FrozenModel):
    id: str
    position: int
    enabled: bool
    behavior: Behavior
    tool_glob: str
    match_regex: str
    description: str
    created_at: float


class MemoryRecord(FrozenModel):
    """One note. ``source_run`` is the run that saved it, ``None`` for a note the user wrote; ``used_count`` and
    ``last_used_at`` say how often and when a run was handed it."""

    id: str
    content: str
    tags: tuple[str, ...]
    created_at: float
    updated_at: float
    source_run: str | None
    used_count: int
    last_used_at: float | None


class ScheduleOptions(FrozenModel):
    """How the runs of a schedule are started. ``None`` takes what the configuration says when the schedule fires:
    the main model, its default reasoning level, the configured approvals, no goal mode, the configured time zone."""

    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    goal_mode: bool | None
    timezone: str | None


class ScheduleRecord(FrozenModel):
    id: str
    name: str
    cron: str | None
    run_at: float | None
    goal: str
    mode: RunMode
    enabled: bool
    created_at: float
    last_run_at: float | None
    next_run_at: float | None
    thread_id: str | None
    last_status: str
    options: ScheduleOptions


class CampaignRecord(FrozenModel):
    """A goal that Dot works on in sessions, through restarts and days, until its outcome is verified or the owner
    stops it. ``thread_id`` is the owner's conversation it was started in. ``targets`` and ``verification`` are what
    its sessions recorded with ``campaign_targets`` (empty until then). ``cron`` repeats its sessions;
    ``next_session_at`` is when the next one starts, ``None`` while none is planned. ``model``, ``effort`` and
    ``approvals`` are the settings of the run that opened it, which every session keeps. ``evidence`` is what the
    accepted claim of the outcome said, empty while the campaign is not achieved."""

    id: str
    title: str
    goal: str
    targets: str
    verification: str
    status: CampaignStatus
    thread_id: str
    model: str
    effort: Effort
    approvals: ApprovalMode
    cron: str | None
    next_session_at: float | None
    created_at: float
    updated_at: float
    evidence: str


class CampaignEntry(FrozenModel):
    """One entry of a campaign's ledger, written by a session with ``campaign_note`` (``run_id`` is that session) or by
    the harness (``None``). ``numbers`` maps a measure to its value, such as ``{"leads": 12}``."""

    id: str
    campaign_id: str
    run_id: str | None
    ts: float
    kind: CampaignEntryKind
    text: str
    numbers: dict[str, float]
