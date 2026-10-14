"""Row types returned by the store."""

from typing import Literal, Self

from pydantic import model_validator

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Effort, Speed
from dotagent.policy.types import ApprovalMode

# Which Chrome the browser tools work in: Dot's own, or the one the user has open (attached over its debugging port).
type BrowserMode = Literal["own", "attach"]

RunStatus = Literal["queued", "running", "done", "blocked", "failed", "cancelled"]
# A run in one of these has not ended.
WORKING_STATUSES: frozenset[RunStatus] = frozenset({"queued", "running", "blocked"})
RunMode = Literal["task", "chat", "research"]
# How a run was handed a note: at its start, by a memory_search call, or as a note changed while it ran.
MemoryUseHow = Literal["start", "search", "live"]
# ``pending``: a run waits for the answer; ``left``: a question a run left for the user and went on without (it is never
# expired; answering it carries the work on in its thread).
ApprovalStatus = Literal["pending", "left", "approved", "denied", "expired"]
Behavior = Literal["allow", "preapproved", "ask", "handoff"]
# ``active``: its sessions go on; ``achieved``: the goal checker accepted its evidence, or the owner said so;
# ``stopped``: the owner stopped it (also with the emergency stop of desktop control, which ends its session), or it ran
# the sessions ``[agent] campaign_max_sessions`` allows.
CampaignStatus = Literal["active", "achieved", "stopped"]
CampaignEntryKind = Literal["progress", "setback", "note", "strategy", "question"]


class ThreadRecord(FrozenModel):
    id: str
    title: str
    source: str
    created_at: float
    updated_at: float


class ChatChoices(FrozenModel):
    """What the next follow-up of a chat starts with, as its composer shows it (item 199): the mode, the model and the
    reasoning level asked of it, the speed (fast mode, item 223), the approval mode and goal mode. ``None`` takes the
    daemon's: its default model, the configured level, ``[policy] approvals`` and ``[agent] goal_mode``; a chat (the
    mode) has no goal mode. A chat's choices are those its first run started with, until the owner picks others in its
    composer."""

    mode: RunMode
    model: str | None
    effort: Effort | None
    speed: Speed
    approvals: ApprovalMode | None
    goal_mode: bool | None

    @model_validator(mode="after")
    def _goal_mode_fits(self) -> Self:
        if self.mode == "chat" and self.goal_mode is True:
            raise ValueError("goal mode is for task and research runs, not for a chat")
        return self


class ThreadSummary(FrozenModel):
    """One chat of the sidebar: a thread with the runs a person started on it (``run_count`` of them) and the latest of
    those runs, which the chat opens on. ``updated_at`` is the later of the last message and the start of the latest
    run; ``latest_created_at`` is when the latest run was created, which a message during the run does not move (the
    sidebar counts a working chat's time from it). ``pinned_at`` is when the owner pinned the chat, ``None`` for one not
    pinned (item 130). ``unread`` is true when the latest run ended after the owner last saw the chat, or ended and the
    owner marked the chat unread (item 131). Threads that only hold delegated runs are not chats and are never listed."""

    id: str
    title: str
    source: str
    created_at: float
    updated_at: float
    pinned_at: float | None
    run_count: int
    latest_run_id: str
    latest_status: RunStatus
    latest_mode: RunMode
    latest_created_at: float
    unread: bool


class RunRecord(FrozenModel):
    """One run. ``effort`` is the reasoning level it was started with (``None`` for runs stored before it was kept);
    ``speed`` is ``fast`` when it was started in fast mode (item 223), which its steps on a model with fast mode use;
    ``approvals`` and ``goal_mode`` are the approval mode and the goal mode it ran with (runs stored before they were
    kept hold ``standard``, ``ask`` and ``False``, which is how those runs behaved). ``campaign_id`` is the campaign the
    run is a session of, ``None`` for any other run. ``agent_id`` is the agent the run belongs to (item 86), ``None``
    for Dot."""

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
    speed: Speed
    approvals: ApprovalMode
    goal_mode: bool
    campaign_id: str | None
    agent_id: str | None


CharacterShape = Literal["circle", "pebble", "squircle", "capsule", "triangle", "hexagon", "cloud", "droplet"]
CharacterExpression = Literal[
    "neutral",
    "attentive",
    "surprised",
    "excited",
    "happy",
    "laughing",
    "angry",
    "sad",
    "scared",
    "suspicious",
    "confused",
    "curious",
    "proud",
    "shy",
    "unimpressed",
    "sleepy",
]
CharacterColour = Literal[
    "ink", "brown", "red", "orange", "amber", "green", "teal", "blue", "violet", "pink", "grey", "cream"
]


class AgentLook(FrozenModel):
    """How an agent's character looks (item 102): one of the shapes, rest expressions and colours of the character
    engine (``static/js/character/skins.js`` and ``expressions.js``)."""

    shape: CharacterShape
    expression: CharacterExpression
    colour: CharacterColour


