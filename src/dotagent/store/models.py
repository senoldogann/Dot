"""Row types returned by the store."""

from typing import Literal, Self

from pydantic import Field, model_validator

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Effort, OneMessage, Speed
from dotagent.policy.types import ApprovalMode
from dotagent.vault.cards import Brand

# Which Chrome the browser tools work in: Dot's own, or the one the user has open (attached over its debugging port).
type BrowserMode = Literal["own", "attach"]

RunStatus = Literal["queued", "running", "done", "blocked", "failed", "cancelled"]
# A run in one of these has not ended.
WORKING_STATUSES: frozenset[RunStatus] = frozenset({"queued", "running", "blocked"})
RunMode = Literal["task", "chat", "research"]
# What a message, and a chat's next follow-up, asks for (item 495): a run's mode, or ``auto``, which each message
# settles by its own words (agent/mode_words.py).
ChatMode = Literal["auto", "task", "chat", "research"]
# Whose words a chat's last line in the sidebar is: the owner's message, or the answer to it (item 277).
ChatLineRole = Literal["user", "assistant"]
# How a run was handed a note: at its start, by a memory_search call, or as a note changed while it ran.
MemoryUseHow = Literal["start", "search", "live", "site"]
# ``pending``: a run waits for the answer; ``left``: a question a run left for the user and went on without (answering
# it carries the work on in its thread; it stays until it is answered, and expires when its chat is rewound to before
# its run, when its campaign is deleted, or when the time a kept step has runs out: ``kept_until``).
ApprovalStatus = Literal["pending", "left", "approved", "denied", "expired"]
# The application a draft goes out with (item 314): ``mail`` an email by Mail, ``message`` an iMessage by Messages.
DraftChannel = Literal["mail", "message"]
# ``waiting``: for the owner to send or discard it; ``sending``: handed to the application; ``failed``: the application
# refused it, and the owner may send it again or discard it; ``sent`` and ``discarded`` are final.
DraftStatus = Literal["waiting", "sending", "sent", "discarded", "failed"]
# Where a thing on Dot's own list stands (item 715): to do, being done by a run of its own, done, or let go.
TodoStatus = Literal["open", "doing", "done", "dropped"]
Behavior = Literal["allow", "preapproved", "ask", "handoff"]
# How the end of a schedule's run is told (item 461): every end, only a first failure or an end that needs the owner,
# or nothing.
NotifyLevel = Literal["always", "problems", "never"]
# ``active``: its sessions go on; ``achieved``: the goal checker accepted its evidence, or the owner said so;
# ``stopped``: the owner stopped it (also with the emergency stop of desktop control, which ends its session), or it ran
# the sessions ``[agent] campaign_max_sessions`` allows.
CampaignStatus = Literal["active", "achieved", "stopped"]
CampaignEntryKind = Literal["progress", "setback", "note", "strategy", "question"]
# The reactions the owner can put on a message of Dot's (item 586), by what each means: 👍 good, ❤️ love it, 👎 not right
# (``wrong``), 🔁 do it again, 📌 remember this, ❓ explain, ✋ hold on.
ReactionName = Literal["good", "love", "wrong", "again", "remember", "explain", "hold"]


class ThreadRecord(FrozenModel):
    """A chat's thread. ``team_id`` is the team the chat is with (item 610), ``None`` for any other chat: every run of
    the thread is told that team's context, whoever started it."""

    id: str
    title: str
    source: str
    created_at: float
    updated_at: float
    team_id: str | None


class ChatChoices(FrozenModel):
    """What the next follow-up of a chat starts with, as its composer shows it (item 199): the mode (``auto`` lets each
    message's words settle it, item 495), the model and the reasoning level asked of it, the speed (fast mode, item
    223), the approval mode and goal mode. ``None`` takes the daemon's: its default model, the configured level,
    ``[policy] approvals`` and ``[agent] goal_mode``; a chat (the mode) has no goal mode. A chat's choices are those
    its first run started with, until the owner picks others in its composer."""

    mode: ChatMode
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


