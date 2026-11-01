"""Typed configuration: the packaged ``defaults.toml`` deep-merged with the user's ``config.toml``."""

import ipaddress
import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import AfterValidator, Field, JsonValue, ValidationError, field_validator, model_validator

from dotagent.core.errors import ConfigError
from dotagent.core.learned_text import LINE_BREAKS, hidden_character
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import KNOWN_PROVIDERS, Effort, split_model_ref
from dotagent.policy.types import ApprovalMode, Preset, QuestionMode
from dotagent.store.models import BrowserMode, NotifyLevel
from dotagent.web.ladder import SearchEngine


def _service_address(value: str) -> str:
    """An address Dot sends requests to, as its HTTP client reads it: ``http://`` or ``https://`` with a host."""
    try:
        url: httpx.URL = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError(f"the address cannot be read: {exc}") from exc
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError("the address must start with http:// or https:// and name a host")
    return value


def _model_reference(value: str) -> str:
    """A model as ``<provider>:<model>``, the provider one Dot knows (:func:`dotagent.llm.types.split_model_ref`)."""
    if split_model_ref(value) is None:
        raise ValueError(
            f"a model must look like '<provider>:<model>', the provider one of {', '.join(KNOWN_PROVIDERS)}"
        )
    return value


def _spend_site(value: str) -> str:
    """A site of ``[policy.spend]`` as the address of a payment step is matched to it
    (:func:`dotagent.policy.spend.spend_host`): a host in lower case without ``www.``, with no scheme, port or path."""
    if value.startswith("www.") or urlsplit(f"https://{value}/").hostname != value:
        raise ValueError("a site must be a bare host in lower case, without www., such as example.com")
    return value


# The lowest port Dot's own Chrome may take DevTools connections on (item 265): the ports below are the well-known ones
# of the system's services.
DEBUG_PORT_MIN: int = 1024


def _debug_port(value: int) -> int:
    """A DevTools port of ``[browser] debug_port`` (item 265): 0 for none, else one from :data:`DEBUG_PORT_MIN` up."""
    if 0 < value < DEBUG_PORT_MIN:
        raise ValueError(f"a DevTools port is 0 for none or from {DEBUG_PORT_MIN} to 65535")
    return value


# A profile of Dot's own Chrome (item 355): "default", "incognito", or the name of one the owner added, which names its
# folder.
_PROFILE_NAME: re.Pattern[str] = re.compile(r"^[^\W_][\w -]{0,47}$")
_RESERVED_PROFILES: frozenset[str] = frozenset({"default", "incognito"})


def _profile_name(value: str) -> str:
    """A profile of ``[browser] profile``: up to 48 letters (of any script), digits, spaces, dashes and underscores,
    from a letter or a digit, so that it names a folder of its own and nothing else."""
    if _PROFILE_NAME.fullmatch(value) is None or value != value.strip():
        raise ValueError("a profile's name is up to 48 letters, digits, spaces, - and _, from a letter or a digit")
    # Default and Incognito are named in one way only, so that "Incognito" is never a folder that keeps what it got.
    if value.casefold() in _RESERVED_PROFILES and value not in _RESERVED_PROFILES:
        raise ValueError("the two profiles every Dot has are written default and incognito")
    return value


# The smallest context size a model's runs may be given (item 250, [models] context): the budget every run had before
# 2026-10-11. A run's first step alone sends its instructions, its tools and the request (29,294 tokens at the median
# of 48 runs, measured 2026-10-15), so a smaller budget would summarise a run after its first few steps.
CONTEXT_FLOOR: int = 48_000


def _context_tokens(value: int | Literal["most"]) -> int | Literal["most"]:
    if isinstance(value, int) and value <= 0:
        raise ValueError('context_tokens is "most" or a number of tokens above 0')
    return value


type ServiceAddress = Annotated[str, AfterValidator(_service_address)]
type ModelReference = Annotated[str, AfterValidator(_model_reference)]
type SpendSite = Annotated[str, AfterValidator(_spend_site)]
type DebugPort = Annotated[int, Field(ge=0, le=65535), AfterValidator(_debug_port)]
type BrowserProfileName = Annotated[str, AfterValidator(_profile_name)]
type ContextSize = Annotated[int, Field(ge=CONTEXT_FLOOR)]
# [agent] context_tokens: "most" of each model's window (item 396), or one number of tokens for every model.
type ContextTokens = Annotated[int | Literal["most"], AfterValidator(_context_tokens)]
# The colour scheme the pages of Dot's browser are told to prefer: the Mac's, or one of the two whatever the Mac shows.
type PageAppearance = Literal["system", "light", "dark"]


class ServerConfig(FrozenModel):
    """``host`` and ``port`` are where the daemon listens, a loopback address only. ``reach`` names the hosts a reverse
    proxy on the owner's own network presents the daemon as (``tailscale serve`` gives ``<mac>.<tailnet>.ts.net``); a
    request with such a ``Host``, and an ``Origin`` of ``https://`` that host, is served like a local one."""

    host: str
    port: int = Field(ge=1, le=65535)
    reach: list[str]
    # Whether the daemon keeps the Mac awake while it charges (item 271), for a phone that reaches it from away.
    keep_awake: bool


class StoreConfig(FrozenModel):
    """What ``dotx prune --apply`` deletes of the database (``[store]``): the events of the runs that ended more than
    ``retention_days`` days ago, and with them the screenshots only those events name. Nothing of the database is
    deleted by itself; the daemon thins only its own copies of it.
    The copies of the database (item 526): made every ``backup_every_hours`` hours while the daemon runs (0: none),
    kept in ``backup_dir`` (``<home>/backups`` when empty), the newest ``backup_keep_hourly`` and one of each of the
    last ``backup_keep_days`` days."""

    retention_days: int = Field(ge=1)
    backup_every_hours: float = Field(ge=0, le=24 * 7, allow_inf_nan=False)
    backup_dir: str
    backup_keep_hourly: int = Field(ge=1, le=24 * 14)
    backup_keep_days: int = Field(ge=0, le=365)

    @field_validator("backup_dir")
    @classmethod
    def _absolute_backup_dir(cls, value: str) -> str:
        # A relative folder would be under the daemon's working directory, / for a launchd job.
        if not value:
            return value
        try:
            folder: Path = Path(value).expanduser()
        except RuntimeError as error:
            # A ~user that is not there: a config error, as any other.
            raise ValueError(f"backup_dir names a home that is not there: {value!r} ({error})") from error
        if not folder.is_absolute():
            raise ValueError(f"backup_dir must be empty, absolute or start with ~: {value!r}")
        return value


class OllamaConfig(FrozenModel):
    base_url: ServiceAddress
    api_key_env: str
    num_ctx: int = Field(ge=1)
    num_ctx_cloud: int = Field(ge=1)
    keep_alive: str
    connect_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    max_concurrent: int = Field(ge=1)


type GoRoute = Literal["chat", "responses", "messages"]


class OpenAIConfig(FrozenModel):
    base_url: ServiceAddress
    api_key_env: str
    organization_env: str
    project_env: str
    wire: Literal["responses", "chat"]
    connect_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    max_concurrent: int = Field(ge=1)


