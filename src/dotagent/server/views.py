"""The shapes the HTTP interface takes and gives. Records of the store are returned as they are."""

from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictBool, StringConstraints, model_validator

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, AttachedFile, AttachmentKind
from dotagent.agent.learning_health import LearningHealth
from dotagent.agent.lessons import RunLessons
from dotagent.agent.resume import CutOff
from dotagent.browser.look import PageLook
from dotagent.browser.profiles import ProfileView
from dotagent.config import BrowserProfileName, DebugPort, JudgeProvider, ModelReference, ModelsConfig, ResumeMode
from dotagent.connections.catalogue import ConnectionKind
from dotagent.connections.directory import PluginView
from dotagent.connections.states import ConnectionState, ConnectionStep
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import Pane
from dotagent.help.elsewhere import FoundElsewhere
from dotagent.llm.roles import ModelRole
from dotagent.llm.types import Effort, OneMessage, Speed, WindowSource
from dotagent.mcp.types import McpFailure
from dotagent.notify.keys import keys_problem
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
    ChatChoices,
    HarnessVersion,
    MemoryRecord,
    MemoryScore,
    QueuedMessage,
    RunMode,
    RunPicks,
    RunRecord,
    RunStatus,
    ScheduleRecord,
)
from dotagent.tailnet import TailnetState
from dotagent.undo.restore import UndoAction, UndoState

_GOAL_CHARS: int = 50_000
_TEXT_CHARS: int = 20_000
_NAME_CHARS: int = 120
# What the composer picks for one message (item 318): at most this many servers and skills, each by its name.
MAX_PICKED_PLUGINS: int = 8
MAX_PICKED_SKILLS: int = 4
PickedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_NAME_CHARS)]
_KEY_CHARS: int = 1000
# Base64 takes four characters for every three bytes.
_UPLOAD_B64_CHARS: int = (MAX_ATTACHMENT_BYTES + 2) // 3 * 4


class ErrorView(FrozenModel):
    message: str
    context: dict[str, JsonValue]


class HealthView(FrozenModel):
    ok: bool
    version: str


class ProofView(FrozenModel):
    """The daemon's answer to a nonce (:func:`dotagent.server.guard.daemon_proof`)."""

    proof: str


class PhoneView(FrozenModel):
    """How a phone reaches Dot (item 267): the addresses a reverse proxy on the owner's network serves it at, one for
    each name of ``[server] reach``; how it reaches the Mac through Tailscale, and whether the daemon keeps the Mac
    awake while it charges (item 271)."""

    addresses: list[str]
    tailnet: TailnetState
    keep_awake: bool


class AwakeRequest(FrozenModel):
    """Whether the daemon keeps the Mac awake while it charges (item 271)."""

    on: bool


class PairingView(FrozenModel):
    """A pairing code (item 267), as Settings › Phone shows it, and when it stops being good (seconds since the
    epoch)."""

    code: str
    expires_at: float


class PairRequest(FrozenModel):
    """A pairing code as a phone's app sends it (item 267)."""

    code: str = Field(min_length=1, max_length=32)


class PairedView(FrozenModel):
    """The access token a pairing code was traded for (item 267)."""

    token: str


# ----------------------------------------------------------------------------------------------- queries


class RunsQuery(FrozenModel):
    limit: int = Field(default=50, ge=1, le=200)
    thread_id: str | None = None


class ThreadsQuery(FrozenModel):
    limit: int = Field(default=100, ge=1, le=500)


class HelpersQuery(FrozenModel):
    limit: int = Field(default=50, ge=1, le=200)


class TerminalView(FrozenModel):
    """A terminal session of a chat (item 229), as the line over the composer shows it: its command, whether it runs in
    the background (its end is told to the chat), when it started (seconds since the epoch), whether its program has
    ended, and its exit status once it ended by itself."""

    id: str
    command: str
    background: bool
    started_at: float
    ended: bool
    exit_status: int | None


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


class ProofQuery(FrozenModel):
    """A nonce of the client, made fresh for each proof it asks for."""

    nonce: str = Field(pattern=r"^[0-9a-f]{32,128}$")


# ---------------------------------------------------------------------------------------------- requests

