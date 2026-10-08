"""The shapes the HTTP interface takes and gives. Records of the store are returned as they are."""

from typing import Literal

from pydantic import Field, JsonValue

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from dotagent.config import BrowserMode, ModelsConfig
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import Pane
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import ApprovalStatus, Behavior, MemoryRecord, RunMode, RunRecord, ScheduleRecord

_GOAL_CHARS: int = 50_000
_TEXT_CHARS: int = 20_000
_NAME_CHARS: int = 120
# Base64 takes four characters for every three bytes.
_UPLOAD_B64_CHARS: int = (MAX_ATTACHMENT_BYTES + 2) // 3 * 4


class ErrorView(FrozenModel):
    message: str
    context: dict[str, JsonValue]


class HealthView(FrozenModel):
    ok: bool
    version: str


# ----------------------------------------------------------------------------------------------- queries


class RunsQuery(FrozenModel):
    limit: int = Field(default=50, ge=1, le=200)
    thread_id: str | None = None


class ThreadsQuery(FrozenModel):
    limit: int = Field(default=100, ge=1, le=500)


class EventsQuery(FrozenModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2000)


class StreamQuery(FrozenModel):
    after: int = Field(default=0, ge=0)


class ApprovalsQuery(FrozenModel):
    status: ApprovalStatus | None = None
    limit: int = Field(default=100, ge=1, le=500)


class MemoriesQuery(FrozenModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class SearchQuery(FrozenModel):
    q: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


# ---------------------------------------------------------------------------------------------- requests


class StartRunRequest(FrozenModel):
    """Start a run. ``model``, ``effort``, ``approvals`` and ``goal_mode`` left out take the configured ones."""

    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    mode: RunMode = "task"
    model: str | None = None
    thread_id: str | None = None
    effort: Effort | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class DeleteThreadsRequest(FrozenModel):
    """The chats to delete, by thread id."""

    ids: list[str] = Field(min_length=1, max_length=500)


class SteerRequest(FrozenModel):
    text: str = Field(min_length=1, max_length=_TEXT_CHARS)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class AttachmentUpload(FrozenModel):
    """A file to attach to a message, base64-encoded (see ``agent.attachments``)."""

    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=200)
    data_b64: str = Field(min_length=1, max_length=_UPLOAD_B64_CHARS)


class AsideRequest(FrozenModel):
    """A side question to a run (``/btw``)."""

    text: str = Field(min_length=1, max_length=_TEXT_CHARS)


class DecisionRequest(FrozenModel):
    """Approve or deny. For a question the run asked, ``note`` carries the answer."""

    approve: bool
    note: str = Field(default="", max_length=_TEXT_CHARS)


class RuleRequest(FrozenModel):
    behavior: Behavior
    tool_glob: str = Field(min_length=1, max_length=_NAME_CHARS)
    match_regex: str = Field(max_length=500)
    description: str = Field(max_length=300)


class RuleEnabledRequest(FrozenModel):
    enabled: bool


class MemoryRequest(FrozenModel):
    content: str = Field(min_length=1, max_length=_TEXT_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=20)


class ScheduleCreateRequest(FrozenModel):
    """A new schedule. ``model``, ``effort``, ``approvals``, ``goal_mode`` and ``timezone`` left out take the configured
    ones when the schedule fires."""

    name: str = Field(min_length=1, max_length=_NAME_CHARS)
    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    cron: str | None = None
    at: str | None = None
    mode: Literal["task", "research"] = "task"
    model: str | None = None
    effort: Effort | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)


class ScheduleUpdateRequest(FrozenModel):
    """The whole new state of a schedule: as for a new one, plus whether it is switched on."""

    name: str = Field(min_length=1, max_length=_NAME_CHARS)
    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    cron: str | None
    at: str | None
    mode: Literal["task", "research"]
    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    goal_mode: bool | None
    timezone: str | None = Field(max_length=64)
    enabled: bool


class BrowserModeRequest(FrozenModel):
    mode: BrowserMode


# --------------------------------------------------------------------------------------------- responses


class RunDetail(FrozenModel):
    """A run as its page shows it. ``resumable`` is true when the daemon cut the run off and a new run can go on from
    where it stopped (see ``agent.resume``)."""

    run: RunRecord
    children: list[RunRecord]
    active: bool
    resumable: bool


class MemoryPage(FrozenModel):
    total: int
    items: list[MemoryRecord]


class ScheduleView(FrozenModel):
    """A schedule as the UI shows it; the times are in the schedule's own ``zone``. ``run_at_input`` is the one-time
    moment as a ``datetime-local`` field takes it (``2026-10-06T09:30``)."""

    record: ScheduleRecord
    when: str
    next_local: str | None
    last_local: str | None
    run_at_input: str | None
    zone: str


class ModelOption(FrozenModel):
    """A model as the pickers show it. ``efforts`` are its reasoning levels, lowest first (fewer than two: no choice);
    ``default_effort`` is the level the configured default effort selects on it."""

    ref: str
    vision: bool
    tools: bool
    thinking: bool
    context_length: int | None
    efforts: list[Effort]
    default_effort: Effort | None


class ProviderModels(FrozenModel):
    provider: str
    models: list[ModelOption]
    error: str | None


class PermissionsView(FrozenModel):
    """What macOS has granted to the program that runs the daemon, and the Settings panes that grant the rest."""

    program: str
    accessibility: bool
    input_events: bool
    screen_recording: bool
    panes: list[Pane]


class FailsafeView(FrozenModel):
    engaged: bool
    cancelled_runs: list[str]


class DeletedThreads(FrozenModel):
    """What a bulk delete did: the chats that went, and the ids that named no chat."""

    deleted: list[str]
    missing: list[str]


class BrowserView(FrozenModel):
    """Which Chrome the browser tools work in (``mode`` is the live one, which a switch changes without a restart),
    whether one is started or connected now, and what the files of the Chrome of the user say: whether it runs and
    allows remote debugging, and on which port. ``inspect_page`` is where the user switches that on."""

    mode: BrowserMode
    connected: bool
    chrome_running: bool
    remote_debugging: bool
    port: int | None
    inspect_page: str
    attach_timeout_s: float


class StatusView(FrozenModel):
    version: str
    pid: int
    started_at: float
    uptime_s: float
    home: str
    host: str
    port: int
    active_runs: list[str]
    pending_approvals: int
    scheduler_running: bool
    failsafe_engaged: bool
    preset: str
    approvals: ApprovalMode
    goal_mode: bool
    browser_mode: BrowserMode
    desktop_enabled: bool
    mcp_tools: int
    models: ModelsConfig