class OpenCodeGoConfig(FrozenModel):
    base_url: ServiceAddress
    api_key_env: str
    route_overrides: dict[str, GoRoute]
    connect_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    max_concurrent: int = Field(ge=1)


class OpenCodeConfig(FrozenModel):
    """OpenCode's own agent, the one client Zen's free models serve (item 283), which Dot starts as a local server of
    its own when a run needs it. ``command`` is the ``opencode`` program: a path, or a name looked up on the login
    shell's PATH and in Homebrew's folders. The server stops once it has served nothing for ``idle_stop_s``; the free
    models OpenCode lists are kept for ``models_ttl_s`` without asking it again."""

    command: str = Field(min_length=1)
    start_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    idle_stop_s: float = Field(gt=0, allow_inf_nan=False)
    models_ttl_s: float = Field(gt=0, allow_inf_nan=False)


class AnthropicConfig(FrozenModel):
    base_url: ServiceAddress
    api_key_env: str
    connect_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    max_concurrent: int = Field(ge=1)


class OpenRouterMaxPrice(FrozenModel):
    """The most a request may cost at the provider OpenRouter routes it to, in dollars per million tokens."""

    prompt: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    completion: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class OpenRouterRouting(FrozenModel):
    """How OpenRouter chooses the provider of one model (its ``provider`` object): each field left out is OpenRouter's
    own default. See https://openrouter.ai/docs/guides/routing/provider-selection."""

    sort: Literal["price", "throughput", "latency"] | None = None
    order: list[str] | None = None
    only: list[str] | None = None
    ignore: list[str] | None = None
    allow_fallbacks: bool | None = None
    quantizations: list[str] | None = None
    max_price: OpenRouterMaxPrice | None = None


class OpenRouterConfig(FrozenModel):
    """OpenRouter (item 349): the models of many labs on one key. Its list of its models, which says what each model
    can do, is kept for ``models_ttl_s`` without asking again. ``routing`` is how OpenRouter chooses the provider of a
    model, by its id; a variant (``model:nitro``) takes its model's (item 737)."""

    base_url: ServiceAddress
    api_key_env: str
    routing: dict[str, OpenRouterRouting]
    models_ttl_s: float = Field(gt=0, allow_inf_nan=False)
    connect_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    attempts: int = Field(ge=1)
    max_concurrent: int = Field(ge=1)


class ProvidersConfig(FrozenModel):
    ollama: OllamaConfig
    openai: OpenAIConfig
    opencode_go: OpenCodeGoConfig
    anthropic: AnthropicConfig
    openrouter: OpenRouterConfig
    opencode: OpenCodeConfig


class ModelsConfig(FrozenModel):
    main: ModelReference
    vision: ModelReference
    fast: ModelReference
    review: ModelReference
    # The Anthropic model that relays a web search through the API's own search tool (item 69); "anthropic:" only.
    search: ModelReference
    effort: Effort
    helpers: list[ModelReference]
    standby: list[ModelReference]
    # At most a day, the longest pause of the router (dotagent.llm.router.MAX_PAUSE_S).
    helper_cooldown_s: float = Field(gt=0, le=86_400, allow_inf_nan=False)
    # The context size of a model's runs where it is not [agent] context_tokens (item 250); Settings › Models sets it.
    context: dict[ModelReference, ContextSize]

    @field_validator("search")
    @classmethod
    def _an_anthropic_model(cls, value: str) -> str:
        split: tuple[str, str] | None = split_model_ref(value)
        if split is not None and split[0] != "anthropic":
            raise ValueError("search must name an Anthropic model (anthropic:<model>)")
        return value


type Tier = Literal["standard", "weak"]
type ScaffoldMode = Literal["fixed", "measured"]
type ToolSurface = Literal["full", "grouped", "auto"]
type PromptStyle = Literal["full", "lean"]
type ResumeMode = Literal["off", "safe"]
type LessonsMode = Literal["off", "propose", "auto"]
# A time of day as [agent] reflection_time takes it: 24-hour, two digits each.
_TIME_OF_DAY: re.Pattern[str] = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class AgentConfig(FrozenModel):
    goal_mode: bool
    # How often the checker may send a run back to work; 0 ends the run at its first refusal.
    goal_patience: int = Field(ge=0)
    run_max_minutes: float = Field(ge=0, allow_inf_nan=False)
    run_max_tokens: int = Field(ge=0)
    campaign_gap_minutes: float = Field(gt=0, allow_inf_nan=False)
    campaign_wait_hours: float = Field(ge=0, allow_inf_nan=False)
    campaign_patience_sessions: int = Field(ge=1)
    campaign_max_sessions: int = Field(ge=0)
    # A campaign whose sessions failed this many times in a row stops, saying so (item 724); 0: never.
    campaign_failed_sessions: int = Field(ge=0)
    # Read by nothing since item 579, which has a campaign follow [browser] mode; kept so that an older config.toml
    # that sets it still loads.
    campaign_browser: BrowserMode
    consult_max_per_run: int = Field(ge=0)
    # At least the floor a run's tool output keeps however small the model's window (dotagent.agent.scaffold).
    tool_output_chars: int = Field(ge=2000)
    # The context size of every model without one of its own in [models.context] (dotagent.agent.scaffold).
    context_tokens: ContextTokens
    # While context_tokens is "most": the most a model gets without a size of its own (item 732); 0 leaves it the most
    # its window allows.
    context_most_tokens: int = Field(ge=0)
    # The harness lessons of arXiv 2609.20804 (item 87): past context_elide_share of the budget older tool output is
    # shortened, past context_summarise_share the older part of the run is summarised; plan_each_turn hands the open
    # plan as a note after the history when it changes and after 20 steps without a change (item 825).
    context_elide_share: float = Field(gt=0, le=1, allow_inf_nan=False)
    context_summarise_share: float = Field(gt=0, le=1, allow_inf_nan=False)
    plan_each_turn: bool
    keep_recent_tool_results: int = Field(ge=0)
    keep_recent_screenshots: int = Field(ge=0)
    # The progress watchdog's limits (dotagent.agent.progress): one repeat or one revisit is every step, so 2 at least.
    stall_limit: int = Field(ge=2)
    stall_escalation: bool
    failure_streak: int = Field(ge=1)
    revisit_window: int = Field(ge=2)
    # Every this many acting steps of a run, the review model judges whether it is still on track (item 909); 0: never.
    checkpoint_steps: int = Field(ge=0)
    finish_nudges: int = Field(ge=0)
    questions: QuestionMode
    parallel_tools: int = Field(ge=1)
    model_call_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    connection_check_s: float = Field(ge=0, allow_inf_nan=False)
    model_wait_s: float = Field(ge=0, allow_inf_nan=False)
    quota_wait_s: float = Field(ge=0, allow_inf_nan=False)
    max_concurrent_runs: int = Field(ge=1)
    scaffold: ScaffoldMode
    tiers: dict[str, Tier]
    profile_min_calls: int
    weak_refusal_share: float
    reminder_every: int = Field(ge=1)
    steer_single_actions: bool
    tool_surface: ToolSurface
    # How much the system prompt says (item 280): "lean" keeps its rules and gives the tools' habits in a line each.
    prompt: PromptStyle
    # Whether the line of an MCP server's tool group in the prompt says what the server is for (item 659); false leaves
    # the line as it was, the server's name and its tools' names.
    mcp_purposes: bool
    verify_answer_paths: bool
    resume: ResumeMode
    resume_within_s: int
    lessons: LessonsMode
    # When the nightly reflection runs (HH:MM in [scheduler] timezone, or "off"), and after how many days without a
    # run being handed it a lesson rests.
    reflection_time: str
    lesson_rest_days: int = Field(ge=1)
    playbooks: bool
    # The acting steps a done task run must have taken before it is distilled into a learned skill.
    skill_min_steps: int = Field(ge=1)
    # After how many days that no run read a learned skill it rests (item 585): not offered to a run, shown as resting.
    skill_rest_days: int = Field(ge=1)
    # How close (cosine of the embeddings of their kinds) two learned skills must be for the review model to be asked
    # whether they are one procedure, and the most merges it proposes to the owner in a night (0 for none).
    skill_merge_similarity: float = Field(gt=0, le=1)
    skill_proposals_per_night: int = Field(ge=0)
    undo_max_file_bytes: int
    undo_max_run_bytes: int
    undo_keep_days: int
    step_router: bool
    step_router_threshold: float = Field(ge=0, le=1)

    @field_validator("reflection_time")
    @classmethod
    def _time_of_day_or_off(cls, value: str) -> str:
        if value != "off" and _TIME_OF_DAY.fullmatch(value) is None:
            raise ValueError('reflection_time is a time of day as "03:30" (24-hour) or "off"')
        return value