# The pages' own key of a new message (item 189): the message sent again with it, after an answer that was lost, gets
# what its first sending made (``dotagent.server.sending.send_once``).
MessageKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{16,64}$")]


class StartRunRequest(FrozenModel):
    """Start a run. ``model``, ``effort``, ``approvals`` and ``goal_mode`` left out take the configured ones; ``speed``
    is ``fast`` for fast mode, on a model that has it (item 223); ``once`` is a choice for this message only (item 251):
    ``ultrathink`` asks the model for its Max level, ``ultracode`` for its Extra high level and tells the run to split
    its independent parts across delegated runs, while the chat keeps ``effort``. ``plugins``, ``skills`` and
    ``plan_first`` are what the owner picked in the composer for this message (item 318, :func:`picks_of`).
    ``standing`` ("Keep going until done") starts a campaign on the goal instead, with this run as its opening session:
    a task, without goal mode and without picks (see ``RunManager.start_campaign``). ``loop``, a rhythm (``30m``,
    ``2h``), makes the goal a loop of the run's chat too, as ``/loop`` in a new chat's box does (item 400)."""

    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    mode: RunMode = "task"
    model: str | None = None
    thread_id: str | None = None
    effort: Effort | None = None
    speed: Speed = "standard"
    once: OneMessage | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    standing: bool = False
    # The Chrome a campaign works in; None takes [agent] campaign_browser. Only with standing.
    browser: BrowserMode | None = None
    # The agent the run belongs to (item 86); None for Dot. Not with standing.
    agent_id: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    plugins: list[PickedName] = Field(default_factory=list, max_length=MAX_PICKED_PLUGINS)
    skills: list[PickedName] = Field(default_factory=list, max_length=MAX_PICKED_SKILLS)
    plan_first: bool = False
    # How often the goal is asked again in the run's chat (LoopRequest.every); None for no loop. Not with standing.
    loop: str | None = Field(default=None, min_length=2, max_length=8)
    key: MessageKey | None = None

    @model_validator(mode="after")
    def _fits_standing(self) -> Self:
        if self.standing and self.agent_id is not None:
            raise ValueError("a campaign (standing) is Dot's own: it cannot be started for an agent")
        if self.standing and (self.plugins or self.skills or self.plan_first):
            raise ValueError("plugins, skills and Plan first go with a message, not with a campaign (standing)")
        if self.standing and self.loop is not None:
            raise ValueError("a loop asks again in a chat; a campaign (standing) goes on by itself")
        if self.browser is not None and not self.standing:
            raise ValueError("browser chooses the Chrome of a campaign: it goes with standing only")
        return self


class DeleteThreadsRequest(FrozenModel):
    """The chats to delete, by thread id."""

    ids: list[str] = Field(min_length=1, max_length=500)


class ThreadChange(FrozenModel):
    """What the owner changes on a chat: from its menu (item 130), its name, its pin, or whether it is seen (item 131);
    from its composer, the choices its next follow-up starts with (item 199). One of them at least."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_NAME_CHARS)] | None = None
    pinned: StrictBool | None = None
    seen: StrictBool | None = None
    choices: ChatChoices | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if self.title is None and self.pinned is None and self.seen is None and self.choices is None:
            raise ValueError("a change of a chat names its title, its pin, whether it is seen or its choices")
        return self


class SteerRequest(FrozenModel):
    text: str = Field(min_length=1, max_length=_TEXT_CHARS)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    key: MessageKey | None = None


class QueueMessageRequest(FrozenModel):
    """Put a message in a chat's queue (item 148), with the choices of the run it starts; those left out take the
    configured ones then, as for ``StartRunRequest``."""

    text: str = Field(min_length=1, max_length=_GOAL_CHARS)
    mode: RunMode = "task"
    model: str | None = None
    effort: Effort | None = None
    speed: Speed = "standard"
    once: OneMessage | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    agent_id: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    plugins: list[PickedName] = Field(default_factory=list, max_length=MAX_PICKED_PLUGINS)
    skills: list[PickedName] = Field(default_factory=list, max_length=MAX_PICKED_SKILLS)
    plan_first: bool = False
    key: MessageKey | None = None


def picks_of(plugins: Sequence[str], skills: Sequence[str], plan_first: bool) -> RunPicks:
    """The picks a message names (item 318), each name once, in the order they were picked."""
    return RunPicks(plugins=tuple(dict.fromkeys(plugins)), skills=tuple(dict.fromkeys(skills)), plan_first=plan_first)


class SavedSkill(FrozenModel):
    """A skill saved from a run at the owner's word (item 318): its name, and whether the owner's skill of its kind was
    updated rather than a new one written."""

    name: str
    updated: bool


class QueueEditRequest(FrozenModel):
    text: str = Field(min_length=1, max_length=_GOAL_CHARS)
    # The files the message carries after the edit (item 376); left out, they stay as they were.
    attachments: list[str] | None = Field(default=None, max_length=MAX_ATTACHMENTS)


class QueueOrderRequest(FrozenModel):
    """The messages of a chat's queue in their new order, each once."""

    ids: list[str] = Field(min_length=1, max_length=500)