class RunPicks(FrozenModel):
    """What the owner picked in the composer for one message (item 318): ``plugins``, the MCP servers whose tools the
    run starts with (by the server's name, a connected app's among them); ``skills``, the skills whose instructions it
    starts with (by name); ``plan_first``, that it writes its plan and asks before it changes anything; ``accounts``,
    the Google and Microsoft accounts signed in whose mail and calendar the run starts with (by address, item 496; the
    picks kept before it name none)."""

    plugins: tuple[str, ...]
    skills: tuple[str, ...]
    plan_first: bool
    accounts: tuple[str, ...] = ()


NO_PICKS: RunPicks = RunPicks(plugins=(), skills=(), plan_first=False, accounts=())


class ChatTeam(FrozenModel):
    """The team a chat is with (item 610), as the chat's row and page name it: ``id`` (the chat's ``actor_id``), the
    team's name and the ids of its agents in order, whose faces the chat wears."""

    id: str
    name: str
    agents: tuple[str, ...]


class ThreadSummary(FrozenModel):
    """One chat of the sidebar: a thread with the runs a person started on it (``run_count`` of them) and the latest of
    those runs, which the chat opens on. ``updated_at`` is the later of the last message and the start of the latest
    run; ``latest_created_at`` is when the latest run was created, which a message during the run does not move (the
    sidebar counts a working chat's time from it). ``pinned_at`` is when the owner pinned the chat, ``None`` for one not
    pinned (item 130). ``unread`` is true when the latest run ended after the owner last saw the chat, or ended and the
    owner marked the chat unread (item 131). ``actor_id`` names whose face the chat wears (item 277): the agent of the
    latest run, else the campaign that run is a session of, else the team the chat is with (``team``, item 610);
    ``None`` for a chat with Dot, which wears a creature of its own. ``last_message`` is the chat's latest words on one
    plain line: the answer of the latest run once it ended with one, else the message that started it; ``last_role``
    says whose they are. ``loop_cron`` is the rhythm of the chat's loop (item 580): of the switched-on repeating
    schedule whose runs go on in the chat (``/loop``, or one a run of it made) that fires next, ``None`` for a chat
    with no such schedule; ``loop_next_at`` is when it fires next. ``campaign_id`` is the campaign whose conversation
    the chat is (the sidebar marks it, as it marks a loop), ``None`` for any other chat. Threads that only hold
    delegated runs are not chats and are never listed."""

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
    actor_id: str | None
    last_message: str
    last_role: ChatLineRole
    loop_cron: str | None
    loop_next_at: float | None
    team: ChatTeam | None
    campaign_id: str | None = None


class RunRecord(FrozenModel):
    """One run. ``effort`` is the reasoning level it was started with (``None`` for runs stored before it was kept);
    ``speed`` is ``fast`` when it was started in fast mode (item 223), which its steps on a model with fast mode use;
    ``once`` is the choice for one message it was started with (item 251; ``effort`` is then the level that choice
    asks for); ``approvals`` and ``goal_mode`` are the approval mode and the goal mode it ran with (runs stored before
    they were kept hold ``standard``, ``None``, ``ask`` and ``False``, which is how those runs behaved). ``campaign_id``
    is the campaign the run is a session of, ``None`` for any other run. ``agent_id`` is the agent the run belongs to
    (item 86), ``None`` for Dot. ``rewindable`` says whether its chat can be rewound to before its first message (item
    282): Dot kept where that message went in the chat's history, which it does not for a delegated run, nor did for
    runs from before. ``harness_version`` is the version of the harness the run took as it started (item 389), ``None``
    when Dot's own text alone was in use and for a run that has not started or is from before; it is how the runs that
    had a version are told from the others (item 588). ``follows_reaction`` is the reaction of the owner's that the run
    follows up (item 844), ``None`` for any other run: the conversation draws no message of the owner's for it, its
    goal being Dot's words for the reaction, which stands as a chip on the message reacted to."""

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
    once: OneMessage | None
    approvals: ApprovalMode
    goal_mode: bool
    campaign_id: str | None
    agent_id: str | None
    rewindable: bool
    harness_version: str | None = None
    follows_reaction: ReactionName | None = None


class RewindCut(FrozenModel):
    """What a rewind of a chat to before one of its runs took out of it (item 282): ``rewound`` is that run and the
    chat's later runs, oldest first, and ``back_to`` the run the chat ends with now (``None`` when none is left)."""

    thread_id: str
    rewound: tuple[RunRecord, ...]
    back_to: str | None


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


