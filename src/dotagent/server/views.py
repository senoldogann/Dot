"""The shapes the HTTP interface takes and gives. Records of the store are returned as they are."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from dotagent.agent.lessons import RunLessons
from dotagent.config import ModelsConfig
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import Pane
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.secrets import SecretSource
from dotagent.store.models import (
    AgentLook,
    AgentRecord,
    ApprovalRecord,
    ApprovalStatus,
    Behavior,
    BrowserMode,
    CampaignEntry,
    CampaignRecord,
    MemoryRecord,
    MemoryScore,
    RunMode,
    RunRecord,
    RunStatus,
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
    # The agent the run belongs to (item 86); None for Dot. Not with standing.
    agent_id: str | None = None
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


class RuleDraftRequest(FrozenModel):
    """A rule in the owner's own words, for the review model to draft (item 110)."""

    sentence: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class RuleEnabledRequest(FrozenModel):
    enabled: bool


class OwnerLinesRequest(FrozenModel):
    """The owner's own lines of the owner section (item 93), one fact each; Dot's lines stay as they are."""

    lines: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]] = Field(
        max_length=20
    )


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


class BrowserRaiseRequest(FrozenModel):
    """Whether Dot's Chrome is brought to the front while a run works (item 97)."""

    raise_window: bool


class KeyRequest(FrozenModel):
    """An API key to save: printable characters without spaces, as the keys of every provider are."""

    value: str = Field(min_length=1, max_length=_KEY_CHARS, pattern=r"^[!-~]+$")


# --------------------------------------------------------------------------------------------- responses


class AgentRequest(FrozenModel):
    """Make or change an agent (item 86). ``model`` empty and ``effort``/``approvals`` ``None`` take the configured
    ones; ``tools`` empty lets the agent use every tool; ``look`` ``None`` leaves the face its id's hue gives it (item
    102)."""

    name: str = Field(min_length=1, max_length=60)
    role: str = Field(max_length=120)
    instructions: str = Field(max_length=_GOAL_CHARS)
    model: str
    effort: Effort | None
    approvals: ApprovalMode | None
    tools: list[str]
    look: AgentLook | None = None


class AgentSummary(FrozenModel):
    """An agent as the list shows it: the record and its newest run, ``None`` before it ran."""

    record: AgentRecord
    latest: RunRecord | None


class TeamMessage(FrozenModel):
    """A message between agents (item 87) as the Team page shows it: the run behind it (``run_id``, the run of the
    agent that answered), the run that sent it (``parent_run_id``), who sent it and to whom (Dot's id is ``dot``),
    the message, the reply (``None`` until the run ends), the run's status and the moments."""

    run_id: str
    parent_run_id: str
    from_id: str
    from_name: str
    to_id: str
    to_name: str
    message: str
    reply: str | None
    status: RunStatus
    ts: float
    finished_at: float | None


class TeamView(FrozenModel):
    """``GET /api/team``: today's messages between agents, oldest first."""

    messages: list[TeamMessage]


class LearnedSkillView(FrozenModel):
    """A skill Dot or an agent learned from its own work (:mod:`dotagent.agent.playbooks`): its name and description,
    the kind of work, where it acted, the runs it comes from, how it fared (``uses`` done after reading it,
    ``failures``), whether it is retired, whose it is (``agent_id``, ``None`` for Dot's) and whether an agent's is
    ``shared`` with every run (item 94)."""

    name: str
    description: str
    kind: str
    host: str
    runs: tuple[str, ...]
    uses: int
    failures: int
    retired: bool
    written_at: float
    updated_at: float
    agent_id: str | None
    shared: bool


class LearnedSkillsView(FrozenModel):
    items: tuple[LearnedSkillView, ...]


class SkillShareRequest(FrozenModel):
    """Whether an agent's learned skill is offered to every run (item 94)."""

    shared: bool


class AgentLearning(FrozenModel):
    """What an agent learned for itself (item 94): its lessons (newest first) with how its own runs ended after
    being handed each (``scores``, per lesson some run was handed), and its learned skills, active and retired."""

    lessons: list[MemoryRecord]
    scores: dict[str, MemoryScore]
    skills: tuple[LearnedSkillView, ...]


class AgentDetail(FrozenModel):
    """An agent's page: the record, its newest runs (newest first), the newest messages it sent or received and what
    it learned."""

    record: AgentRecord
    runs: list[RunRecord]
    messages: list[TeamMessage]
    learned: AgentLearning


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
    ``held`` is true while the user has taken the run's browser over (``POST /runs/{id}/takeover``); ``agent`` is the
    agent the run belongs to (item 86), ``None`` for Dot."""

    run: RunRecord
    children: list[RunRecord]
    active: bool
    resumable: bool
    lessons: RunLessons | None
    questions: list[ApprovalRecord]
    agent: AgentRecord | None
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
    """A page of the notes, newest change first; ``resting`` names the items that rest
    (:func:`dotagent.memory.service.resting`), so the page can group them; ``scores`` says, per item some run was
    handed, how those runs ended; ``agents`` names the agents, by id, so the page can say whose a lesson is."""

    total: int
    items: list[MemoryRecord]
    resting: list[str]
    scores: dict[str, MemoryScore]
    agents: dict[str, str]


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


class PaletteSkill(FrozenModel):
    name: str
    description: str
    source: str


class PaletteTool(FrozenModel):
    name: str
    description: str


class PaletteServer(FrozenModel):
    name: str
    tools: tuple[PaletteTool, ...]


class PaletteView(FrozenModel):
    """What the composer's slash menu offers besides its own commands: the skills a run can use and the MCP servers
    with the tools the model calls."""

    skills: tuple[PaletteSkill, ...]
    servers: tuple[PaletteServer, ...]


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
    raise_window: bool


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


# The sections: what waits for the user, what works now, what ended today, what was learned today (item 72: the
# journal and the lessons) and what is due later today (schedules, a campaign's next session).
ActivitySection = Literal["waiting", "working", "recent", "learned", "due"]
# What a row of the Activity list is: a run of its own, a run delegated by another, a session of a campaign (or the
# next one due), an approval or a question a run waits for, a run the user holds (``takeover``), a schedule due, the
# reflection that ran today, a lesson or the journal note written today.
ActivityKind = Literal[
    "run", "delegated", "campaign", "approval", "question", "takeover", "schedule", "reflection", "lesson", "journal"
]


class ActivityRow(FrozenModel):
    """One thing in motion, or ended today: ``detail`` says what it needs or where it stands, ``channel`` is where the
    run last worked (browser, files, shell, desktop, mail; empty before its first tool result), ``since`` is when it
    started waiting, working or ended, ``href`` opens it in the web UI. A run that ended carries its ``minutes`` of
    wall time and its ``tokens`` (input and output together); every other row has ``None`` for both. A row for a run
    carries its ``steps`` and, for a delegated run, the ``parent_run_id`` of the run that delegated it, so the lists
    can nest it under its parent; the other rows have ``None`` for both. ``actor_id`` names the actor whose face the
    row wears (actors.js): the delegated run's own id, the campaign's, the schedule's; ``None`` for Dot's own."""

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
    steps: int | None
    parent_run_id: str | None
    actor_id: str | None


class ActivityView(FrozenModel):
    """``GET /activity``: what waits for the user, what works now, what ended today, what was learned today and what
    is due later today (see ``server.activity``)."""

    waiting: list[ActivityRow]
    working: list[ActivityRow]
    recent: list[ActivityRow]
    learned_today: list[ActivityRow]
    due: list[ActivityRow]
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