class QueuedFile(FrozenModel):
    """A file of a queued message, as the composer lists it when the message is edited."""

    id: str
    name: str
    size: int
    kind: AttachmentKind


class QueuedMessageView(QueuedMessage):
    """A queued message with the files it carries named, so editing it puts them back in the box (item 376)."""

    files: list[QueuedFile]


class QueueView(FrozenModel):
    """A chat's queue: its messages, the next one first, and whether it waits for the owner to resume it."""

    held: bool
    messages: list[QueuedMessageView]


class AttachmentUpload(FrozenModel):
    """A file to attach to a message, base64-encoded (see ``agent.attachments``)."""

    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=200)
    data_b64: str = Field(min_length=1, max_length=_UPLOAD_B64_CHARS)


class LoopRequest(FrozenModel):
    """A loop of a chat (item 281): ``text`` asked again on the rhythm ``every`` (``30m``, ``2h``)."""

    every: str = Field(min_length=2, max_length=8)
    text: str = Field(min_length=1, max_length=_TEXT_CHARS)


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
    """A new schedule. ``model``, ``effort``, ``speed``, ``approvals``, ``goal_mode`` and ``timezone`` left out take the
    configured ones when the schedule fires (the standard speed for ``speed``)."""

    name: str = Field(min_length=1, max_length=_NAME_CHARS)
    goal: str = Field(min_length=1, max_length=_GOAL_CHARS)
    cron: str | None = None
    at: str | None = None
    mode: Literal["task", "research"] = "task"
    model: str | None = None
    effort: Effort | None = None
    speed: Speed | None = None
    approvals: ApprovalMode | None = None
    goal_mode: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    # Item 257: how many earlier runs' answers a firing carries, and whether it goes on in the same chat.
    history_runs: int | None = Field(default=None, ge=0, le=20)
    same_chat: bool | None = None


class ScheduleUpdateRequest(FrozenModel):
    """The whole new state of a schedule: as for a new one, plus whether it is switched on. ``history_runs``,
    ``same_chat`` and ``speed`` left out take the configured ones, as a client from before item 257 sends none of them
    (the standard speed for ``speed``, item 291)."""

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
    history_runs: int | None = Field(default=None, ge=0, le=20)
    same_chat: bool | None = None
    speed: Speed | None = None
    enabled: bool


class BrowserModeRequest(FrozenModel):
    mode: BrowserMode


class MainModelRequest(FrozenModel):
    """The model to make the default (item 199): the one a run that names none starts on."""

    model: ModelReference


class RoleModelRequest(FrozenModel):
    """The model for a part of Dot's own work (item 232): the ``fast``, the ``review`` or the ``vision`` model."""

    role: ModelRole
    model: ModelReference


class ResumeRequest(FrozenModel):
    """Whether the daemon's next start resumes on its own the runs it cut off (item 231): ``[agent] resume``."""

    resume: ResumeMode


class ResumeView(FrozenModel):
    """Whether the daemon's next start resumes on its own the runs it cut off, and how long before it they may have been
    cut off (``[agent] resume_within_s``)."""

    resume: ResumeMode
    resume_within_s: int


class JudgeRequest(FrozenModel):
    """Whether the typed judge answers through TypeSafe or answers nothing (item 255): ``[judge] provider``."""

    provider: JudgeProvider