class AgentToolUse(FrozenModel):
    """What an agent's newest ended runs did (item 874): how many runs, the tokens they spent, when the newest
    began (``None`` before any ended) and the calls of each tool."""

    runs: int
    tokens: int
    newest: float | None
    calls: dict[str, int]


class AgentRecord(FrozenModel):
    """An agent the owner made (item 86): a name, a role in one line, the instructions that join the system prompt of
    its runs, and the bounds of its runs: ``model`` (empty for the configured one), ``effort`` and ``approvals``
    (``None`` for the configured ones) and ``tools``, the names of the tools it may use (empty for every tool). Its
    face is its ``look`` (item 102), or with ``None`` the shape and colour its id's hue gives it (actors.js).
    ``made_by`` is the run that made it with ``create_agent`` (item 99), ``None`` for one the owner made.
    ``paused_at`` is when the owner paused it (item 465): its schedules start nothing by themselves until it is resumed;
    ``None`` while it works. Dot itself is ``DOT_AGENT``, never stored; its look lives in the kv store under
    ``look:dot``."""

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
    paused_at: float | None


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
    paused_at=None,
)


class TeamRecord(FrozenModel):
    """A team the owner has (item 610): a name, what it does, and its agents, by id, in the order the pages show them. A
    chat with the team (a thread whose ``team_id`` it is) works as the team: Dot leads its agents and hands each its
    part. ``ready_team`` is the ready team (item 504) it was made from, ``None`` for one the owner put together from
    their own agents, so that a second Use of that ready team finds this record. Its agents are the owner's: removing
    the team removes this record and nothing else."""

    id: str
    name: str
    purpose: str
    agents: tuple[str, ...]
    created_at: float
    ready_team: str | None


def chat_team(team: TeamRecord | None) -> ChatTeam | None:
    """The team as a chat's row and page name it (:class:`ChatTeam`); ``None`` for a chat with no team."""
    return None if team is None else ChatTeam(id=team.id, name=team.name, agents=team.agents)


class TradingOrderRecord(FrozenModel):
    """An order of trading (docs/trading.md): ``notional`` is what it was worth in the quote currency (asked, then
    filled), ``size`` the base it bought or sold, ``price`` the average fill and ``fee`` what it paid. ``status`` is
    ``pending`` while it is under way, ``filled``, ``failed`` (it surely did nothing) or ``unknown`` (it was sent and
    no answer said what it did: it counts against the day). ``exchange_id`` is Coinbase's id of a live order."""

    id: str
    mode: Literal["paper", "live"]
    product: str
    side: Literal["buy", "sell"]
    notional: float
    size: float
    price: float
    fee: float
    status: Literal["pending", "filled", "failed", "unknown"]
    ts: float
    run_id: str
    exchange_id: str
    note: str


class TradingExitRecord(FrozenModel):
    """The exit of a buy (item 770): Dot sells ``size`` of ``product`` once the bid falls to ``stop`` or rises to
    ``target``. ``status`` is ``open`` while it is watched (or waits for its account to be in use again), ``stopped``
    or ``targeted`` once its sell filled, ``closed`` once sells by hand took its size or the paper account started
    again, ``failed`` when its sell was refused; ``order_id`` is the buy's, ``sell_id`` the sell's that closed it."""

    id: str
    mode: Literal["paper", "live"]
    product: str
    size: float
    stop: float
    target: float | None
    ts: float
    order_id: str
    run_id: str
    status: Literal["open", "stopped", "targeted", "closed", "failed"]
    closed_ts: float | None
    sell_id: str
    note: str


class EventRecord(FrozenModel):
    id: int
    run_id: str
    ts: float
    kind: str
    data: JsonObject


