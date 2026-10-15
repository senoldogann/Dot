"""Typed configuration: the packaged ``defaults.toml`` deep-merged with the user's ``config.toml``."""

import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import AfterValidator, Field, JsonValue, ValidationError, field_validator, model_validator

from dotagent.core.errors import ConfigError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import KNOWN_PROVIDERS, Effort, split_model_ref
from dotagent.policy.types import ApprovalMode, Preset, QuestionMode
from dotagent.store.models import BrowserMode


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


# The smallest context size a model's runs may be given (item 250, [models] context): the budget every run had before
# 2026-10-11. A run's first step alone sends its instructions, its tools and the request (29,294 tokens at the median
# of 48 runs, measured 2026-10-15), so a smaller budget would summarise a run after its first few steps.
CONTEXT_FLOOR: int = 48_000

type ServiceAddress = Annotated[str, AfterValidator(_service_address)]
type ModelReference = Annotated[str, AfterValidator(_model_reference)]
type SpendSite = Annotated[str, AfterValidator(_spend_site)]
type DebugPort = Annotated[int, Field(ge=0, le=65535), AfterValidator(_debug_port)]
type ContextSize = Annotated[int, Field(ge=CONTEXT_FLOOR)]


class ServerConfig(FrozenModel):
    """``host`` and ``port`` are where the daemon listens, a loopback address only. ``reach`` names the hosts a reverse
    proxy on the owner's own network presents the daemon as (``tailscale serve`` gives ``<mac>.<tailnet>.ts.net``); a
    request with such a ``Host``, and an ``Origin`` of ``https://`` that host, is served like a local one."""

    host: str
    port: int = Field(ge=1, le=65535)
    reach: list[str]


class StoreConfig(FrozenModel):
    """What ``dotx prune --apply`` deletes of the database (``[store]``): the events of the runs that ended more than
    ``retention_days`` days ago, and with them the screenshots only those events name. Nothing is deleted by itself."""

    retention_days: int = Field(ge=1)