class JudgeView(FrozenModel):
    """The typed judge as it stands: its provider, and whether the key of its service (``key_name``) is set."""

    provider: JudgeProvider
    key_name: str
    key_set: bool


class SandboxRequest(FrozenModel):
    """Whether the processes of a run outside yolo start in the macOS sandbox (item 262): ``[shell] sandbox``."""

    sandbox: bool


class SandboxView(FrozenModel):
    """Whether the processes of a run outside yolo start in the macOS sandbox now (item 262)."""

    sandbox: bool


class BrowserRaiseRequest(FrozenModel):
    """Whether Dot's Chrome is brought to the front while a run works (item 97)."""

    raise_window: bool


class BrowserDebugPortRequest(FrozenModel):
    """The port of this Mac on which Dot's own Chrome takes DevTools connections (item 265); 0 for none."""

    debug_port: DebugPort


class BrowserEnabledRequest(FrozenModel):
    """Whether runs are offered the browser tools (item 354)."""

    enabled: bool


class BrowserProfileRequest(FrozenModel):
    """A profile of Dot's own Chrome to add, or to work on (item 355)."""

    name: BrowserProfileName


class KeyRequest(FrozenModel):
    """An API key to save: printable characters without spaces, as the keys of every provider are."""

    value: str = Field(min_length=1, max_length=_KEY_CHARS, pattern=r"^[!-~]+$")


# --------------------------------------------------------------------------------------------- responses


class AgentRequest(FrozenModel):
    """Make or change an agent (item 86). ``model`` empty and ``effort``/``approvals`` ``None`` take the configured
    ones; ``tools`` names the tools the agent may use, is ``None`` (or left out) for every tool, and is empty for the
    ones an agent has, which a new agent has none of, so a list that did not load never lifts the bounds of an agent
    (the code review's Y5); ``look`` ``None`` leaves the face its id's hue gives it (item 102)."""

    name: str = Field(min_length=1, max_length=60)
    role: str = Field(max_length=120)
    instructions: str = Field(max_length=_GOAL_CHARS)
    model: str
    effort: Effort | None
    approvals: ApprovalMode | None
    tools: list[str] | None = None
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


class LearningHealthView(FrozenModel):
    """``GET /api/memories/learning``: how Dot's learning fares (:mod:`dotagent.agent.learning_health`, review O25):
    the record, ``None`` before the first outcome, why learning has stopped (``None`` while it works), and whether the
    harness's nightly step is set to run (``[harness] step_time``, item 391), so that a step that never ran reads as
    off, not as failing."""

    health: LearningHealth | None
    stalled: str | None
    harness_on: bool


class HarnessView(FrozenModel):
    """``GET /api/harness`` (item 389, docs/harness.md): the adopted version, ``None`` while a run's prompt is Dot's
    own text alone, every version the newest first, the bounds a component keeps to, and how the nightly step is set
    (item 393): when it runs ("off" never), the scoring and final sets, the repeats, and who adopts a version the probe
    showed better."""

    adopted: HarnessVersion | None
    versions: tuple[HarnessVersion, ...]
    habits_chars: int
    tool_guidance_chars: int
    component_lines: int
    step_time: str
    scoring_tasks: tuple[str, ...]
    final_tasks: tuple[str, ...]
    step_repeats: int
    adopt: Literal["owner", "auto"]


class HarnessRequest(FrozenModel):
    """``POST /api/harness``: a version of the owner's. A component given replaces the adopted version's text, one left
    out (``None``) keeps it; ``summary`` says in a line what the version is; ``adopt`` adopts it at once."""

    habits: str | None
    tool_guidance: str | None
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    adopt: bool


class LearnedSkillsView(FrozenModel):
    items: tuple[LearnedSkillView, ...]


class SkillShareRequest(FrozenModel):
    """Whether an agent's learned skill is offered to every run (item 94)."""

    shared: bool


class ElsewhereChange(FrozenModel):
    """The owner's yes to a skill or an MCP server that another agent of the Mac set up (item 165), or taking it back:
    the item as found, by its kind, its agent and its name."""

    kind: Literal["skill", "server"]
    agent: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    added: bool