class PaceConfig(FrozenModel):
    """How many public and social browser actions a site gets in an hour and in a day, and how far apart public ones
    are (:mod:`dotagent.policy.pace`); 0 removes a limit."""

    public_per_hour: int = Field(ge=0)
    public_per_day: int = Field(ge=0)
    social_per_hour: int = Field(ge=0)
    social_per_day: int = Field(ge=0)
    public_spacing_s: float = Field(ge=0, le=3600)


class SpendCap(FrozenModel):
    """The caps of a site in ``[policy.spend]``: the most one purchase may be, and the most a day may add up to
    (:mod:`dotagent.policy.spend`). Plain numbers, compared with the number on the page whatever its currency."""

    per_purchase: float = Field(gt=0, allow_inf_nan=False)
    per_day: float = Field(gt=0, allow_inf_nan=False)


# What a question of the policy that nobody answers in time comes to (item 482): a refusal, as it always was, or, for a
# step the run hands to the owner, a step kept for them while the run goes on.
type Unanswered = Literal["refuse", "park"]


class PolicyConfig(FrozenModel):
    """``code_sources`` lists the applications ``code_fetch`` may read one-time codes from (``mail``, ``messages``);
    empty, the tool does not exist. ``spend`` maps a site (its host without ``www.``) to its pre-approved caps.
    ``unanswered`` is what a question nobody answers within ``approval_timeout_s`` comes to, ``parked_hours`` how
    long a step kept for the owner waits before it lapses (item 482), and ``permit_hours`` how long their yes to a kept
    step holds once they gave it (item 483). ``yolo_taint_check``: a yolo run that read outside content has the review
    model check the steps an instruction planted there is after (item 525). ``protected_paths`` are the locations the
    shell guard and the search tools keep a run from (the credentials programs read there are the commands' own to
    read: ssh, git, a cloud CLI); ``sealed_paths`` the stores no program a run starts has any use for (the data of the
    browsers, Mail and Messages), which the sandbox closes to a command too, whatever path, variable or substitution
    names them (item 531): the guard keeps them from a run by name as well, as it does ``protected_paths``."""

    preset: Preset
    approvals: ApprovalMode
    yolo_taint_check: bool
    approval_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    unanswered: Unanswered
    parked_hours: float = Field(gt=0, le=24 * 30, allow_inf_nan=False)
    permit_hours: float = Field(gt=0, le=24 * 30, allow_inf_nan=False)
    protected_paths: list[str]
    sealed_paths: list[str]
    code_sources: list[Literal["mail", "messages"]]
    spend: dict[SpendSite, SpendCap]
    pace: PaceConfig

    @property
    def refused_paths(self) -> list[str]:
        """Every location the shell guard and the search tools keep a run from: ``protected_paths`` and, besides them,
        ``sealed_paths`` (item 531)."""
        return [*self.protected_paths, *(path for path in self.sealed_paths if path not in self.protected_paths)]


class BrowserConfig(FrozenModel):
    # Whether runs are offered the browser tools at all (item 354); an agent's own tools only narrow what is offered.
    enabled: bool
    mode: BrowserMode
    attach_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    channel: str
    headless: bool
    # Whether Dot's Chrome is brought to the front while a run works (item 97); Take over brings it either way.
    raise_window: bool
    # Whether the owner may use a held run's page from the screen that shows it, a phone's too (item 470).
    remote_input: bool
    # The port of this Mac on which Dot's own Chrome also takes DevTools connections, from a run's scripts (item 265);
    # 0 for none.
    debug_port: DebugPort
    # The profile Dot's own Chrome works on (item 355): "default", "incognito" or one the owner added.
    profile: BrowserProfileName
    # Whether a page of Dot's own Chrome fills its window instead of having the size below (item 354).
    fit_window: bool
    # A page Chrome can open: from a small phone's to a 4K screen's.
    viewport_width: int = Field(ge=320, le=3840)
    viewport_height: int = Field(ge=240, le=2160)
    appearance: PageAppearance
    # How large Dot's own Chrome draws pages, in percent of their size (item 358): Chrome's default zoom.
    zoom: int = Field(ge=25, le=500)
    navigation_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    action_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    snapshot_max_chars: int
    viewport_margin_px: int = Field(ge=0)
    settle_quiet_ms: int = Field(ge=0)
    settle_max_ms: int = Field(ge=0)
    navigation_attempts: int = Field(ge=1)
    navigation_backoff_ms: int = Field(ge=0)
    max_tabs: int = Field(ge=1)
    tab_idle_s: float = Field(gt=0, allow_inf_nan=False)
    # A tab of a chat that is not its current one closes once unused this long (item 696); 0 keeps it.
    background_tab_idle_s: float = Field(ge=0, allow_inf_nan=False)
    # Dot's own Chrome closes once unused this long (item 454); 0 keeps it open until the daemon stops.
    idle_close_s: float = Field(ge=0, allow_inf_nan=False)
    # The JPEG quality Chrome takes a screenshot with.
    screenshot_quality: int = Field(ge=1, le=100)
    max_batch_steps: int = Field(ge=1)
    read_text_chars: int
    allowed_hosts: list[str]
    blocked_hosts: list[str]
    decline_cookie_banners: bool


