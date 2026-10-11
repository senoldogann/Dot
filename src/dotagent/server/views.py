"""The shapes the HTTP interface takes and gives. Records of the store are returned as they are."""

from typing import Literal

from pydantic import Field, JsonValue

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from dotagent.agent.lessons import RunLessons
from dotagent.config import ModelsConfig
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import Pane
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.secrets import SecretSource
from dotagent.store.models import (
    ApprovalRecord,
    ApprovalStatus,
    Behavior,
    BrowserMode,
    CampaignEntry,
    CampaignRecord,
    MemoryRecord,
    RunMode,
    RunRecord,
    ScheduleRecord,
)
from dotagent.undo.restore import UndoAction, UndoState

_GOAL_CHARS: int = 50_000
_TEXT_CHARS: int = 20_000
_NAME_CHARS: int = 120
_KEY_CHARS: int = 1000
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
    """Start a run. ``model``, ``effort``, ``approvals`` and ``goal_mode`` left out take the configured ones.
    ``standing`` ("Keep going until done") starts a campaign on the goal instead, with this run as its opening session:
    a task, without goal mode (see ``RunManager.start_campaign``)."""

    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    mode: RunMode = "task"
    model: str | None = None
    thread_id: str | None = None
    effort: Effort | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    standing: bool = False
    # The Chrome a campaign works in; None takes [agent] campaign_browser. Only with standing.
    browser: BrowserMode | None = None
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


class KeyRequest(FrozenModel):
    """An API key to save: printable characters without spaces, as the keys of every provider are."""

    value: str = Field(min_length=1, max_length=_KEY_CHARS, pattern=r"^[!-~]+$")


# --------------------------------------------------------------------------------------------- responses


class CampaignChip(FrozenModel):
    """The campaign a run is a session of, as the run's page names it; ``session`` is the run's number among the
    campaign's sessions."""

    id: str
    title: str
    session: int


class RunDetail(FrozenModel):
    """A run as its page shows it. ``resumable`` is true when the daemon cut the run off and a new run can go on from
    where it stopped (see ``agent.resume``); ``lessons`` is what became of the lessons drawn from the run once it ended,
    ``None`` when none were (see ``agent.lessons``); ``questions`` are the questions the run left for the user that are
    still open, oldest first; ``campaign`` is the campaign the run is a session of, ``None`` for any other run;
    ``held`` is true while the user has taken the run's browser over (``POST /runs/{id}/takeover``)."""

    run: RunRecord
    children: list[RunRecord]
    active: bool
    resumable: bool
    lessons: RunLessons | None
    questions: list[ApprovalRecord]
    campaign: CampaignChip | None
    held: bool


class ActionStep(FrozenModel):
    """One acting tool call of a run, as the Actions panel lists it: when it ran, the tool, the channel it went through
    (``tools.channels``), the kind the policy gave it, its target (a path, an address, an application, a command) and
    summary, whether it succeeded, and its evidence, the result the model got, clipped."""

    event_id: int
    at: float
    tool: str
    channel: str
    kind: str
    target: str
    summary: str
    ok: bool
    evidence: str


class FileEdit(FrozenModel):
    """One file the run or a run it delegated to changed, with what taking the change back would do (see
    ``undo.restore.UndoStep``): ``state`` is ``ready`` for a file as the runs left it, which one button takes back,
    ``conflict``, ``cannot`` or ``as_before`` otherwise, with ``reason``. ``tool`` and ``run_id`` are the first change's,
    ``changes`` how many the runs made."""

    path: str
    state: UndoState
    action: UndoAction
    reason: str | None
    tool: str
    run_id: str
    noted_at: float
    changes: int


class RunActions(FrozenModel):
    """What a run did and what can be undone: its acting steps, its approvals and questions with their decisions, and
    the files it (or a run it delegated to) changed. ``ended`` says whether the run and its delegated runs have all
    ended, which the undo of a file needs."""

    steps: list[ActionStep]
    approvals: list[ApprovalRecord]
    edits: list[FileEdit]
    ended: bool