class ElsewhereItem(FrozenModel):
    """A skill or an MCP server that another agent of the Mac set up, as found: by its kind, its agent and its name."""

    kind: Literal["skill", "server"]
    agent: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class ElsewhereImport(FrozenModel):
    """The owner's yes to several found items at once (item 219), each named once."""

    items: tuple[ElsewhereItem, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        if len(set(self.items)) != len(self.items):
            raise ValueError("an item is named more than once")
        return self


class ElsewhereRefusal(FrozenModel):
    """A found item an import could not add, and why."""

    item: ElsewhereItem
    reason: str


class ElsewhereImported(FrozenModel):
    """What an import did (item 219): the items it added, those it could not and why, and what is found after it."""

    added: tuple[ElsewhereItem, ...]
    refused: tuple[ElsewhereRefusal, ...]
    found: FoundElsewhere


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
    """A run as its page shows it. ``resumable`` is true when the run was cut off (by the daemon, or by the end of its
    ``dotx`` command) and a new run can go on from where it stopped (see ``agent.resume``); ``lessons`` is what became
    of the lessons drawn from the run once it ended, ``None`` when none were (see ``agent.lessons``); ``questions`` are
    the questions the run left for the user that are still open, oldest first; ``campaign`` is the campaign the run is a
    session of, ``None`` for any other run; ``held`` is true while the user has taken the run's browser over
    (``POST /runs/{id}/takeover``); ``agent`` is the agent the run belongs to (item 86), ``None`` for Dot;
    ``keep_going`` is true for a run in goal mode whose goal asks it to keep going until it is done, which its goal
    check is told (item 132). ``active`` is true while the daemon drives the run; ``elsewhere`` while another
    process that lives drives it at work (a ``dotx`` command): its page follows it and its Stop asks that process to
    stop it, but it is not steered or taken over from here. ``choices`` are what the next follow-up of the run's chat
    starts with (item 199), ``None`` for a chat that has none of its own yet. ``title`` is the title of the run's chat,
    which a phone's top bar shows (item 295). ``cut_off`` is how the run was cut off (``agent.resume.cut_off_by``),
    which the line that offers to resume it names (item 299); ``None`` for a run that ended otherwise or works."""

    run: RunRecord
    title: str
    children: list[RunRecord]
    active: bool
    elsewhere: bool
    resumable: bool
    cut_off: CutOff | None
    lessons: RunLessons | None
    questions: list[ApprovalRecord]
    agent: AgentRecord | None
    campaign: CampaignChip | None
    held: bool
    keep_going: bool
    choices: ChatChoices | None


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
    ``conflict``, ``cannot`` or ``as_before`` otherwise, with ``reason``. ``tool`` and ``run_id`` are the first
    change's, ``changes`` how many the runs made."""

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


class RewindRequest(FrozenModel):
    """The owner's answer to Edit from here (item 282): ``revert`` takes back the file changes of the runs taken out of
    the chat too, ``keep`` leaves the files as they are."""

    files: Literal["revert", "keep"]


class RewindView(FrozenModel):
    """A chat rewound to before one of the owner's messages (item 282): the run it ends with now (``None`` when none is
    left, and a new chat's composer takes the message), the message's words and attachments for the composer, and the
    files taken back (``reverted``) and those left as they are, with why (``left``)."""

    thread_id: str
    back_to: str | None
    text: str
    files: list[AttachedFile]
    reverted: list[FileEdit]
    left: list[FileEdit]


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


class ContextChoice(FrozenModel):
    """The context size of a model's runs as Settings › Models shows and chooses it (item 250). ``tokens`` is the budget
    of its next run; ``chosen`` the size chosen for it, ``None`` when its runs have the default; ``default`` the budget
    it has without a choice: three quarters of its window while ``[agent] context_tokens`` is ``"most"`` (item 396),
    96,000 when its provider does not say its window, else ``configured``, cut to three quarters of its window when that
    is less; ``configured`` the number ``[agent] context_tokens`` sets, ``None`` while it is ``"most"``; ``floor`` and
    ``ceiling`` the least and the most it may be given (``ceiling`` is three quarters of its window, ``None`` when its
    provider does not say); ``sizes`` the sizes besides the default that it may be given, smallest first, none when its
    window allows none; ``summarise_at`` the estimated prompt past which its next run summarises its older part."""

    tokens: int
    chosen: int | None
    default: int
    configured: int | None
    floor: int
    ceiling: int | None
    sizes: list[int]
    summarise_at: int


class ContextSizeRequest(FrozenModel):
    """The context size to give the runs of ``model`` (item 250); ``None`` gives them the default one."""

    model: ModelReference
    tokens: int | None


class ContextEvidenceQuery(FrozenModel):
    """The model whose runs say what its context size does (item 250)."""

    model: ModelReference


class ModelOption(FrozenModel):
    """A model as the pickers show it. ``context_length`` is the window Dot uses, ``own_context_length`` the model's own
    when it is larger and ``window_source`` where the window comes from (see ``ModelInfo``); ``context`` is the context
    size of its runs (item 250); ``efforts`` are its reasoning levels, lowest first (fewer than two: no choice);
    ``default_effort`` is the level the configured default effort selects on it; ``free``: served at no cost;
    ``learns``: its provider may use what is sent to it to improve its models (some of Zen's free models, item 283);
    ``fast_mode``: it has fast mode (item 223)."""

    ref: str
    vision: bool
    tools: bool
    thinking: bool
    context_length: int | None
    own_context_length: int | None
    window_source: WindowSource | None
    context: ContextChoice
    efforts: list[Effort]
    default_effort: Effort | None
    free: bool
    learns: bool
    fast_mode: bool


type ProviderProblem = Literal["no_key", "not_installed", "key_refused", "access_refused", "failed"]


class ProviderModels(FrozenModel):
    """The models of one provider, or why it listed none: ``error`` is the message and ``problem`` what kind of failure
    it is (``no_key``: no key is set for it, ``not_installed``: the program it runs on is not installed, OpenCode for
    Zen's free models, ``key_refused``: it answered HTTP 401, ``access_refused``: it answered HTTP 403, ``failed``:
    anything else); both are ``None`` when the provider answered."""

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
    """What macOS has granted to ``program``, the program it holds responsible for the daemon (Dot.app for the
    service), and the Settings panes that grant the rest."""

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
    whether one is started or connected now, whether the Chrome of the user went away since Dot was connected to it
    (``lost``) and whether Dot waits now for its Allow (``waiting_allow``, item 341), and what the files of the Chrome
    of the user say: whether it runs and
    allows remote debugging, and on which port. ``inspect_page`` is where the user switches that on. ``debug_port``
    is the port Dot's own Chrome takes DevTools connections on (item 265; 0 for none), and ``devtools_address`` where
    a run's scripts reach it: set while the main session is Dot's own Chrome and a port is. ``signing_in`` is true
    while Dot's own profile is open in a plain Chrome window that Settings › Browser opened for the owner to sign in to
    sites (item 344), until the owner quits it. ``enabled`` says whether runs are offered the browser tools, and
    ``look`` how pages look (item 354). ``profile`` is the profile Dot's own Chrome works on, of ``profiles`` (item
    355)."""

    mode: BrowserMode
    connected: bool
    lost: bool
    waiting_allow: bool
    signing_in: bool
    chrome_running: bool
    remote_debugging: bool
    port: int | None
    inspect_page: str
    attach_timeout_s: float
    raise_window: bool
    debug_port: int
    devtools_address: str | None
    enabled: bool
    look: PageLook
    profile: str
    profiles: tuple[ProfileView, ...]


class ConnectionView(FrozenModel):
    """One application of Customize › Plugins (items 278 and 316). ``kind`` says how Dot reaches it: ``mac``, it
    drives it with AppleScript; ``web``, it uses it in its own browser; ``folder``, it reads and writes the folder it
    keeps in sync. ``state`` is where it stands and ``step`` what sets it up (``connections/states.py``), ``None``
    when there is none to take; ``waiting`` is true while macOS asks the owner about it on the Mac."""

    id: str
    kind: ConnectionKind
    name: str
    does: str
    state: ConnectionState
    step: ConnectionStep | None
    waiting: bool


class ConnectionsView(FrozenModel):
    """``GET /connections``: the applications of the Mac, then the folders, then the web's, in the catalogue's order.
    ``browser_mode`` and ``headless`` say why a web application offers no sign-in: in attach mode Dot uses the owner's
    own Chrome as it is signed in, and a browser without a window has none to sign in in. ``runner`` is the name of the
    program macOS asks for Dot and lists in Privacy & Security (Dot for the service, item 288)."""

    apps: tuple[ConnectionView, ...]
    browser_mode: BrowserMode
    headless: bool
    runner: str


class PluginsView(FrozenModel):
    """``GET /plugins``: the listings of Customize's Plugins page (item 316), in its order."""

    plugins: tuple[PluginView, ...]