class DesktopConfig(FrozenModel):
    enabled: bool
    screenshot_max_edge: int = Field(ge=1)
    jpeg_quality: int = Field(ge=1, le=100)
    settle_quiet_ms: int = Field(ge=0)
    settle_ms: int = Field(ge=0)
    failsafe_corner: bool
    ax_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    ax_budget_s: float = Field(gt=0, allow_inf_nan=False)
    ax_max_nodes: int = Field(ge=1)
    ax_max_depth: int = Field(ge=1)
    snapshot_max_chars: int
    capture_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    paste_settle_ms: int = Field(ge=0)
    paste_threshold_chars: int
    keep_awake: bool
    keep_awake_s: int
    max_batch_steps: int = Field(ge=1)
    # Whether a screenshot comes with the text on it, read on this Mac, and where each line is (item 701).
    read_screen_text: bool
    # Whether a run that acted on the desktop gives the front back to the application that had it (item 702).
    give_front_back: bool
    # Whether Dot's own pointer shows where the steps of desktop_act go (item 706).
    show_pointer: bool
    launch_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    activate_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    osascript_timeout_s: float = Field(gt=0, allow_inf_nan=False)


type SnapshotSound = Literal["click", "pop", "glass", "off"]


# A name a person reads and the prompt says: no control character, so that it stays one line (item 420).
SHOWN_NAME: str = r"^[^\x00-\x1f\x7f]*$"


class AssistantConfig(FrozenModel):
    """Who Dot is to its owner (item 420): the name the owner calls it by, the owner's own name (empty: the first
    name of their account on this Mac), and whether Dot has had its first conversation."""

    name: str = Field(min_length=1, max_length=24, pattern=SHOWN_NAME)
    owner: str = Field(max_length=40, pattern=SHOWN_NAME)
    introduced: bool


class SnapshotsConfig(FrozenModel):
    """Snapshots (item 405): whether both Command keys together capture the window in front into the open chat's
    composer, and whether a double tap of one does too (``double_tap``, off: item 814), whether the window's text comes
    with it, the sound that says it was taken, whether the capture animates into the box (its card flies to Dot's
    window, item 414), whether the window flashes as it is captured, whether Dot's window comes to the front with it,
    and how close the two taps must be."""

    enabled: bool
    app_text: bool
    sound: SnapshotSound
    animations: bool
    flash: bool
    open_dot: bool
    double_tap: bool = False
    double_tap_ms: int = Field(gt=0)


# Whether a run started from a channel tells what it does while it works (item 618): ``steps`` sends a few messages of
# its commands, tools and newest thought, ``off`` only its answer.
type ProgressMode = Literal["steps", "off"]


class ChannelsConfig(FrozenModel):
    """Which of the owner's messaging apps reach Dot (item 572): whether Dot reads the owner's conversation with
    themselves in Messages and answers in it, and tells them there what it says on its own (item 582) (``imessage``),
    and whether a run started from there tells what it does while it works (``progress``, item 618). Settings ›
    Channels switches both, live. ``language`` is the language of the lines Dot writes there itself (item 805):
    ``auto`` speaks the language of the owner's last message."""

    imessage: bool
    progress: ProgressMode
    language: Literal["auto", "en", "tr"] = "auto"


# What a reaction does once the run it is about has ended (item 586): ``follow_up`` starts a run in the same chat, as
# the owner's own message would, ``feedback`` only keeps it.
type AfterRun = Literal["follow_up", "feedback"]


class ReactionsConfig(FrozenModel):
    """The reactions on Dot's messages (item 586, docs/runs.md): ``follow_ups`` is whether a reaction may start a run
    after the run it is about has ended (off: every reaction is only kept, and a 📌 still makes its note);
    ``follow_ups_per_hour`` the most runs reactions start in an hour, whatever the chats; ``hold_minutes`` how long a
    run held by a ✋ waits for the owner's next message before it stops. The six that follow say what each reaction
    does after a run: 👍 ``good``, ❤️ ``love``, 👎 ``wrong``, 🔁 ``again``, ❓ ``explain`` and ✋ ``hold``. A 📌 always
    keeps its message as a note. Settings › General › Reactions changes ``follow_ups`` and these six, live."""

    follow_ups: bool
    follow_ups_per_hour: int = Field(ge=1, le=1000)
    hold_minutes: float = Field(gt=0, le=24 * 60, allow_inf_nan=False)
    good: AfterRun
    love: AfterRun
    wrong: AfterRun
    again: AfterRun
    explain: AfterRun
    hold: AfterRun


class RecapConfig(FrozenModel):
    """The recap of a run (item 536): whether one is written, after how many steps the first, and after how many more
    each new one; one more is due when the run ended after its last."""

    enabled: bool
    first_after_steps: int = Field(ge=1)
    every_steps: int = Field(ge=1)


type EmbedProvider = Literal["ollama", "openai"]


class MemoryConfig(FrozenModel):
    embed_provider: EmbedProvider
    embed_model: str
    embed_enabled: bool
    # The tokens a run's starting notes may take: inject_share of the model's context window, at most inject_tokens.
    inject_tokens: int = Field(ge=0)
    inject_share: float = Field(gt=0, le=1)
    candidate_pool: int
    min_similarity: float
    inject_min_similarity: float
    similar_threshold: float
    max_chars: int
    # After how many days without a run being handed it an agent's note rests (not handed to a run on its own), and
    # the half-life of the lift a note's last use gives it in a search.
    rest_days: int = Field(ge=1)
    use_half_life_days: float = Field(gt=0, allow_inf_nan=False)
    # The window of a note's score (how the runs handed it ended) and the runs it takes before the score counts.
    score_days: int = Field(ge=1)
    score_min_uses: int = Field(ge=1)
    # The most characters Dot's part of the owner section (what it knows about you) holds.
    owner_chars: int = Field(ge=100)
    # The nightly merge of near-duplicate notes (item 584): how close two notes must be (cosine) for the review model to
    # be asked whether they say the same thing, and the most merges a night makes (0 for none).
    merge_similarity: float = Field(gt=0, le=1)
    merges_per_night: int = Field(ge=0)
    # After how many days of resting a note is proposed to be forgotten, and for how many days a note the owner forgot
    # waits in the trash, restorable, before the reflection deletes it.
    forget_after_days: int = Field(ge=1)
    trash_days: int = Field(ge=1)