class ToolStep(FrozenModel):
    """A tool call or its result as the Run tab reads it (item 650): ``kind`` is ``tool_call`` or ``tool_result``;
    a call has its ``args``, a result its ``ok`` and ``result_kind``, and a change of the memory its ``text`` and
    ``summary`` (empty otherwise)."""

    run_id: str
    kind: str
    call_id: str
    tool: str
    args: JsonObject
    ok: bool
    result_kind: str
    text: str
    summary: str


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
    """A question a run asked: of the policy, of the agent itself, or one answered without a person. A step kept for the
    owner (item 482) also has ``digest``, the fingerprint of its action that keeps it once in its chat, and
    ``kept_until``, when it lapses; both are None for any other. The owner's yes to a kept ask (item 483) holds until
    ``permit_until`` and is spent at ``used_at``, the first time the run does the step; None for any other. A kept step
    belongs to the conversation ``scope_thread_id`` and, for the sessions of a campaign, to ``scope_campaign_id``
    (item 484), where it is kept once and its answer holds."""

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
    digest: str | None
    kept_until: float | None
    permit_until: float | None
    used_at: float | None
    scope_thread_id: str | None
    scope_campaign_id: str | None


class TodoRecord(FrozenModel):
    """A thing Dot means to do for the owner on its own (item 715): what (``title``) and why it helps them (``why``),
    who put it on the list (``source``: a run's tool, the owner, the search for work) and the run that did
    (``source_run_id``), whether that run had read content third parties can author (``tainted``, which the run that
    does it starts with), how many runs tried it, the newest of them and what came of it (``note``)."""

    id: str
    title: str
    why: str
    source: str
    source_run_id: str | None
    tainted: bool
    status: TodoStatus
    attempts: int
    run_id: str | None
    note: str
    created_at: float
    updated_at: float


class DraftRecord(FrozenModel):
    """An email or a message a run wrote for the owner (item 314). Nothing goes out until the owner presses Send, and
    then with the recipients and words as they stand on the card; ``error`` says why the last send failed."""

    id: str
    run_id: str
    channel: DraftChannel
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    status: DraftStatus
    created_at: float
    decided_at: float | None
    error: str | None


class ShownPage(FrozenModel):
    """A page of HTML that a run wrote and showed in the conversation (``show_page``). Its id is all it takes to read
    it: the daemon serves the page to whoever names it (``GET /pages/<id>``), so the id is made to be unguessable."""

    id: str
    run_id: str
    title: str
    html: str
    created_at: float


# What an item of Dot's own vault is (item 621).
VaultKind = Literal["login", "card", "contact"]


class VaultRecord(FrozenModel):
    """An item of Dot's own vault as the database keeps it (item 621): what a list shows and a run is told, never a
    secret (those are in the Keychain, :mod:`dotagent.vault.secretstore`). A login has a website and a username, a
    card a brand, its last four digits and a nickname, a contact its ``fields`` (``name``, ``address``, ``address2``,
    ``city``, ``region``, ``postal_code``, ``country``, ``phone``, ``email``); a card's ``fields`` are its billing
    address, if it has one."""

    id: str
    kind: VaultKind
    label: str
    website: str | None = None
    username: str | None = None
    brand: Brand | None = None
    last4: str | None = None
    nickname: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class RuleRecord(FrozenModel):
    id: str
    position: int
    enabled: bool
    behavior: Behavior
    tool_glob: str
    match_regex: str
    description: str
    created_at: float


class FollowUpOf(FrozenModel):
    """What the follow-up run that a reaction started or queued follows up (item 586): the reaction ``reaction`` of the
    owner on the message ``message`` of the run ``run_id``, and ``context``, what the run reads under the owner's
    words: that message, quoted as Dot's own earlier text. The owner's words (the run's goal) never hold the quote,
    since what a reviewer takes as the owner's instruction must be theirs alone; the run starts as untrusted as the run
    of the message, or the chat's last, ended (:meth:`RunManager._carried_by`)."""

    run_id: str
    message: str
    reaction: ReactionName
    context: str