type McpOrigin = Literal["config", "plugin", "elsewhere", "connected"]
type McpServerState = Literal["ready", "left_out", "disabled", "trying", "signing_in"]


class McpServerView(FrozenModel):
    """One MCP server Dot has, as Customize › Plugins shows it (item 279). ``origin`` is where it is set up: Dot's
    config, a plugin, another agent of the Mac (Customize › Skills › Found on this Mac), or the Plugins page itself,
    the only place that removes one. ``address`` is its URL or its command. ``state``: its tools are offered to runs
    (``ready``, with ``tools``, their names on the server), it was left out (``error`` says why), it is switched off
    in the config, or Dot tries it now, waiting for the owner to sign in on ``link`` (``signing_in``). ``signs_in``:
    Dot signs in to it with the owner's account, and Sign in again renews that."""

    name: str
    origin: McpOrigin
    address: str
    service: str | None
    state: McpServerState
    tools: tuple[str, ...]
    error: str | None
    link: str | None
    signs_in: bool


class McpServiceView(FrozenModel):
    """A service of the MCP catalogue (``mcp/catalogue.py``), with whether Dot has its server already."""

    id: str
    name: str
    does: str
    url: str
    signs_in: bool
    added: bool


class McpServersView(FrozenModel):
    """``GET /mcp/servers``: every MCP server Dot has, those of its config first, and the catalogue's services."""

    servers: tuple[McpServerView, ...]
    services: tuple[McpServiceView, ...]