class HarnessConfig(FrozenModel):
    """The harness (item 389, docs/harness.md): how long each component of a version may be, in characters, and in
    lines; a version past these is refused when it is written."""

    habits_chars: int = Field(ge=0)
    tool_guidance_chars: int = Field(ge=0)
    component_lines: int = Field(ge=1)
    # The models of the roles of the improvement loop (item 390): a model reference each, "" for [models] review.
    proposer: str
    composer: str
    selector: str
    experience: str
    # The nightly step (item 391): when it runs (HH:MM in [scheduler] timezone, or "off"), the scoring set and the
    # final set (eval task ids, no task in both: item 589), how many times each task is played, how many passes more
    # than the base a candidate needs over the whole scoring set to count as better (item 589: one lucky attempt is no
    # evidence), which scopes are proposed (each once), and its budget of minutes and tokens; who adopts a version the
    # probe showed better: the owner, or the step itself; and how many days the loop's own records are kept (0: for
    # ever). Each set names at least one task: an empty list would select the whole catalogue, the final set with it.
    step_time: str
    scoring_tasks: list[str] = Field(min_length=1)
    final_tasks: list[str] = Field(min_length=1)
    step_repeats: int = Field(ge=1)
    step_margin: int = Field(ge=1)
    step_candidates: list[Literal["invariant", "domain"]] = Field(min_length=1, max_length=2)
    step_minutes: float = Field(gt=0, allow_inf_nan=False)
    step_tokens: int = Field(gt=0)
    adopt: Literal["owner", "auto"]
    keep_days: int = Field(ge=0)
    # Where a step scores a text (item 719): the tasks it looks for headroom in (a task of the final set or of the
    # scoring set is left out of it), how many of a task's newest attempts tell its headroom, how many tasks with
    # headroom and how many saturated ones are scored, how many tasks with headroom are the least that a candidate is
    # proposed for, and how many tasks of the pool the base tries a step.
    pool_tasks: list[str]
    headroom_window: int = Field(ge=1)
    headroom_tasks: int = Field(ge=1)
    regression_tasks: int = Field(ge=0)
    headroom_least: int = Field(ge=1)
    calibrate_tasks: int = Field(ge=0)

    @field_validator("proposer", "composer", "selector", "experience")
    @classmethod
    def _model_or_review(cls, value: str) -> str:
        return value if value == "" else _model_reference(value)

    @field_validator("step_time")
    @classmethod
    def _step_time_of_day_or_off(cls, value: str) -> str:
        if value != "off" and _TIME_OF_DAY.fullmatch(value) is None:
            raise ValueError('step_time is a time of day as "04:10" (24-hour) or "off"')
        return value

    @field_validator("step_candidates")
    @classmethod
    def _each_scope_once(cls, value: list[Literal["invariant", "domain"]]) -> list[Literal["invariant", "domain"]]:
        if len(set(value)) != len(value):
            raise ValueError("step_candidates names a scope once: a scope proposed twice is probed twice for nothing")
        return value

    @model_validator(mode="after")
    def _sets_share_no_task(self) -> Self:
        shared: list[str] = sorted(set(self.scoring_tasks) & set(self.final_tasks))
        if shared:
            raise ValueError(
                f"scoring_tasks and final_tasks share {', '.join(shared)}: the final set is never used to choose "
                "between candidates, so no task of it may be in the scoring set. Name both lists in [harness]: a "
                "task you name in one is taken out of the other's default, but not out of one you wrote too"
            )
        return self


class ShellConfig(FrozenModel):
    pass_env: list[str]
    login_env_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    default_timeout_s: int = Field(ge=1)
    max_timeout_s: int = Field(ge=1)
    terminal_sessions: int = Field(ge=0, le=10)
    # At least the longest wait of a terminal read, so that no session is closed while it is read.
    terminal_idle_s: float = Field(ge=60)
    sandbox: bool

    @model_validator(mode="after")
    def _default_within_max(self) -> Self:
        if self.default_timeout_s > self.max_timeout_s:
            raise ValueError("default_timeout_s cannot be more than max_timeout_s")
        return self


class CrawlConfig(FrozenModel):
    """The limits of ``web_crawl`` (``[web.crawl]``): the most pages and the deepest link a crawl may take (a call may
    ask for less), how many fetches run at once, the spacing of requests to one host, when a page is rendered in the
    browser (``auto``: when it shows little text without JavaScript), whether ``robots.txt`` is only reported or
    obeyed, and the most characters of text kept per page."""

    max_pages: int = Field(ge=1, le=500)
    max_depth: int = Field(ge=0, le=10)
    concurrency: int = Field(ge=1, le=16)
    per_host_spacing_s: float = Field(ge=0.0)
    render: Literal["auto", "never", "always"]
    robots: Literal["report", "obey"]
    page_max_chars: int = Field(ge=1000)


class WebConfig(FrozenModel):
    search_backend: Literal["auto", "searxng", "duckduckgo", "brave", "ollama", "anthropic"]
    # The engines web_search asks, in turn, as Settings › Web search orders them (item 655); empty: the ladder of
    # search_backend. An engine whose key is not set is passed over and said to be.
    search_order: list[SearchEngine] = Field(default_factory=list)
    # The base address of the owner's SearXNG instance (https://docs.searxng.org), whose own engines answer a search
    # without a key; empty: no such rung.
    searxng_url: str
    # Where the Anthropic rung stands on the auto ladder when ANTHROPIC_API_KEY is set: after Ollama's free search
    # (auto), before it (first), or not at all (off).
    anthropic_search: Literal["auto", "first", "off"]
    search_spacing_s: float
    fetch_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    fetch_max_bytes: int
    pdf_max_bytes: int
    allow_private_hosts: bool
    user_agent: str
    crawl: CrawlConfig

    @field_validator("search_order")
    @classmethod
    def _each_engine_once(cls, value: list[SearchEngine]) -> list[SearchEngine]:
        if len(set(value)) != len(value):
            raise ValueError("search_order names an engine twice")
        return value

    @field_validator("searxng_url")
    @classmethod
    def _searxng_url_is_a_base(cls, value: str) -> str:
        address: str = value.strip().rstrip("/")
        if not address:
            return ""
        parts = urlsplit(address)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
            raise ValueError("searxng_url must be an http or https address without credentials")
        return address


class SchedulerConfig(FrozenModel):
    timezone: str
    tick_s: float = Field(gt=0, allow_inf_nan=False)
    min_interval_s: int
    misfire_grace_s: int
    max_schedules: int
    # Item 257: how many earlier runs of a schedule a firing's opening carries the answers of (0: none), in how many
    # characters at most, and whether a firing goes on in the chat of the one before instead.
    history_runs: int = Field(ge=0, le=20)
    history_chars: int = Field(ge=500, le=20_000)
    same_chat: bool
    # Item 458: how many failed runs in a row switch a repeating schedule off (0: never); a schedule may choose its own.
    stop_after_failures: int = Field(ge=0, le=50)
    # Item 461: how the end of a schedule's run is told; a schedule may choose its own.
    notify: NotifyLevel


class WatchersConfig(FrozenModel):
    """The watchers (item 571, docs/proactive.md): whether they look at all, how often the daemon sees which is due, and
    the limits that keep a flood of mail or a spam run from becoming a flood of tells, model calls and runs."""

    enabled: bool
    tick_s: float = Field(gt=0, allow_inf_nan=False)
    tells_per_hour: int = Field(ge=1, le=200)
    runs_per_hour: int = Field(ge=0, le=100)
    judged_per_look: int = Field(ge=1, le=200)