class QueuedMessage(FrozenModel):
    """A message sent to a chat while a run worked there: it waits in the chat's queue, in ``position`` order, and
    starts as the chat's next run, with the choices it was sent with, once the run before it has ended done (see
    ``RunManager.queue``); ``once`` is one of them, for this message only (item 251), and so are ``picks`` (item
    318). ``attachments`` are the ids of its uploaded files; ``source`` is where it was sent from, and the source of the
    run it starts. ``follow_up`` is set for the follow-up of a reaction (item 586), which starts with the context it
    carries."""

    id: str
    thread_id: str
    position: int
    text: str
    attachments: tuple[str, ...]
    mode: RunMode
    model: str | None
    effort: Effort | None
    speed: Speed
    once: OneMessage | None
    picks: RunPicks
    approvals: ApprovalMode | None
    goal_mode: bool | None
    agent_id: str | None
    source: str
    created_at: float
    follow_up: FollowUpOf | None = None


class ReactionRecord(FrozenModel):
    """A reaction of the owner on a message of Dot's that stands now (item 586). ``message`` is ``answer`` for the
    answer a run ended with, or ``step-<n>`` for what it said in its step ``n``; ``thread_id`` is the chat of the run;
    ``follow_up_run_id`` is the run that the reaction started as a follow-up, once it has started (none while it waits
    in the chat's queue, and for a reaction that started none); ``note_id`` is the note a 📌 made, none for one that
    found the note there already."""

    run_id: str
    message: str
    reaction: ReactionName
    thread_id: str
    created_at: float
    follow_up_run_id: str | None
    note_id: str | None


class MergedSource(FrozenModel):
    """A note a merged note was made of, as it was when it was merged (item 584): where it came from and when, so that
    the merged note keeps every source's provenance whatever becomes of the source."""

    id: str
    source_run: str | None
    agent_id: str | None
    created_at: float
    updated_at: float
    last_used_at: float | None
    used_count: int
    tags: tuple[str, ...]
    tainted: bool


class MemoryRecord(FrozenModel):
    """One note. ``source_run`` is the run that saved it, ``None`` for a note the user wrote; ``agent_id`` is the
    agent whose lesson it is (item 94), ``None`` for Dot's, which every run may be handed; ``used_count`` and
    ``last_used_at`` say how often and when a run was handed it. ``expires_at`` is when a note saved for a while stops
    holding (item 306), ``None`` for one that holds until it is changed; ``replaced_by`` is the newer note that made it
    outdated, when the reflection put it aside for that (item 306). ``tainted`` says the note was written while the run
    that saved it had read content third parties can author: a search that returns it taints the run that read it.
    The fields with a default are NULL in a note written before them (item 584): ``merged_from`` names the notes a
    merged note was made of, ``merged_into`` the merged note a resting one was merged into, ``pinned_at`` is when the
    owner pinned the note (it never rests) and ``rested_at`` when it began to rest."""

    id: str
    content: str
    tags: tuple[str, ...]
    created_at: float
    updated_at: float
    source_run: str | None
    agent_id: str | None
    used_count: int
    last_used_at: float | None
    expires_at: float | None
    replaced_by: str | None
    tainted: bool
    merged_into: str | None = None
    merged_from: tuple[MergedSource, ...] = ()
    pinned_at: float | None = None
    rested_at: float | None = None


class ResultStep(FrozenModel):
    """What a step of a run said of itself when it ended (item 585): the tool, the kind of its action, what it acted on
    (a path, a command, an address, as the run's record shows it), the flags its action raised and, when the call's
    arguments named something Dot guards or loads (an MCP call's target is its server, so the executor reads its
    arguments where it classifies it), what they named: ``""`` for a step that named nothing, or that an older Dot
    recorded. ``helper`` says the step is one of a run the run delegated (the steps of a family are read together)."""

    tool: str
    kind: str
    target: str
    flags: tuple[str, ...]
    names_dot: str = ""
    helper: bool = False


class TrashedMemory(FrozenModel):
    """A note the owner forgot, kept whole for a while so that it can come back (item 584): the note as it was and
    when it went to the trash."""

    record: MemoryRecord
    trashed_at: float


# How the list of notes is ordered (item 584): by what changed last, by the last use, by the most or the fewest uses.
type MemoryOrder = Literal["changed", "used", "most_used", "least_used"]


class AwakeNotes(FrozenModel):
    """Which notes a search may find, asked of the store so that the cut to the best ones is made among them (item 557):
    the notes not tagged ``rest_tag``, whose time has not run out at ``now``, and, with ``scoped``, the ones a run of
    ``agent_id`` may be handed (Dot's, and that agent's own)."""

    now: float
    rest_tag: str
    scoped: bool
    agent_id: str | None