class McpServerAdd(FrozenModel):
    """``POST /mcp/servers``: a service of the catalogue (``service``), or a server by ``name`` and ``address``, a URL
    (over SSE when its path ends in ``/sse``, else streamable HTTP) or a command line. ``origin`` is the page's own
    origin, where the authorization server sends the browser back after a sign-in."""

    service: str | None
    name: str | None
    address: str | None
    origin: str


class McpReconnect(FrozenModel):
    """``POST /mcp/servers/{name}/connect``: the page's own origin, as in :class:`McpServerAdd`."""

    origin: str


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
    # What the pages the daemon serves are (a digest of their files at its start): a page loaded from other pages is out
    # of date, as after an update of Dot.
    pages: str
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
    # What a schedule that leaves them open does (item 257): the earlier runs it knows, and whether it keeps its chat.
    schedule_history_runs: int
    schedule_same_chat: bool
    browser_mode: BrowserMode
    desktop_enabled: bool
    mcp_tools: int
    # The enabled MCP servers that could not start with the daemon, each with the reason; Dot runs without their tools.
    mcp_failures: list[McpFailure]
    models: ModelsConfig
    # Whether the daemon's next start resumes on its own the runs it cut off (item 231).
    resume: ResumeView


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
    is due later today (see ``server.activity``). ``ended_delegated`` are the runs that runs at work delegated and
    that have ended: the pages show them under the run that delegated them, with those at work (item 198)."""

    waiting: list[ActivityRow]
    working: list[ActivityRow]
    ended_delegated: list[ActivityRow]
    recent: list[ActivityRow]
    learned_today: list[ActivityRow]
    due: list[ActivityRow]
    generated_at: float


class PushKeyView(FrozenModel):
    """``GET /push/key``: the ``applicationServerKey`` a page subscribes with."""

    public_key: str


class PushKeysPart(FrozenModel):
    """The keys a push to the subscription is encrypted with; keys that cannot encrypt anything are refused here."""

    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _usable(self) -> Self:
        problem: str | None = keys_problem(self.p256dh, self.auth)
        if problem is not None:
            raise ValueError(problem)
        return self


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