class InitiativeConfig(FrozenModel):
    """Dot's own initiative (item 715, docs/proactive.md): the owner switches it on in Schedules; these say when the Mac
    counts as idle, how far apart Dot's own runs start, how often it looks for work, how many runs try one thing, and
    how many of its own runs start in a day (0: no limit), and the approval mode its runs start in (``yolo`` by default,
    item 756, the owner's word of 2026-10-27: a run of its own that asked waited on nobody, as nobody watches it)."""

    idle_minutes: float = Field(ge=0, le=1440, allow_inf_nan=False)
    gap_minutes: float = Field(ge=0, le=1440, allow_inf_nan=False)
    look_every_hours: float = Field(gt=0, le=168, allow_inf_nan=False)
    attempts: int = Field(ge=1, le=10)
    runs_per_day: int = Field(ge=0, le=1000)
    approvals: ApprovalMode
    # The hours of a weekday, in UTC, from the first to the second, in which none of its runs starts but an urgent thing
    # of the owner's (item 826): a provider's dearer hours. Empty: none.
    peak_hours_utc: tuple[int, ...] = ()

    @field_validator("peak_hours_utc")
    @classmethod
    def _two_hours(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if value and (len(value) != 2 or not 0 <= value[0] < value[1] <= 24):
            raise ValueError("peak_hours_utc is two hours of the day, the first before the second: [12, 18]")
        return value


type McpTransport = Literal["stdio", "http", "sse"]

# A lower-case letter, then lower-case letters, digits and hyphens; at most 24 characters. Plugin servers follow it too.
MCP_NAME_PATTERN: str = r"^[a-z][a-z0-9-]{0,23}$"
# The longest ``description`` an ``[[mcp]]`` table may carry (item 659): one line beside the server's group in the
# instructions of every run, among the names of its tools.
MCP_DESCRIPTION_CHARS: int = 200


def _a_line_of_words(value: str) -> str:
    """What a server is for, as the owner wrote it in ``description`` (item 659): one line of printable characters, at
    most :data:`MCP_DESCRIPTION_CHARS` of them once the spaces at its ends are taken off. Refused rather than
    mended, as the owner wrote it and can write it again."""
    words: str = value.strip()
    if LINE_BREAKS.search(words) is not None:
        raise ValueError("a description is one line: it has a line break in it")
    if "\t" in words:
        raise ValueError("a description is plain words: it has a tab in it")
    hidden: str | None = hidden_character(words)
    if hidden is not None:
        raise ValueError(f"a description is plain words: it has a character that prints nothing in it ({hidden})")
    if len(words) > MCP_DESCRIPTION_CHARS:
        raise ValueError(f"a description is at most {MCP_DESCRIPTION_CHARS} characters; this one has {len(words)}")
    return words


class McpServerConfig(FrozenModel):
    """One MCP server Dot talks to (a ``[[mcp]]`` table): a ``command`` it starts and talks to over standard input and
    output, or a ``url`` it connects to over streamable HTTP (``transport = "http"``, the default for a URL) or the
    older SSE transport (``transport = "sse"``).

    Values of ``env`` and ``headers`` may refer to secrets as ``${NAME}``; they are looked up like API keys
    (environment, ``.env``, Keychain). ``allow`` lists the server's tools to offer to the model (empty: all of them, up
    to a limit). ``oauth`` says that a server at a URL is signed in to as MCP's authorization asks, on Customize ›
    Plugins (item 279): Dot then sends the access token it keeps for the server and renews it. ``cwd`` is the folder a
    server Dot starts (a ``command``) starts in, a whole path; the workspace when it names none (item 637). The
    web-research server that comes with Dot starts in its own data folder, where a run cannot write, so that no file
    a run leaves in the workspace runs in it or in a program it starts. ``description`` is what
    the server is for, in the owner's own words (item 659): the line of its tool group in a run's instructions says it,
    and nothing the server says of itself, which stands there only for a server with no description.
    """

    name: str = Field(pattern=MCP_NAME_PATTERN)
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    headers: dict[str, str] = Field(default_factory=dict)
    transport: McpTransport | None = None
    oauth: bool = False
    allow: list[str] = Field(default_factory=list)
    enabled: bool = True
    startup_timeout_s: float = Field(default=60.0, gt=0)
    call_timeout_s: float = Field(default=120.0, gt=0)
    cwd: str | None = Field(default=None, min_length=1)
    description: Annotated[str, AfterValidator(_a_line_of_words)] = ""

    def transport_kind(self) -> McpTransport:
        """The transport in use: the one written down, else stdio for a command and streamable HTTP for a URL."""
        if self.transport is not None:
            return self.transport
        return "stdio" if self.command is not None else "http"

    @model_validator(mode="after")
    def _one_way_to_reach_it(self) -> Self:
        if (self.command is None) == (self.url is None):
            raise ValueError("an MCP server needs exactly one of command (stdio) and url (http or sse)")
        kind: McpTransport = self.transport_kind()
        if kind == "stdio" and self.command is None:
            raise ValueError("transport stdio needs a command, not a url")
        if kind != "stdio" and self.url is None:
            raise ValueError(f"transport {kind} needs a url, not a command")
        if self.command is None and (self.args or self.env):
            raise ValueError("args and env belong to a command; a url takes headers")
        if self.url is None and self.headers:
            raise ValueError("headers belong to a url; a command takes args and env")
        if self.url is None and self.oauth:
            raise ValueError("oauth belongs to a url; a command signs in by its own environment")
        if self.cwd is not None and self.command is None:
            raise ValueError("cwd belongs to a command; a server at a url has no folder of Dot's")
        if self.cwd is not None and not Path(self.cwd).is_absolute():
            raise ValueError("cwd is a whole path, from / on")
        return self


class BuiltinMcpConfig(FrozenModel):
    """The MCP servers that come with Dot (``[builtin_mcp]``, item 583), a switch for each, named for its server with
    an underscore for the hyphen: ``web_research`` is the server ``web-research``, which searches the web and reads
    pages (the package ``dotagent-web-research``). Dot starts one that is on with the servers of ``[[mcp]]``, unless
    its package is not installed or another server has its name: a ``[[mcp]]`` table named ``web-research`` replaces
    it. The table is not ``[mcp]``, which TOML cannot have beside the ``[[mcp]]`` tables."""

    web_research: bool


class SkillsConfig(FrozenModel):
    """Where skills are looked for besides ``<home>/skills`` and the ``skills/`` folder of each plugin (``[skills]``).
    Each entry is a directory whose children are skill folders (``<name>/SKILL.md``); ``~`` expands. ``elsewhere``:
    whether ``find_help`` also lists the skills and MCP servers that the Mac's other agents set up and the owner can
    add (item 165); the evals turn it off, as they run without the user's skills. ``listed``: how many skills a run's
    instructions list at most; a run offered more is listed those that fit its goal best, and find_help searches the
    rest (item 166). ``builtin``: whether runs are offered the skills that come with Dot (Excel, Word and OneDrive on
    the Mac, item 196); a skill of the user's with the same name is used instead of one of them."""

    dirs: list[str] = Field(default_factory=list)
    elsewhere: bool = True
    listed: int = Field(default=20, ge=1)
    builtin: bool = True


class ScoutConfig(FrozenModel):
    """The plugin scout (item 711, docs/extending.md): when it looks each night for plugins worth proposing and for
    updates of those installed, and how much one night may read. It installs nothing: each proposal waits for the
    owner."""

    time: str
    leads_per_night: int = Field(ge=0, le=200)
    updates_per_night: int = Field(ge=0, le=100)
    minutes: int = Field(ge=1, le=240)
    review_timeout_s: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("time")
    @classmethod
    def _time_of_day(cls, value: str) -> str:
        if value != "off" and _TIME_OF_DAY.fullmatch(value) is None:
            raise ValueError('[scout] time is a time of day as "03:30" (24-hour) or "off"')
        return value


class PluginsConfig(FrozenModel):
    """Where plugins are looked for besides ``<home>/plugins`` (``[plugins]``). Each entry is a directory whose children
    are plugins in the Claude Code layout (``.claude-plugin/plugin.json``), or one such plugin itself; ``~`` expands."""

    dirs: list[str] = Field(default_factory=list)


# Whether runs trade, and with what money (docs/trading.md): no trading tools at all, an account of Dot's own that
# trades at the market's real prices with money that is not there, or the owner's Coinbase account.
TradingMode = Literal["off", "paper", "live"]
# The trend engine of item 818: off, or the rule of dotagent.trading.trend.
TrendEngineMode = Literal["off", "trend"]
# Among [trading] products (item 771): every product of the quote with [trading] min_volume a day.
ANY_PRODUCT: str = "*"
_PRODUCT: re.Pattern[str] = re.compile(r"[A-Z0-9]{2,10}-[A-Z]{3,5}")
_CURRENCY: re.Pattern[str] = re.compile(r"[A-Z]{3,5}")


def is_product_id(text: str) -> bool:
    """Whether ``text`` reads as a Coinbase product id, as BTC-EUR: what a product of Coinbase's list must be before it
    is shown to a run (item 772), as a name in [trading] products must."""
    return _PRODUCT.fullmatch(text) is not None


class ReconConfig(FrozenModel):
    """Network reconnaissance (``[recon]``, item 776, docs/recon.md). A passive audit reads what a browser's visit
    reads, on any public site; an active one asks which common ports answer, and runs only on a host whose name is in
    ``owned_hosts``, or is a subdomain of one: the owner's own, or one they may test. Nothing else can widen it, and
    the list is empty until the owner writes it."""

    owned_hosts: tuple[str, ...] = ()

    @field_validator("owned_hosts")
    @classmethod
    def _host_names(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        names: list[str] = []
        for host in hosts:
            name: str = host.strip().lower().rstrip(".")
            if (
                not name
                or "/" in name
                or "*" in name
                or "://" in name
                or any(character.isspace() for character in name)
            ):
                raise ValueError(
                    f"[recon] owned_hosts holds bare host names, without a wildcard, port, scheme or path; not {host!r}"
                )
            if ":" in name:
                try:
                    ipaddress.IPv6Address(name)
                except ValueError:
                    raise ValueError(
                        "[recon] owned_hosts holds bare host names, without a wildcard, port, scheme or path; an "
                        f"IPv6 address is written without brackets; not {host!r}"
                    ) from None
            names.append(name)
        return tuple(dict.fromkeys(names))


class TradingConfig(FrozenModel):
    """Trading on Coinbase (``[trading]``, docs/trading.md). The limits are in ``quote``, the currency every product of
    ``products`` is priced in, and no run can raise them: a run cannot change this file, and an order past one of them
    is refused before it reaches any market, in every mode, yolo's too. ``max_order`` is the most one order may be
    worth, ``max_day`` the most the orders of a day (the owner's day, ``[scheduler] timezone``) may add up to, buys and
    sells, ``max_day_loss`` how much the account may lose in a day before no buy is placed until the next (a sell still
    is), ``max_position`` the most one product held may be worth after a buy. ``paper_start`` is what the paper account
    starts with, ``paper_fee_rate`` the share of an order its fills pay, as Coinbase's taker fee does."""

    mode: TradingMode
    quote: str
    products: list[str] = Field(min_length=1, max_length=50)
    # With "*" among the products (item 771): every product priced in quote that trades at least this much of the
    # quote a day, as Coinbase sums it; a market thinner than that is one a small order moves, and one a pump empties.
    min_volume: float = Field(default=1_000_000.0, ge=0, allow_inf_nan=False)
    max_order: float = Field(gt=0, allow_inf_nan=False)
    max_day: float = Field(gt=0, allow_inf_nan=False)
    max_day_loss: float = Field(gt=0, allow_inf_nan=False)
    max_position: float = Field(gt=0, allow_inf_nan=False)
    paper_start: float = Field(gt=0, allow_inf_nan=False)
    paper_fee_rate: float = Field(ge=0, lt=0.1, allow_inf_nan=False)
    timeout_s: float = Field(gt=0, le=120, allow_inf_nan=False)
    # A move of the market that starts a run (item 772): the id of the schedule it starts ("": none), the least move
    # over fifteen minutes, in percent, that does, how many such runs a day may have and how many minutes apart.
    wake_schedule: str = Field(default="", max_length=64)
    wake_move_pct: float = Field(default=3.0, gt=0, le=100, allow_inf_nan=False)
    wake_per_day: int = Field(default=6, ge=0, le=96)
    wake_gap_min: float = Field(default=30.0, ge=5, le=1440, allow_inf_nan=False)
    # The trend engine (item 818, :mod:`dotagent.trading.engine`): ``"trend"`` trades ``engine_coins`` (each priced in
    # ``quote``) by the rule of :mod:`dotagent.trading.trend`, on paper only, within the limits above, with no model;
    # ``engine_target_vol`` is the yearly volatility each product's share is sized to, ``engine_band`` how far a share
    # may be off its target before the engine trades it.
    engine: TrendEngineMode = "off"
    engine_coins: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL"], min_length=1, max_length=10)
    engine_target_vol: float = Field(default=0.5, gt=0, le=2, allow_inf_nan=False)
    engine_band: float = Field(default=0.10, ge=0, lt=1, allow_inf_nan=False)

    @field_validator("engine_coins")
    @classmethod
    def _coins(cls, value: list[str]) -> list[str]:
        wrong: list[str] = [coin for coin in value if _CURRENCY.fullmatch(coin) is None]
        if wrong:
            raise ValueError('[trading] engine_coins are currency codes in capitals, as "BTC": ' + ", ".join(wrong))
        if len(set(value)) != len(value):
            raise ValueError("[trading] engine_coins names a coin twice")
        return value

    def engine_products(self) -> list[str]:
        """The products the trend engine trades: each of ``engine_coins`` in ``quote``."""
        return [f"{coin}-{self.quote}" for coin in self.engine_coins]

    @field_validator("quote")
    @classmethod
    def _currency(cls, value: str) -> str:
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError('[trading] quote is a currency code in capitals, as "EUR"')
        return value

    @model_validator(mode="after")
    def _products_in_quote(self) -> Self:
        wrong: list[str] = [
            product
            for product in self.products
            if product != ANY_PRODUCT
            and (_PRODUCT.fullmatch(product) is None or not product.endswith(f"-{self.quote}"))
        ]
        if wrong:
            raise ValueError(
                f'[trading] products are Coinbase product ids priced in {self.quote}, as BTC-{self.quote}, or "*": '
                + ", ".join(wrong)
            )
        if self.max_order > self.max_day:
            raise ValueError("[trading] max_order is more than max_day, which every order counts against")
        return self


# Who the typed judge asks: nobody, or TypeSafe's System One API.
JudgeProvider = Literal["none", "typesafe"]


class JudgeConfig(FrozenModel):
    """The typed judge (``[judge]``, :mod:`dotagent.agent.judge`): ``provider = "none"`` leaves its two questions to the
    policy's patterns and the goal checker; ``"typesafe"`` asks TypeSafe's System One API at ``base_url`` with the key
    named by ``api_key_env``. ``timeout_s`` bounds one judgment, retries included. With ``second_opinion`` each
    question is asked a second time in an independent phrasing, and the two answers have to agree before the judge
    acts on them. The judge_items tool (:mod:`dotagent.tools.judge_items`) uses the same key, address, model and
    timeout whatever ``provider`` says, and asks about ``items_per_request`` items in one request; so do the post tools
    of a campaign (:mod:`dotagent.tools.posts`), whose check of a draft fires at ``draft_check_threshold``."""

    provider: JudgeProvider
    api_key_env: str
    base_url: ServiceAddress
    model: str
    timeout_s: float = Field(gt=0, le=60)
    public_action_threshold: float = Field(ge=0, le=1)
    target_threshold: float = Field(ge=0, le=1)
    second_opinion: bool
    items_per_request: int = Field(ge=1, le=500)
    draft_check_threshold: float = Field(ge=0, le=1)


class AppConfig(FrozenModel):
    mcp: list[McpServerConfig]
    builtin_mcp: BuiltinMcpConfig
    server: ServerConfig
    store: StoreConfig
    models: ModelsConfig
    providers: ProvidersConfig
    judge: JudgeConfig
    agent: AgentConfig
    policy: PolicyConfig
    shell: ShellConfig
    web: WebConfig
    browser: BrowserConfig
    desktop: DesktopConfig
    snapshots: SnapshotsConfig
    channels: ChannelsConfig
    reactions: ReactionsConfig
    recap: RecapConfig
    assistant: AssistantConfig
    memory: MemoryConfig
    harness: HarnessConfig
    scheduler: SchedulerConfig
    watchers: WatchersConfig
    initiative: InitiativeConfig
    skills: SkillsConfig
    plugins: PluginsConfig
    scout: ScoutConfig
    recon: ReconConfig
    trading: TradingConfig

    @field_validator("mcp")
    @classmethod
    def _unique_server_names(cls, servers: list[McpServerConfig]) -> list[McpServerConfig]:
        names: list[str] = [server.name for server in servers]
        repeated: list[str] = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"MCP server names must be unique; repeated: {', '.join(repeated)}")
        return servers


def deep_merge(base: JsonObject, overlay: JsonObject) -> JsonObject:
    """Return a new mapping: ``overlay`` wins, nested mappings merge, everything else is replaced."""
    merged: JsonObject = dict(base)
    for key, value in overlay.items():
        current: JsonValue = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> JsonObject:
    try:
        with path.open("rb") as handle:
            parsed: JsonObject = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("config file is not valid TOML", {"path": str(path), "error": str(exc)}) from exc
    return parsed


def load_defaults() -> JsonObject:
    text: str = resources.files("dotagent").joinpath("defaults.toml").read_text(encoding="utf-8")
    try:
        parsed: JsonObject = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("packaged defaults.toml is not valid TOML", {"error": str(exc)}) from exc
    return parsed


# How many of the errors of a configuration its message names; the context holds them all.
_PROBLEMS_NAMED: int = 5


def describe_problems(exc: ValidationError) -> str:
    """What failed validation, in a line: a key the schema does not have is named as one a newer Dot may add (a daemon
    that runs an older checkout than the one that wrote the file refuses it), any other error by its path and its
    message. At most :data:`_PROBLEMS_NAMED` errors are named."""
    problems: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        path: str = ".".join(str(part) for part in error["loc"])
        if error["type"] == "extra_forbidden":
            problems.append(f"{path} is not a setting this version of Dot knows (a newer Dot may add it)")
        else:
            problems.append(f"{path}: {error['msg']}")
    named: str = "; ".join(problems[:_PROBLEMS_NAMED])
    return named if len(problems) <= _PROBLEMS_NAMED else f"{named}; and {len(problems) - _PROBLEMS_NAMED} more"


def _validated(merged: JsonObject, source: str) -> AppConfig:
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"configuration failed validation: {describe_problems(exc)}",
            {
                "path": source,
                "errors": exc.json(include_url=False, include_context=False, include_input=False),
                "fix": f"correct or remove the setting in {source}, or update the Dot that reads it",
            },
        ) from exc