class MemoryScore(FrozenModel):
    """How the runs a note was handed to ended: ``handed`` runs in all, of which ``done`` ended done and ``failed``
    failed (the rest were cancelled, or still work)."""

    handed: int
    done: int
    failed: int


class ScheduleOptions(FrozenModel):
    """How the runs of a schedule are started. ``None`` takes what the configuration says when the schedule fires:
    the main model, its default reasoning level, the standard speed, the configured approvals, no goal mode, the
    configured time zone, ``[scheduler] history_runs`` earlier runs' answers in front of the goal and ``[scheduler]
    same_chat`` (item 257), and ``[scheduler] stop_after_failures`` for how many failed runs in a row switch a
    repeating schedule off (item 458), and ``[scheduler] notify`` for how its runs' ends are told (item 461). A
    schedule a run makes keeps that run's choices and its chat (item 291). ``agent_id`` names the owner's agent its runs
    run as, within that agent's bounds (item 462), and ``None`` is Dot, as every schedule was before."""

    model: str | None
    effort: Effort | None
    speed: Speed | None
    approvals: ApprovalMode | None
    goal_mode: bool | None
    timezone: str | None
    history_runs: int | None
    same_chat: bool | None
    stop_after_failures: int | None
    notify: NotifyLevel | None
    agent_id: str | None
    # The skills its runs start with, as a message's picks of skills do (item 666): a task the owner taught Dot and
    # asked it to repeat. None for every schedule before.
    skills: tuple[str, ...] = ()


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
    # How many of its runs in a row ended failed (item 458): a run that ends done sets it to 0, one cancelled leaves it,
    # and switching the schedule on again or changing it starts it afresh.
    runs_failed_in_row: int


class StepRow(FrozenModel):
    """A step of a past run as a rule's try-out reads it (item 469): the ``tool_result`` event's tool, the kind and risk
    the policy gave its action, its summary and target, whether it succeeded, and what the policy decided of it (raw, as
    the event keeps it). ``recorded`` says whether the event has that field at all: written since item 468, it is
    ``None`` for a step the gate did not let through or that never reached it; ``stopped_by`` is then the source,
    verdict and taint (item 474) of the ``policy`` event of its call, when the gate emitted one. ``flags`` are those of
    its action (kept since item 468), ``tainted_before`` whether its run was tainted before the step's result, and
    ``yolo`` whether its run asked nothing (item 443)."""

    event_id: int
    run_id: str
    at: float
    tool: str
    kind: str
    risk: str
    summary: str
    target: str
    ok: bool
    decision: JsonObject | None
    recorded: bool
    stopped_by: JsonObject | None
    flags: tuple[str, ...]
    tainted_before: bool
    yolo: bool


class ScheduleFiring(FrozenModel):
    """A run a schedule started, at its time or by hand (item 455), with the title of the chat it went in, which says
    which of the two it was."""

    run: RunRecord
    chat_title: str


# Who decided an approval for the owner without asking them (item 443's records): the review model, or yolo.
type AutoDecider = Literal["reviewer", "yolo"]


class UnattendedRun(FrozenModel):
    """A run that a schedule or a campaign started (item 466), with the name of the schedule whose chat it went in and
    the title of the campaign it was a session of; a firing from before item 257 names no schedule."""

    run: RunRecord
    schedule_name: str | None
    campaign_title: str | None


class UnattendedCount(FrozenModel):
    """How many of the runs that schedules and campaigns started ended with ``status``, or work (item 466)."""

    status: RunStatus
    campaign: bool
    count: int


class AutoDecisionCount(FrozenModel):
    """How many approvals ``decided_by`` answered ``status`` for the owner (item 466) in the run ``run``, which a
    schedule or a campaign started, and in its helpers; with what names the run, as :class:`UnattendedRun` has it."""

    run: UnattendedRun
    decided_by: AutoDecider
    status: Literal["approved", "denied"]
    count: int