class UndoRequest(FrozenModel):
    path: str = Field(min_length=1, max_length=_TEXT_CHARS)


class CampaignSummary(FrozenModel):
    """A campaign as the list of campaigns shows it: how many sessions it ran, the session working now (``None`` when
    none is), when the next one starts, in this Mac's time zone (``None`` when none is planned), and the newest
    ``progress`` entry of its ledger (``None`` before the first)."""

    record: CampaignRecord
    sessions: int
    working: str | None
    next_local: str | None
    progress: CampaignEntry | None


class CampaignDetail(FrozenModel):
    """A campaign as its page shows it: its sessions, oldest first; the session working now and when the next one
    starts, as its summary says them; the newest entries of its ledger, oldest first; the questions its sessions asked
    the owner and what became of them, oldest first; and the tokens its sessions and the runs they delegated to used."""

    record: CampaignRecord
    sessions: list[RunRecord]
    working: str | None
    next_local: str | None
    entries: list[CampaignEntry]
    questions: list[ApprovalRecord]
    tokens: int


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


type ProviderProblem = Literal["no_key", "key_refused", "failed"]


class ProviderModels(FrozenModel):
    """The models of one provider, or why it listed none: ``error`` is the message and ``problem`` what kind of failure
    it is (``no_key``: no key is set for it, ``key_refused``: it answered HTTP 401 or 403, ``failed``: anything else);
    both are ``None`` when the provider answered."""

    provider: str
    models: list[ModelOption]
    error: str | None
    problem: ProviderProblem | None


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


class KeyView(FrozenModel):
    """The API key of one provider as the Settings page shows it; the value never leaves the daemon. ``source`` is where
    the key in use comes from (``None``: it is not set), ``saved`` whether Dot's ``.env`` file holds one (the page saves
    and removes only that one), ``tail`` the last characters of the key in use ("" when unset or too short)."""

    provider: str
    name: str
    source: SecretSource | None
    saved: bool
    tail: str


class KeysView(FrozenModel):
    """The API key of every provider, and the ``.env`` file the Settings page saves keys in."""

    env_file: str
    keys: list[KeyView]


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


ActivitySection = Literal["waiting", "working", "recent"]
# What a row of the Activity list is: a run of its own, a run delegated by another, a session of a campaign, an
# approval or a question a run waits for, a run the user holds (``takeover``), a schedule due within the hour.
ActivityKind = Literal["run", "delegated", "campaign", "approval", "question", "takeover", "schedule"]


class ActivityRow(FrozenModel):
    """One thing in motion, or ended today: ``detail`` says what it needs or where it stands, ``channel`` is where the
    run last worked (browser, files, shell, desktop, mail; empty before its first tool result), ``since`` is when it
    started waiting, working or ended, ``href`` opens it in the web UI. A run that ended carries its ``minutes`` of
    wall time and its ``tokens`` (input and output together); every other row has ``None`` for both."""

    id: str
    section: ActivitySection
    kind: ActivityKind
    title: str
    detail: str
    channel: str
    since: float
    href: str
    run_id: str | None
    status: str
    minutes: float | None
    tokens: int | None


class ActivityView(FrozenModel):
    """``GET /activity``: what waits for the user, what works now, what ended today (see ``server.activity``)."""

    waiting: list[ActivityRow]
    working: list[ActivityRow]
    recent: list[ActivityRow]
    generated_at: float


class PushKeyView(FrozenModel):
    """``GET /push/key``: the ``applicationServerKey`` a page subscribes with."""

    public_key: str


class PushKeysPart(FrozenModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscribeRequest(FrozenModel):
    """``POST /push/subscriptions``: ``PushSubscription.toJSON()`` of the browser."""

    endpoint: str = Field(min_length=1, max_length=2000, pattern=r"^https://")
    keys: PushKeysPart


class ActivityCount(FrozenModel):
    """``GET /activity/count``: the numbers a menu bar item shows, and the state of the whole of Dot (the face of the
    web UI shows the same: waiting when something waits for the user, working while a run is active, else idle)."""

    waiting: int
    working: int
    state: Literal["idle", "working", "waiting"]