class AgentRecord(FrozenModel):
    """An agent the owner made (item 86): a name, a role in one line, the instructions that join the system prompt of
    its runs, and the bounds of its runs: ``model`` (empty for the configured one), ``effort`` and ``approvals``
    (``None`` for the configured ones) and ``tools``, the names of the tools it may use (empty for every tool). Its
    face is its ``look`` (item 102), or with ``None`` the shape and colour its id's hue gives it (actors.js).
    ``made_by`` is the run that made it with ``create_agent`` (item 99), ``None`` for one the owner made. Dot itself
    is ``DOT_AGENT``, never stored; its look lives in the kv store under ``look:dot``."""

    id: str
    name: str
    role: str
    instructions: str
    model: str
    effort: Effort | None
    approvals: ApprovalMode | None
    tools: tuple[str, ...]
    created_at: float
    updated_at: float
    made_by: str | None
    look: AgentLook | None


DOT_AGENT_ID: str = "dot"
DOT_AGENT: AgentRecord = AgentRecord(
    id=DOT_AGENT_ID,
    name="Dot",
    role="Your assistant",
    instructions="",
    model="",
    effort=None,
    approvals=None,
    tools=(),
    created_at=0.0,
    updated_at=0.0,
    made_by=None,
    look=None,
)


class EventRecord(FrozenModel):
    id: int
    run_id: str
    ts: float
    kind: str
    data: JsonObject


class SentMessage(FrozenModel):
    """A message the pages sent with a key of their own, kept so that the same message sent again (after an answer
    that was lost) is not done twice: ``target`` is the run it started or steered, or the queued message it became;
    ``None`` while its sending is under way."""

    key: str
    sent_at: float
    target: str | None


class WorkingRun(FrozenModel):
    """A run at work (queued, running or blocked on a question), its chat, and the process that drives it
    (:mod:`dotagent.agent.owners`)."""

    run_id: str
    thread_id: str
    owner: str


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


class ShownPage(FrozenModel):
    """A page of HTML that a run wrote and showed in the conversation (``show_page``). Its id is all it takes to read
    it: the daemon serves the page to whoever names it (``GET /pages/<id>``), so the id is made to be unguessable."""

    id: str
    run_id: str
    title: str
    html: str
    created_at: float


class RuleRecord(FrozenModel):
    id: str
    position: int
    enabled: bool
    behavior: Behavior
    tool_glob: str
    match_regex: str
    description: str
    created_at: float


class QueuedMessage(FrozenModel):
    """A message sent to a chat while a run worked there: it waits in the chat's queue, in ``position`` order, and
    starts as the chat's next run, with the choices it was sent with, once the run before it has ended done (see
    ``RunManager.queue``). ``attachments`` are the ids of its uploaded files; ``source`` is where it was sent from,
    and the source of the run it starts."""

    id: str
    thread_id: str
    position: int
    text: str
    attachments: tuple[str, ...]
    mode: RunMode
    model: str | None
    effort: Effort | None
    speed: Speed
    approvals: ApprovalMode | None
    goal_mode: bool | None
    agent_id: str | None
    source: str
    created_at: float


class MemoryRecord(FrozenModel):
    """One note. ``source_run`` is the run that saved it, ``None`` for a note the user wrote; ``agent_id`` is the
    agent whose lesson it is (item 94), ``None`` for Dot's, which every run may be handed; ``used_count`` and
    ``last_used_at`` say how often and when a run was handed it."""

    id: str
    content: str
    tags: tuple[str, ...]
    created_at: float
    updated_at: float
    source_run: str | None
    agent_id: str | None
    used_count: int
    last_used_at: float | None


class MemoryScore(FrozenModel):
    """How the runs a note was handed to ended: ``handed`` runs in all, of which ``done`` ended done and ``failed``
    failed (the rest were cancelled, or still work)."""

    handed: int
    done: int
    failed: int


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
    ``next_session_at`` is when the next one starts, ``None`` while none is planned. ``model``, ``effort``, ``speed``
    and ``approvals`` are the settings of the run that opened it, which every session keeps. ``files`` are the paths of
    the files the owner attached to its goal. ``evidence`` is what the accepted claim of the outcome said, empty while
    the campaign is not achieved. ``browser`` is the Chrome its sessions work in: Dot's own (``own``) or the one the
    owner has open (``attach``); ``None`` for a campaign from before the choice, whose sessions use the daemon's."""

    id: str
    title: str
    goal: str
    targets: str
    verification: str
    status: CampaignStatus
    thread_id: str
    model: str
    effort: Effort
    speed: Speed
    approvals: ApprovalMode
    files: tuple[str, ...]
    cron: str | None
    next_session_at: float | None
    created_at: float
    updated_at: float
    evidence: str
    browser: BrowserMode | None


class PushSubscription(FrozenModel):
    """A browser's web push subscription (item 52): the push service's endpoint and the keys the message is encrypted
    with, as ``PushSubscription.toJSON()`` gives them."""

    endpoint: str
    p256dh: str
    auth: str
    created_at: float


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
