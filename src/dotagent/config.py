"""Typed configuration: the packaged ``defaults.toml`` deep-merged with the user's ``config.toml``."""

import tomllib
from importlib import resources
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from dotagent.core.errors import ConfigError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode, Preset, QuestionMode


class ServerConfig(FrozenModel):
    host: str
    port: int


class OllamaConfig(FrozenModel):
    base_url: str
    api_key_env: str
    num_ctx: int
    keep_alive: str
    connect_timeout_s: float
    read_timeout_s: float
    attempts: int
    max_concurrent: int


type GoRoute = Literal["chat", "responses", "messages"]


class OpenAIConfig(FrozenModel):
    base_url: str
    api_key_env: str
    organization_env: str
    project_env: str
    wire: Literal["responses", "chat"]
    connect_timeout_s: float
    read_timeout_s: float
    attempts: int
    max_concurrent: int


class OpenCodeGoConfig(FrozenModel):
    base_url: str
    api_key_env: str
    route_overrides: dict[str, GoRoute]
    connect_timeout_s: float
    read_timeout_s: float
    attempts: int
    max_concurrent: int


class AnthropicConfig(FrozenModel):
    base_url: str
    api_key_env: str
    connect_timeout_s: float
    read_timeout_s: float
    attempts: int
    max_concurrent: int


class ProvidersConfig(FrozenModel):
    ollama: OllamaConfig
    openai: OpenAIConfig
    opencode_go: OpenCodeGoConfig
    anthropic: AnthropicConfig


class ModelsConfig(FrozenModel):
    main: str
    vision: str
    fast: str
    review: str
    effort: Effort
    helpers: list[str]
    standby: list[str]
    # At most a day, the longest pause of the router (dotagent.llm.router.MAX_PAUSE_S).
    helper_cooldown_s: float = Field(gt=0, le=86_400, allow_inf_nan=False)


type Tier = Literal["standard", "weak"]
type ScaffoldMode = Literal["fixed", "measured"]
type ToolSurface = Literal["full", "grouped", "auto"]
type ResumeMode = Literal["off", "safe"]
type LessonsMode = Literal["off", "propose", "auto"]


class AgentConfig(FrozenModel):
    goal_mode: bool
    goal_patience: int
    run_max_minutes: float = Field(ge=0, allow_inf_nan=False)
    run_max_tokens: int = Field(ge=0)
    campaign_gap_minutes: float = Field(gt=0, allow_inf_nan=False)
    campaign_wait_hours: float = Field(ge=0, allow_inf_nan=False)
    campaign_patience_sessions: int = Field(ge=1)
    campaign_max_sessions: int = Field(ge=0)
    consult_max_per_run: int = Field(ge=0)
    tool_output_chars: int
    context_tokens: int
    keep_recent_tool_results: int
    keep_recent_screenshots: int
    stall_limit: int
    stall_escalation: bool
    failure_streak: int
    revisit_window: int
    finish_nudges: int
    questions: QuestionMode
    parallel_tools: int
    model_call_timeout_s: float
    max_concurrent_runs: int
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
    playbooks: bool
    undo_max_file_bytes: int
    undo_max_run_bytes: int
    undo_keep_days: int


class PaceConfig(FrozenModel):
    """How many public and social browser actions a site gets in an hour and in a day, and how far apart public ones
    are (:mod:`dotagent.policy.pace`); 0 removes a limit."""

    public_per_hour: int = Field(ge=0)
    public_per_day: int = Field(ge=0)
    social_per_hour: int = Field(ge=0)
    social_per_day: int = Field(ge=0)
    public_spacing_s: float = Field(ge=0, le=3600)


class PolicyConfig(FrozenModel):
    """``code_sources`` lists the applications ``code_fetch`` may read one-time codes from (``mail``, ``messages``);
    empty, the tool does not exist."""

    preset: Preset
    approvals: ApprovalMode
    approval_timeout_s: float
    protected_paths: list[str]
    code_sources: list[Literal["mail", "messages"]]
    pace: PaceConfig


type BrowserMode = Literal["own", "attach"]


class BrowserConfig(FrozenModel):
    mode: BrowserMode
    attach_timeout_s: float
    channel: str
    headless: bool
    viewport_width: int
    viewport_height: int
    navigation_timeout_s: float
    action_timeout_s: float
    snapshot_max_chars: int
    viewport_margin_px: int
    settle_quiet_ms: int
    settle_max_ms: int
    side_effect_grace_ms: int
    navigation_attempts: int
    navigation_backoff_ms: int
    max_tabs: int
    tab_idle_s: float
    screenshot_quality: int
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
    ax_timeout_s: float
    ax_budget_s: float
    ax_max_nodes: int
    ax_max_depth: int
    snapshot_max_chars: int
    capture_timeout_s: float
    paste_settle_ms: int
    paste_threshold_chars: int
    keep_awake: bool
    keep_awake_s: int
    max_batch_steps: int
    launch_timeout_s: float
    activate_timeout_s: float
    osascript_timeout_s: float


type EmbedProvider = Literal["ollama", "openai"]


class MemoryConfig(FrozenModel):
    embed_provider: EmbedProvider
    embed_model: str
    embed_enabled: bool
    inject_top_k: int
    core_limit: int
    user_limit: int
    candidate_pool: int
    min_similarity: float
    inject_min_similarity: float
    similar_threshold: float
    max_chars: int


class ShellConfig(FrozenModel):
    pass_env: list[str]
    login_env_timeout_s: float
    default_timeout_s: int
    max_timeout_s: int
    terminal_sessions: int = Field(ge=0, le=10)
    # At least the longest wait of a terminal read, so that no session is closed while it is read.
    terminal_idle_s: float = Field(ge=60)


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
    search_backend: Literal["auto", "duckduckgo", "ollama"]
    search_spacing_s: float
    search_attempts: int
    fetch_timeout_s: float
    fetch_max_bytes: int
    pdf_max_bytes: int
    allow_private_hosts: bool
    user_agent: str
    crawl: CrawlConfig


class SchedulerConfig(FrozenModel):
    timezone: str
    tick_s: float
    min_interval_s: int
    misfire_grace_s: int
    max_schedules: int


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
    Each entry is a directory whose children are skill folders (``<name>/SKILL.md``); ``~`` expands."""

    dirs: list[str] = Field(default_factory=list)


class PluginsConfig(FrozenModel):
    """Where plugins are looked for besides ``<home>/plugins`` (``[plugins]``). Each entry is a directory whose children
    are plugins in the Claude Code layout (``.claude-plugin/plugin.json``), or one such plugin itself; ``~`` expands."""

    dirs: list[str] = Field(default_factory=list)


class JudgeConfig(FrozenModel):
    """The typed judge (``[judge]``, :mod:`dotagent.agent.judge`): ``provider = "none"`` leaves its two questions to the
    policy's patterns and the goal checker; ``"typesafe"`` asks TypeSafe's System One API at ``base_url`` with the key
    named by ``api_key_env``. ``timeout_s`` bounds one judgment, retries included."""

    provider: Literal["none", "typesafe"]
    api_key_env: str
    base_url: str
    model: str
    timeout_s: float = Field(gt=0, le=60)
    public_action_threshold: float = Field(ge=0, le=1)
    target_threshold: float = Field(ge=0, le=1)


class AppConfig(FrozenModel):
    mcp: list[McpServerConfig]
    server: ServerConfig
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