def _settled_task_sets(merged: JsonObject, user: JsonObject, source: str) -> JsonObject:
    """``merged`` with the two task sets of the harness made disjoint where the owner wrote only one of them (item 589):
    a task they name in ``final_tasks`` is no longer in the default ``scoring_tasks`` (and the other way round), as
    their word wins over a default that grew a task since they wrote their file. Two lists they both wrote are left as
    they are: whether they share a task is for the validation to say. A list that names every task of the other's
    default leaves it none, which is said here, in words that name the list to write too (``source`` is the file), and
    not by the validation's "List should have at least 1 item"."""
    written: JsonValue = user.get("harness")
    table: JsonValue = merged.get("harness")
    if not isinstance(written, dict) or not isinstance(table, dict):
        return merged
    named: str | None = (
        "final_tasks"
        if "final_tasks" in written and "scoring_tasks" not in written
        else "scoring_tasks"
        if "scoring_tasks" in written and "final_tasks" not in written
        else None
    )
    given: JsonValue = None if named is None else table.get(named)
    if named is None or not isinstance(given, list):
        return merged
    default: str = "scoring_tasks" if named == "final_tasks" else "final_tasks"
    others: JsonValue = table.get(default)
    if not isinstance(others, list):
        return merged
    left: list[JsonValue] = [task for task in others if task not in given]
    if not left:
        raise ConfigError(
            f"configuration failed validation: harness.{named} names every task of the default {default}, which leaves "
            f"it none: name {default} too",
            {"path": source, "fix": f"name {default} as well as {named} under [harness] in {source}"},
        )
    return {**merged, "harness": {**table, default: left}}