class CampaignRecord(FrozenModel):
    """A goal that Dot works on in sessions, through restarts and days, until its outcome is verified or the owner
    stops it. ``thread_id`` is the owner's conversation it was started in. ``targets`` and ``verification`` are what
    its sessions recorded with ``campaign_targets`` (empty until then). ``cron`` repeats its sessions;
    ``next_session_at`` is when the next one starts, ``None`` while none is planned. ``model``, ``effort``, ``speed``,
    ``once`` and ``approvals`` are the settings of the run that opened it, which every session keeps. ``files`` are the
    paths of the files the owner attached to its goal. ``evidence`` is what the accepted claim of the outcome said,
    empty while the campaign is not achieved. ``browser`` is the Chrome its sessions work in: Dot's own (``own``) or the
    one the owner has open (``attach``); ``None`` for a campaign started without one (item 579) or from before the
    choice, whose sessions use the daemon's of the moment."""

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
    once: OneMessage | None
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


type HarnessState = Literal["candidate", "probed", "adopted", "retired"]


class HarnessVersion(FrozenModel):
    """One version of the harness (item 389, docs/harness.md): the texts a run's system prompt takes (``habits`` after
    how to work, ``tool_guidance`` after the tools; an empty one adds nothing), who made it (``made_by``: ``owner``, or
    the name of the improvement loop), the version it was made from (``parent_id``, ``None`` for one made from Dot's own
    text alone), its ``state`` (a ``candidate`` nobody tried, one ``probed`` by the loop, the one ``adopted``, which
    every run takes, or one ``retired``), what it is in a line (``summary``), and the evidence and decisions behind it
    (``notes``, empty for one of the owner's). ``adopted_at`` and ``retired_at`` are when it last was; a version adopted
    again after it was retired has no ``retired_at``."""

    id: str
    parent_id: str | None
    made_by: str
    state: HarnessState
    summary: str
    habits: str
    tool_guidance: str
    notes: str
    created_at: float
    adopted_at: float | None
    retired_at: float | None


type HarnessStepState = Literal["done", "keep", "skipped", "failed", "calibrating"]


class HarnessStep(FrozenModel):
    """One step of the improvement loop (item 391, docs/harness.md): the run it learned from (``from_run``, ``None``
    when none was there to learn from), the version the probes compared against (``base_version``, ``None`` for Dot's
    own text alone), how it ended (``state``: ``done`` with a probed version written, ``keep`` with the base kept,
    ``skipped`` with nothing to learn from, ``failed``), what it did as JSON (``outcome``: the candidates, the probes'
    contrasts, the decision, the probed version) and ``error`` when it failed."""

    id: str
    started_at: float
    ended_at: float | None
    from_run: str | None
    base_version: str | None
    state: HarnessStepState
    outcome: str
    error: str | None


class HarnessProbe(FrozenModel):
    """One attempt of a probe (item 391): the step it was part of, the version played (``None`` for Dot's own text
    alone) and its ``label``, the task and the attempt, and what the attempt came to: whether it passed, how the run
    ended, its steps, tokens (the run's and those of the runs it delegated to), tool errors and seconds, the approvals
    its run asked (every one declined: nobody is there), and how many of the task's checks held of how many (item 589;
    none of the three is counted for an attempt recorded before)."""

    id: int
    step_id: str
    version_id: str | None
    label: str
    task_id: str
    attempt: int
    passed: bool
    status: RunStatus
    cut: str
    steps: int
    input_tokens: int
    output_tokens: int
    tool_errors: int
    seconds: float
    created_at: float
    approvals_asked: int = 0
    checks_passed: int = 0
    checks_total: int = 0


class HarnessPruned(FrozenModel):
    """What :meth:`dotagent.store.db.Store.prune_harness` deleted of the loop's own records (item 589): versions put
    aside, steps, the attempts of their probes and experiences."""

    versions: int
    steps: int
    probes: int
    experiences: int


class HarnessExperience(FrozenModel):
    """An experience the loop kept (item 391): the shape of situation it applies to (``cue``), the lesson, the
    evidence it rests on, how sure the writer was, the step and version it came from, and whether the run it was
    written from had read content third parties can author (``tainted``, item 392), which keeps it from the proposer."""

    id: str
    step_id: str
    version_id: str | None
    cue: str
    lesson: str
    evidence: str
    confidence: str
    created_at: float
    tainted: bool