class OllamaConfig(FrozenModel):
    base_url: ServiceAddress
    api_key_env: str
    num_ctx: int
    num_ctx_cloud: int
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
    its own when a run or the list of models needs it. ``command`` is the ``opencode`` program: a path, or a name looked
    up on the login shell's PATH and in Homebrew's folders. The server stops once it has served nothing for
    ``idle_stop_s``; the free models it lists are kept for ``models_ttl_s`` without starting it again."""

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


class ProvidersConfig(FrozenModel):
    ollama: OllamaConfig
    openai: OpenAIConfig
    opencode_go: OpenCodeGoConfig
    anthropic: AnthropicConfig
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
type ResumeMode = Literal["off", "safe"]
type LessonsMode = Literal["off", "propose", "auto"]
# A time of day as [agent] reflection_time takes it: 24-hour, two digits each.
_TIME_OF_DAY: re.Pattern[str] = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class AgentConfig(FrozenModel):
    goal_mode: bool
    goal_patience: int
    run_max_minutes: float = Field(ge=0, allow_inf_nan=False)
    run_max_tokens: int = Field(ge=0)
    campaign_gap_minutes: float = Field(gt=0, allow_inf_nan=False)
    campaign_wait_hours: float = Field(ge=0, allow_inf_nan=False)
    campaign_patience_sessions: int = Field(ge=1)
    campaign_max_sessions: int = Field(ge=0)
    # The Chrome a campaign's sessions work in unless its start says otherwise.
    campaign_browser: BrowserMode
    consult_max_per_run: int = Field(ge=0)
    # At least the floor a run's tool output keeps however small the model's window (dotagent.agent.scaffold).
    tool_output_chars: int = Field(ge=2000)
    context_tokens: int = Field(gt=0)
    # The harness lessons of arXiv 2609.20804 (item 87): past context_elide_share of the budget older tool output is
    # shortened, past context_summarise_share the older part of the run is summarised; plan_each_turn puts the open
    # plan at the end of the system prompt of every step.
    context_elide_share: float = Field(gt=0, le=1, allow_inf_nan=False)
    context_summarise_share: float = Field(gt=0, le=1, allow_inf_nan=False)
    plan_each_turn: bool
    keep_recent_tool_results: int
    keep_recent_screenshots: int
    stall_limit: int
    stall_escalation: bool
    failure_streak: int
    revisit_window: int
    finish_nudges: int
    questions: QuestionMode
    parallel_tools: int = Field(ge=1)
    model_call_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    model_wait_s: float = Field(ge=0, allow_inf_nan=False)
    max_concurrent_runs: int = Field(ge=1)
    scaffold: ScaffoldMode
    tiers: dict[str, Tier]
    profile_min_calls: int
    weak_refusal_share: float
    reminder_every: int
    steer_single_actions: bool
    tool_surface: ToolSurface
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


class PolicyConfig(FrozenModel):
    """``code_sources`` lists the applications ``code_fetch`` may read one-time codes from (``mail``, ``messages``);
    empty, the tool does not exist. ``spend`` maps a site (its host without ``www.``) to its pre-approved caps."""

    preset: Preset
    approvals: ApprovalMode
    approval_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    protected_paths: list[str]
    code_sources: list[Literal["mail", "messages"]]
    spend: dict[SpendSite, SpendCap]
    pace: PaceConfig


class BrowserConfig(FrozenModel):
    mode: BrowserMode
    attach_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    channel: str
    headless: bool
    # Whether Dot's Chrome is brought to the front while a run works (item 97); Take over brings it either way.
    raise_window: bool
    # The port of this Mac on which Dot's own Chrome also takes DevTools connections, from a run's scripts (item 265);
    # 0 for none.
    debug_port: DebugPort
    # A page Chrome can open: from a small phone's to a 4K screen's.
    viewport_width: int = Field(ge=320, le=3840)
    viewport_height: int = Field(ge=240, le=2160)
    navigation_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    action_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    snapshot_max_chars: int
    viewport_margin_px: int = Field(ge=0)
    settle_quiet_ms: int
    settle_max_ms: int
    side_effect_grace_ms: int
    navigation_attempts: int
    navigation_backoff_ms: int
    max_tabs: int = Field(ge=1)
    tab_idle_s: float = Field(gt=0, allow_inf_nan=False)
    # The JPEG quality Chrome takes a screenshot with.
    screenshot_quality: int = Field(ge=1, le=100)
    max_batch_steps: int
    read_text_chars: int
    allowed_hosts: list[str]
    blocked_hosts: list[str]
    decline_cookie_banners: bool


class DesktopConfig(FrozenModel):
    enabled: bool
    screenshot_max_edge: int
    jpeg_quality: int
    settle_quiet_ms: int
    settle_ms: int
    failsafe_corner: bool
    ax_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    ax_budget_s: float = Field(gt=0, allow_inf_nan=False)
    ax_max_nodes: int
    ax_max_depth: int
    snapshot_max_chars: int
    capture_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    paste_settle_ms: int
    paste_threshold_chars: int
    keep_awake: bool
    keep_awake_s: int
    max_batch_steps: int
    launch_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    activate_timeout_s: float = Field(gt=0, allow_inf_nan=False)
    osascript_timeout_s: float = Field(gt=0, allow_inf_nan=False)


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
    search_backend: Literal["auto", "duckduckgo", "brave", "ollama", "anthropic"]
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


type McpTransport = Literal["stdio", "http", "sse"]

# A lower-case letter, then lower-case letters, digits and hyphens; at most 24 characters. Plugin servers follow it too.
MCP_NAME_PATTERN: str = r"^[a-z][a-z0-9-]{0,23}$"


class McpServerConfig(FrozenModel):
    """One MCP server Dot talks to (a ``[[mcp]]`` table): a ``command`` it starts and talks to over standard input and
    output, or a ``url`` it connects to over streamable HTTP (``transport = "http"``, the default for a URL) or the older
    SSE transport (``transport = "sse"``).

    Values of ``env`` and ``headers`` may refer to secrets as ``${NAME}``; they are looked up like API keys
    (environment, ``.env``, Keychain). ``allow`` lists the server's tools to offer to the model (empty: all of them, up
    to a limit).
    """

    name: str = Field(pattern=MCP_NAME_PATTERN)
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    headers: dict[str, str] = Field(default_factory=dict)
    transport: McpTransport | None = None
    allow: list[str] = Field(default_factory=list)
    enabled: bool = True
    startup_timeout_s: float = Field(default=60.0, gt=0)
    call_timeout_s: float = Field(default=120.0, gt=0)

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
        return self


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


class PluginsConfig(FrozenModel):
    """Where plugins are looked for besides ``<home>/plugins`` (``[plugins]``). Each entry is a directory whose children
    are plugins in the Claude Code layout (``.claude-plugin/plugin.json``), or one such plugin itself; ``~`` expands."""

    dirs: list[str] = Field(default_factory=list)


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
    memory: MemoryConfig
    scheduler: SchedulerConfig
    skills: SkillsConfig
    plugins: PluginsConfig

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


def _validated(merged: JsonObject, source: str) -> AppConfig:
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            "configuration failed validation",
            {
                "path": source,
                "errors": exc.json(include_url=False, include_context=False, include_input=False),
            },
        ) from exc


def load_config(user_path: Path) -> AppConfig:
    """Load defaults, overlay ``user_path`` if it exists, and validate the result."""
    merged: JsonObject = load_defaults()
    if user_path.exists():
        merged = deep_merge(merged, _read_toml(user_path))
    return _validated(merged, str(user_path))


def load_default_config() -> AppConfig:
    """The packaged defaults alone, with no file of the user laid over them."""
    return _validated(load_defaults(), "defaults.toml")


def load_user_config() -> AppConfig:
    return load_config(config_path())


# The service of the typed judge ([judge]); its key is listed with the providers' and reaches the judge, not the router.
JUDGE_SERVICE: str = "typesafe"


def providers_by_key(config: AppConfig) -> dict[str, str]:
    """The provider of each key Dot manages (Settings › API keys, ``dotx auth``), by the name of its variable, in the
    order they are listed: the providers whose models need a key first, Ollama (whose models the local server runs
    without one) next, and the typed judge's service last."""
    providers: ProvidersConfig = config.providers
    return {
        providers.openai.api_key_env: "openai",
        providers.anthropic.api_key_env: "anthropic",
        providers.opencode_go.api_key_env: "opencode_go",
        providers.ollama.api_key_env: "ollama",
        config.judge.api_key_env: JUDGE_SERVICE,
    }