def load_config(user_path: Path) -> AppConfig:
    """Load defaults, overlay ``user_path`` if it exists, and validate the result."""
    merged: JsonObject = load_defaults()
    if user_path.exists():
        user: JsonObject = _read_toml(user_path)
        merged = _settled_task_sets(deep_merge(merged, user), user, str(user_path))
    return _validated(merged, str(user_path))


def load_default_config() -> AppConfig:
    """The packaged defaults alone, with no file of the user laid over them."""
    return _validated(load_defaults(), "defaults.toml")


def load_user_config() -> AppConfig:
    return load_config(config_path())


# The service of the typed judge ([judge]); its key is listed with the providers' and reaches the judge, not the router.
JUDGE_SERVICE: str = "typesafe"


def providers_by_key(config: AppConfig) -> dict[str, str]:
    """The provider of each key Dot manages (Settings › Providers, ``dotx auth``), by the name of its variable, in the
    order they are listed: the providers whose models need a key first, Ollama (whose models the local server runs
    without one) next, and the typed judge's service last."""
    providers: ProvidersConfig = config.providers
    return {
        providers.openai.api_key_env: "openai",
        providers.anthropic.api_key_env: "anthropic",
        providers.opencode_go.api_key_env: "opencode_go",
        providers.openrouter.api_key_env: "openrouter",
        providers.ollama.api_key_env: "ollama",
        config.judge.api_key_env: JUDGE_SERVICE,
    }
