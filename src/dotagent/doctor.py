"""Environment diagnostics for ``dotx doctor``: what works, what is missing and how to fix it. The check of one
provider's key is the one the Providers page of the settings runs."""

import asyncio
import os
import platform
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from pydantic import JsonValue

from dotagent.agent.helpers import Roster, keyed_providers, roster_for, unavailable_text
from dotagent.browser.attach import DebugEndpoint, read_debug_endpoint
from dotagent.browser.keychain import KeychainMode, read_keychain_mode
from dotagent.browser.login import CHROME_APP
from dotagent.browser.profile_lock import profile_holder_pid, started_by_dot
from dotagent.browser.profiles import profile_dir
from dotagent.config import (
    AppConfig,
    JudgeConfig,
    LessonsMode,
    McpServerConfig,
    MemoryConfig,
    ModelsConfig,
    ProvidersConfig,
)
from dotagent.core.errors import BrowserError, ConfigError, ProviderError, ToolExecutionError, WebError
from dotagent.core.paths import (
    chrome_data_dir,
    config_path,
    db_path,
    dot_home,
    env_file_path,
    plugins_dir,
    skills_dir,
    workspace_dir,
)
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.desktop.capture import rect_capture_supported
from dotagent.desktop.codes import chat_db_path
from dotagent.desktop.perms import check_permissions
from dotagent.desktop.types import Permissions
from dotagent.dot_app import LAUNCHER_ENV, has_kept_launcher
from dotagent.help.apps import APP_ROOTS
from dotagent.llm.calls import log_model_call
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.opencode_go import GoUsage, OpenCodeGoProvider
from dotagent.llm.openrouter import KeyStatus, OpenRouterProvider, listed_id
from dotagent.llm.router import ModelRef, ModelRouter, parse_model_ref
from dotagent.llm.types import ChatProvider, ModelInfo
from dotagent.llm.typesafe import NoulQuestion, TypeSafeClient
from dotagent.mcp.builtin import builtin_servers, switch_of, web_research_installed
from dotagent.mcp.launch import referenced_secrets
from dotagent.memory.embedder import OpenAIEmbedder, build_openai_embedder
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import PluginCatalog, PluginProblem
from dotagent.secrets import require_secret, resolve_secret
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.office import (
    CLOUD_STORAGE,
    OFFICE_APPS,
    ONEDRIVE_APP,
    OneDriveKind,
    app_version,
    onedrive_folders,
)
from dotagent.skills.types import SkillCatalog, SkillProblem
from dotagent.store.backup import backup_state
from dotagent.tools.shell import read_login_env
from dotagent.web.client import WebClient
from dotagent.web.keyed import SEARCH_KEYS, keyed_engines
from dotagent.web.ladder import SearchEngine
from dotagent.web.search import AnthropicRung, SearchChoice, search_ladder, search_ollama
from dotagent.web.types import SearchHit

_PROBE_TIMEOUT_S: float = 5.0
_XCODE_SELECT: str = "/usr/bin/xcode-select"
_ERROR_CHARS: int = 300
_PROBLEMS_SHOWN: int = 3
# Embedding models trained mostly on English text: they match English notes by meaning, other languages by words only.
_ENGLISH_CENTRIC_EMBEDDERS: frozenset[str] = frozenset(
    {"mxbai-embed-large", "nomic-embed-text", "all-minilm", "snowflake-arctic-embed"}
)
_MULTILINGUAL_EMBEDDER: str = "bge-m3"
# The search that checks a key of Ollama's hosted web search.
_SEARCH_PROBE: str = "Ollama web search"


class CheckResult(FrozenModel):
    name: str
    ok: bool
    required: bool
    detail: str


def _result(name: str, ok: bool, required: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, ok=ok, required=required, detail=detail)


class ServerCheck(FrozenModel):
    """The row of the Ollama server, and whether the server answered at all (``reached``; a wrong answer is one)."""

    row: CheckResult
    reached: bool


async def _check_ollama_server(config: AppConfig, required: bool) -> ServerCheck:
    url: str = f"{config.providers.ollama.base_url.rstrip('/')}/api/version"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            response: httpx.Response = await client.get(url)
    except httpx.HTTPError as exc:
        detail: str = f"{url} is not reachable ({type(exc).__name__}); start Ollama"
        return ServerCheck(row=_result("ollama server", False, required, detail), reached=False)
    if response.status_code != 200:
        row: CheckResult = _result("ollama server", False, required, f"{url} answered HTTP {response.status_code}")
        return ServerCheck(row=row, reached=True)
    return ServerCheck(row=_result("ollama server", True, required, f"{url} -> {response.text.strip()}"), reached=True)


class Served(FrozenModel):
    """The names of the models a provider serves to its key, as the list it gives says, or why that list could not be
    read (``problem``, empty when it was read)."""

    names: frozenset[str]
    problem: str


async def _served(router: ModelRouter, provider: str) -> Served:
    """Read the list of the models ``provider`` serves to its key, within the time of a probe."""
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_S):
            connector: ChatProvider = await router.provider(provider)
            models: tuple[ModelInfo, ...] = await connector.list_models()
    except TimeoutError:
        return Served(names=frozenset(), problem=f"the list did not come within {_PROBE_TIMEOUT_S:g}s")
    except (ConfigError, ProviderError) as exc:
        return Served(names=frozenset(), problem=exc.message)
    return Served(names=frozenset(model.id for model in models), problem="")


def _served_by(ref: ModelRef, listing: Served) -> bool:
    """Whether the list a provider serves holds the model ``ref`` names: OpenRouter's holds a variant of a model it
    lists (``:nitro``, ``:floor``, ``:online``), which its connector knows by the listed model (item 349)."""
    if ref.provider == "openrouter":
        return listed_id(listing.names, ref.model) is not None
    return ref.model in listing.names


async def _check_model(
    router: ModelRouter, role: str, reference: str, served: Mapping[str, Served], down: Mapping[str, str]
) -> CheckResult:
    """A configured model, which its provider must serve to its key: Ollama and Anthropic answer for the model itself,
    OpenAI, OpenCode Go and OpenRouter through the list of the models they serve (``served``, by provider). When that
    list could not be read, the model is a warning, and is not asked about (OpenRouter's connector would read the same
    list again): the provider's own row fails for it. So is a model of a provider whose server could not be reached
    (``down``: what its row says, by provider), which is not asked, as the call would only wait out its retries (item
    212)."""
    try:
        ref: ModelRef = parse_model_ref(reference)
    except ConfigError as exc:
        return _result(f"model {role}", False, True, f"{reference}: {exc.message}")
    if ref.provider in down:
        return _result(f"model {role}", False, False, f"{reference}: not checked, as {down[ref.provider]}")
    listing: Served | None = served.get(ref.provider)
    if listing is not None and listing.problem:
        return _result(
            f"model {role}",
            False,
            False,
            f"{reference}: not checked, as the list of the models {ref.provider} serves could not be read "
            f"({listing.problem})",
        )
    if listing is not None and not _served_by(ref, listing):
        return _result(
            f"model {role}",
            False,
            True,
            f"{reference}: not among the {len(listing.names)} models {ref.provider} serves to this key "
            f"(dotx models {ref.provider} lists them)",
        )
    try:
        info = await router.model_info(ref)
    except (ConfigError, ProviderError) as exc:
        return _result(f"model {role}", False, True, f"{reference}: {exc.message}")
    capabilities: str = ", ".join(
        name
        for name, present in (("tools", info.tools), ("vision", info.vision), ("thinking", info.thinking))
        if present
    )
    return _result(f"model {role}", True, True, f"{reference} ({capabilities or 'text only'})")


def _review_row(models: ModelsConfig, lessons: LessonsMode) -> CheckResult:
    """The review model checks the main model's work (goal checks, second opinions, auto approvals). A main model that
    is its own review model is worth a warning, never a failure; with ``lessons`` on auto the same model also approves
    the lessons, so the warning says so."""
    if models.review.strip() == models.main.strip():
        detail: str = (
            f"{models.review} is also the main model, so it checks its own work and independent checking is weaker; "
            "a review model of another family is better"
        )
        if lessons == "auto":
            detail += (
                ". Lessons are approved by the run's own model; give [models] review another model for an independent "
                "check"
            )
        return _result("review model", False, False, detail)
    return _result("review model", True, False, f"{models.review} is not the main model")


async def _models_row(
    name: str,
    setting: str,
    models: Sequence[str],
    providers: ProvidersConfig,
    router: ModelRouter,
    served: Mapping[str, Served],
    down: Mapping[str, str],
) -> CheckResult:
    """Each helper or standby model and whether it can answer: its provider has a key and serves it to that key, as
    :func:`_check_model` checks a model (the pauses of the running daemon are not seen from here). One that cannot
    answer is worth a warning, never a failure."""
    if not models:
        return _result(name, True, False, f"none ({setting} is empty)")
    try:
        roster: Roster = roster_for(models, await keyed_providers(providers, models), {})
    except ConfigError as exc:
        return _result(name, False, False, f"{exc.message}: {', '.join(models)}")
    checks: dict[str, CheckResult] = {
        model: await _check_model(router, name, model, served, down) for model in roster.available
    }
    states: dict[str, str] = {
        model: f"{model} ready" if check.ok else check.detail for model, check in checks.items()
    } | {entry.model: unavailable_text(entry, time.time()) for entry in roster.unavailable}
    in_order: list[str] = [states[label] for label in dict.fromkeys(parse_model_ref(model).label for model in models)]
    return _result(name, any(check.ok for check in checks.values()), False, "; ".join(in_order))


def _has_secret(variable: str) -> bool:
    """Whether the key ``variable`` is set; a Keychain that does not answer or refuses is taken as no key, so that the
    checks still run (item 558): a run that needs the key is told the Keychain's own error."""
    try:
        return resolve_secret(variable) is not None
    except ConfigError:
        return False


def _check_key(provider: str, variable: str, present: bool, needed: bool) -> CheckResult:
    """A key that a configured model needs and lacks fails; one that nothing uses yet is only a pointer to add it."""
    name: str = f"{provider} key"
    if present:
        return _result(name, True, needed, f"{variable} is set")
    if needed:
        return _result(name, False, True, f"{variable} is missing (dotx auth set {variable})")
    return _result(name, False, False, f"not set; add it to use {provider} models: dotx auth set {variable}")


def _search_row(
    config: AppConfig,
    ollama_key_present: bool,
    anthropic_key_present: bool,
    keyed: frozenset[SearchEngine] = frozenset(),
    web_research: bool = False,
) -> CheckResult:
    """The engines web_search climbs, as a run does (item 655): the order Settings › Web search keeps, else what [web]
    search_backend says and what auto resolves to on this Mac (the Anthropic rung joins when its key is set, the search
    APIs whose keys are set (``keyed``) and the web-research server's search (``web_research``, when it is on and
    installed: a run climbs it once the server has started, which the server's own row says) before the scraped
    engines, the browser rung when the run's browser is Dot's own Chrome with a visible
    window)."""
    choice: SearchChoice = config.web.search_backend
    visible_own: bool = config.browser.mode == "own" and not config.browser.headless
    anthropic: AnthropicRung | None = config.web.anthropic_search if anthropic_key_present else None
    ladder: str = " -> ".join(
        search_ladder(
            choice,
            ollama_key_present,
            anthropic,
            visible_own,
            bool(config.web.searxng_url),
            tuple(config.web.search_order),
            keyed,
            web_research,
        )
    )
    if config.web.search_order:
        missing: list[str] = [
            engine for engine in config.web.search_order if engine in SEARCH_KEYS and engine not in keyed
        ]
        note: str = f"; no key for {', '.join(missing)}, passed over" if missing else ""
        return _result("web search", True, False, f"{ladder} ([web] search_order, Settings › Web search{note})")
    if choice != "auto":
        return _result("web search", True, False, f"{ladder} ([web] search_backend)")
    keys: str = ", ".join(
        f"{variable} is {'set' if present else 'not set'}"
        for variable, present in (
            (config.providers.ollama.api_key_env, ollama_key_present),
            (config.providers.anthropic.api_key_env, anthropic_key_present),
        )
    )
    return _result("web search", True, False, f"{ladder} (auto: {keys})")


def _openai_api_row(served: Served, required: bool) -> CheckResult:
    """With a key present, the list of the models the key can use (:func:`_served`): it proves the key, the project and
    the network path."""
    if served.problem:
        return _result("openai api", False, required, served.problem)
    return _result("openai api", True, required, f"{len(served.names)} chat models are available to this key")


async def _check_opencode_go_api(router: ModelRouter, required: bool) -> CheckResult:
    """With a key present, read the subscription's quota windows (a 403 means there is no active Go plan)."""
    try:
        provider: ChatProvider = await router.provider("opencode_go")
        if not isinstance(provider, OpenCodeGoProvider):
            raise ConfigError("the opencode_go connector has an unexpected type", {"type": type(provider).__name__})
        usage: GoUsage = await provider.usage()
    except (ConfigError, ProviderError) as exc:
        return _result("opencode go api", False, required, exc.message)
    windows: str = ", ".join(f"{window.name} {window.percent:g}% ({window.status})" for window in usage.windows)
    return _result("opencode go api", True, required, windows or "subscription active")


async def _check_openrouter_api(router: ModelRouter, required: bool) -> CheckResult:
    """With a key present, ask OpenRouter about the key (a free call): it proves the key, and says what it has spent and
    may spend."""
    try:
        provider: ChatProvider = await router.provider("openrouter")
        if not isinstance(provider, OpenRouterProvider):
            raise ConfigError("the openrouter connector has an unexpected type", {"type": type(provider).__name__})
        status: KeyStatus = await provider.key_status()
    except (ConfigError, ProviderError) as exc:
        return _result("openrouter api", False, required, exc.message)
    limit: str = "no limit" if status.limit is None else f"a limit of {status.limit:g}"
    tier: str = ", on the free tier" if status.free_tier else ""
    return _result("openrouter api", True, required, f"{status.usage:g} credits spent, {limit}{tier}")


async def _check_anthropic_api(router: ModelRouter, required: bool) -> CheckResult:
    """With a key present, list the models the key can use (a free call): proves the key and the network path."""
    try:
        provider: ChatProvider = await router.provider("anthropic")
        models: tuple[ModelInfo, ...] = await provider.list_models()
    except (ConfigError, ProviderError) as exc:
        return _result("anthropic api", False, required, exc.message)
    return _result("anthropic api", True, required, f"{len(models)} models are available to this key")


async def _check_ollama_search(config: AppConfig) -> CheckResult:
    """Run one search of Ollama's hosted web search, the service its key is for; it counts against the key's
    allowance of searches."""
    variable: str = config.providers.ollama.api_key_env
    key: str | None = await asyncio.to_thread(resolve_secret, variable)
    if key is None:
        return _result("ollama web search", False, True, f"{variable} is not set")
    client: WebClient = WebClient(config.web, config.browser.allowed_hosts, config.browser.blocked_hosts)
    try:
        hits: tuple[SearchHit, ...] = await search_ollama(client, key, _SEARCH_PROBE, 1)
    except WebError as exc:
        status: JsonValue = exc.context.get("status")
        detail: str = exc.message if status is None else f"{exc.message} (HTTP {status})"
        return _result("ollama web search", False, True, detail)
    finally:
        await client.aclose()
    return _result("ollama web search", True, True, f"a test search with this key worked; results: {len(hits)}")


async def _check_typesafe_key(config: JudgeConfig) -> CheckResult:
    """TypeSafe's key, with one small judgment: its API reference documents no other call to check a key with."""
    try:
        key: str = await asyncio.to_thread(require_secret, config.api_key_env, "the typed judge")
        client: TypeSafeClient = TypeSafeClient(config, key)
        try:
            await client.nouls("A key check.", (NoulQuestion(id="check", instructions="Is this a key check?"),))
        finally:
            await client.aclose()
    except (ConfigError, ProviderError) as exc:
        return _result("typesafe api", False, True, exc.message)
    return _result("typesafe api", True, True, f"{config.model} answers with this key")


def _judge_row(config: JudgeConfig, present: bool) -> CheckResult:
    """The typed judge: off, or the service it asks and whether its key is set (nothing is asked here); and whether
    the judge_items tool is offered, which only needs the key."""
    items: str = (
        f"judge_items is offered ({config.api_key_env} is set)"
        if present
        else f"judge_items is not offered until {config.api_key_env} is set"
    )
    if config.provider == "none":
        return _result("judge", True, False, f'off ([judge] provider = "none"); {items}')
    if not present:
        return _result(
            "judge",
            False,
            True,
            f"{config.api_key_env} is missing (dotx auth set {config.api_key_env}); until it is set the policy's "
            f"patterns and the goal checker decide alone, and {items}",
        )
    return _result("judge", True, True, f"{config.provider}:{config.model}; {items}")


async def check_provider_key(config: AppConfig, provider: str) -> CheckResult:
    """Ask ``provider`` whether it takes the key in use now, through connectors made for this check alone. The calls
    for OpenAI, OpenCode Go, Anthropic and OpenRouter cost nothing; Ollama's key is for its hosted web search, so it is
    checked with one search, and TypeSafe's, the typed judge's, with one small judgment."""
    if provider == "ollama":
        return await _check_ollama_search(config)
    if provider == "typesafe":
        return await _check_typesafe_key(config.judge)
    router: ModelRouter = ModelRouter(config, log_model_call, os.environ)
    try:
        match provider:
            case "openai":
                return _openai_api_row(await _served(router, "openai"), True)
            case "opencode_go":
                return await _check_opencode_go_api(router, True)
            case "anthropic":
                return await _check_anthropic_api(router, True)
            case "openrouter":
                return await _check_openrouter_api(router, True)
            case _:
                raise ConfigError("no key check exists for this provider", {"provider": provider})
    finally:
        await router.aclose()


def model_is_installed(installed: tuple[str, ...], wanted: str) -> bool:
    """Whether ``wanted`` is among the ``installed`` names; Ollama reads a name without a tag as ``:latest``."""
    full: str = wanted if ":" in wanted else f"{wanted}:latest"
    return full in installed


async def _check_openai_embeddings(config: AppConfig) -> CheckResult:
    """With the OpenAI embedder: the key, the project's access to the model and the network path (a free call)."""
    wanted: str = config.memory.embed_model
    base_url: str = config.providers.openai.base_url
    try:
        embedder: OpenAIEmbedder = await build_openai_embedder(config)
        try:
            served: str = await embedder.served_model()
        finally:
            await embedder.aclose()
    except ConfigError as exc:
        hint: str = str(exc.context.get("hint", ""))
        return _result("memory", False, True, f"openai:{wanted} cannot be used for notes: {exc.message} ({hint})")
    except ProviderError as exc:
        return _result("memory", False, True, f"openai:{wanted} cannot be used for notes: {exc.message}")
    return _result(
        "memory",
        True,
        False,
        f"openai:{served or wanted} at {base_url} (notes are found by meaning and by words; their text is sent "
        f"there). The [memory] similarity thresholds are tuned for {_MULTILINGUAL_EMBEDDER}: check them for this model",
    )


async def _check_memory(router: ModelRouter, app: AppConfig, down: Mapping[str, str]) -> CheckResult:
    """With embeddings on, the embedding model must be reachable: installed in Ollama (an English-centric one gets a
    hint) or served to the OpenAI key. An Ollama server that could not be reached (``down``) is not asked (item 212)."""
    config: MemoryConfig = app.memory
    if not config.embed_enabled:
        return _result("memory", True, False, "notes are searched by words only ([memory] embed_enabled = false)")
    if config.embed_provider == "openai":
        return await _check_openai_embeddings(app)
    if "ollama" in down:
        return _result("memory", False, True, f"the embedding models cannot be listed, as {down['ollama']}")
    try:
        provider: ChatProvider = await router.provider("ollama")
        if not isinstance(provider, OllamaProvider):
            raise ConfigError("the ollama connector has an unexpected type", {"type": type(provider).__name__})
        installed: tuple[str, ...] = await provider.installed_names()
    except (ConfigError, ProviderError) as exc:
        return _result("memory", False, True, f"the embedding models cannot be listed: {exc.message}")
    if not model_is_installed(installed, config.embed_model):
        return _result(
            "memory",
            False,
            True,
            f"{config.embed_model} is not installed: ollama pull {config.embed_model} "
            "(or switch embeddings off with [memory] embed_enabled = false)",
        )
    if config.embed_model.partition(":")[0] in _ENGLISH_CENTRIC_EMBEDDERS:
        return _result(
            "memory",
            False,
            False,
            f"{config.embed_model} knows English best; notes in other languages are found by their words, not their "
            f"meaning. For meaning in Turkish: ollama pull {_MULTILINGUAL_EMBEDDER}, set [memory] embed_model, then "
            "dotx memory reindex",
        )
    return _result("memory", True, False, f"{config.embed_model} (notes are found by meaning and by words)")


async def _check_browser_launch(config: AppConfig) -> CheckResult:
    """Start a throwaway headless Chrome through Playwright: proves the channel and the driver work."""
    channel: str = config.browser.channel
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=channel, headless=True)
            version: str = browser.version
            await browser.close()
    except PlaywrightError as exc:
        return _result(
            "browser launch", False, False, f"{channel} did not start: {truncate_middle(str(exc), _ERROR_CHARS)}"
        )
    return _result("browser launch", True, False, f"{channel} {version} starts under Playwright")


def _check_browser_profile(config: AppConfig) -> CheckResult:
    """The profile of Dot's Chrome: free, or open in the Chrome Playwright started for a run, which is as it should be;
    open in any other Chrome window (Sign in…, or one the owner opened), which keeps a run's from starting, it needs a
    look."""
    profile: Path = profile_dir(config.browser.profile)
    holder: int | None = profile_holder_pid(profile)
    keychain: KeychainMode = read_keychain_mode(profile)
    fine: bool = True
    state: str = "free"
    if holder is not None and started_by_dot(holder):
        state = f"in use by a run's Chrome, pid {holder}"
    elif holder is not None:
        fine = False
        state = (
            f"open in pid {holder}, a Chrome window that is not a run's (Sign in… opens one): quit it for a run's "
            "to start"
        )
    return _result(
        "browser profile",
        fine,
        False,
        f"{profile} ({state}, {keychain} keychain); sign in with: dotx browser login, "
        "or copy your Chrome sign-ins with: dotx browser import-chrome",
    )


def _check_browser_mode(config: AppConfig) -> CheckResult:
    """Which Chrome the browser tools use. In attach mode it looks at the files of the open Chrome and whether its port
    answers; no WebSocket handshake happens, so Chrome does not ask anyone to click Allow."""
    if config.browser.mode == "own":
        return _result(
            "browser mode",
            True,
            False,
            "own: Dot starts a Chrome of its own; to work in the Chrome you have open instead: dotx browser attach",
        )
    try:
        endpoint: DebugEndpoint = read_debug_endpoint(chrome_data_dir())
    except BrowserError as exc:
        return _result("browser mode", False, False, f"attach: {exc.message}")
    return _result(
        "browser mode",
        True,
        False,
        f"attach: your Chrome is open and listens on port {endpoint.port}; Chrome asks you to click Allow "
        "when Dot connects",
    )


def _codes_row(config: AppConfig) -> CheckResult:
    """Where code_fetch may read one-time codes from, and whether the Messages database can be opened."""
    sources: list[str] = list(config.policy.code_sources)
    if not sources:
        return _result("one-time codes", True, False, "off ([policy] code_sources is empty)")
    detail: str = ", ".join(sources)
    if "messages" in sources:
        db: Path = chat_db_path()
        try:
            sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True).close()
        except sqlite3.OperationalError:
            return _result("one-time codes", False, False, f"{detail}; {db} cannot be opened (Full Disk Access)")
        detail = f"{detail}; {db} opens"
    return _result("one-time codes", True, False, detail)


def _ripgrep_row() -> CheckResult:
    ripgrep: str | None = shutil.which("rg")
    return _result("ripgrep", ripgrep is not None, True, ripgrep or "install with: brew install ripgrep")


def _chrome_row() -> CheckResult:
    chrome_present: bool = CHROME_APP.exists()
    return _result(
        "google chrome",
        chrome_present,
        False,
        str(CHROME_APP) if chrome_present else "the browser tools use Chrome; install it",
    )


def _local_checks() -> list[CheckResult]:
    """Checks that only touch the local filesystem and PATH."""
    env_state: str = "present" if env_file_path().exists() else "absent"
    return [
        _result("home directory", dot_home().is_dir(), True, str(dot_home())),
        _result("env file", True, False, f"{env_file_path()} ({env_state})"),
        _ripgrep_row(),
        _chrome_row(),
    ]


def _check_screen_capture_support() -> CheckResult:
    """The desktop tools see the screen through ScreenCaptureKit's rectangle capture, which macOS has from 15.2 on."""
    version: str = platform.mac_ver()[0]
    if rect_capture_supported():
        return _result("desktop macos", True, False, f"macOS {version} can capture the screen")
    return _result(
        "desktop macos",
        False,
        False,
        f"macOS {version} cannot capture the screen: the desktop tools need macOS 15.2 or later; update macOS",
    )


def _xcode_tools_row(required: bool) -> CheckResult:
    """Xcode's Command Line Tools, with which the service builds Dot.app on this Mac (:mod:`dotagent.dot_app`):
    ``xcode-select -p`` names their folder, and fails without them, without the dialog that installs them. With a
    prebuilt program named in ``DOT_APP_LAUNCHER`` (the desktop app's, item 575), or kept in the Dot.app that is there
    (``dotx setup`` typed in a terminal on an install of the app has no such variable), there is nothing to build."""
    if os.environ.get(LAUNCHER_ENV, "") != "" or has_kept_launcher(dot_home()):
        return _result("xcode tools", True, False, "not needed: the desktop app carries the program Dot.app is made of")
    try:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            [_XCODE_SELECT, "-p"], capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired:
        return _result("xcode tools", False, required, f"xcode-select did not answer within {_PROBE_TIMEOUT_S:g}s")
    if completed.returncode != 0:
        return _result(
            "xcode tools",
            False,
            required,
            "the service builds Dot.app with them: install them with xcode-select --install",
        )
    return _result("xcode tools", True, required, completed.stdout.strip())


def setup_checks(service: bool) -> list[CheckResult]:
    """What ``dotx setup`` checks of this Mac before it chooses a model: the macOS version, ripgrep, Chrome, and Xcode's
    Command Line Tools, which the service needs (``service``: it is installed after)."""
    return [_check_screen_capture_support(), _ripgrep_row(), _chrome_row(), _xcode_tools_row(service)]


_DESKTOP_FIX: str = (
    "not granted to the app that runs Dot: dotx desktop setup (terminal) or dotx service permissions (service)"
)


def _check_desktop(config: AppConfig) -> list[CheckResult]:
    """The macOS version and the privacy permissions the desktop tools need, as macOS reports them for the app that
    runs Dot."""
    if not config.desktop.enabled:
        return [_result("desktop", True, False, "switched off ([desktop] enabled = false)")]
    permissions: Permissions = check_permissions()
    rows: tuple[tuple[str, bool], ...] = (
        ("desktop accessibility", permissions.accessibility),
        ("desktop input events", permissions.post_event),
        ("desktop screen capture", permissions.screen_recording),
    )
    return [
        _check_screen_capture_support(),
        *(
            _result(
                name,
                granted,
                False,
                "granted" if granted else _DESKTOP_FIX,
            )
            for name, granted in rows
        ),
    ]


def _find_command(command: str, login_path: str | None) -> str | None:
    """Where an MCP server's ``command`` is: an executable file when written as a path (a relative one starts from the
    workspace, where the server is started), else a name on the login PATH."""
    if os.sep in command:
        candidate: Path = workspace_dir() / command  # an absolute command replaces the workspace in the join
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(command, path=login_path)


def _mcp_row(
    config: McpServerConfig, found: str | None, missing_secrets: list[str], built_in: bool = False
) -> CheckResult:
    """The row of one MCP server from what was found on this Mac. A server that cannot start never stops Dot, which
    starts without its tools. A server that comes with Dot (``built_in``, item 583) is switched by its own setting."""
    name: str = f"mcp {config.name}"
    off: str = f"[builtin_mcp] {switch_of(config.name)} = false" if built_in else "[[mcp]] enabled = false"
    if not config.enabled:
        return _result(name, True, False, f"switched off ({off})")
    problems: list[str] = []
    if config.command is not None and found is None:
        problems.append(f"{config.command} is not installed or not on the login shell's PATH")
    if missing_secrets:
        problems.append(f"secrets not set: {', '.join(missing_secrets)} (dotx auth set NAME)")
    if problems:
        return _result(
            name,
            False,
            False,
            f"{'; '.join(problems)}. Dot starts without its tools while it fails; {off} stops trying it",
        )
    where: str = f"{config.url} over {config.transport_kind()}" if found is None else found
    return _result(
        name, True, False, f"{'built in, ' if built_in else ''}{where}; connect for real with: dotx mcp list"
    )


async def _mcp_server_row(config: McpServerConfig, login_path: str | None, built_in: bool) -> CheckResult:
    if not config.enabled:
        return _mcp_row(config, None, [], built_in)
    found: str | None = None
    if config.command is not None:
        found = await asyncio.to_thread(_find_command, config.command, login_path)
    names: list[str] = referenced_secrets(config)
    present: list[bool] = list(await asyncio.gather(*(asyncio.to_thread(_has_secret, name) for name in names)))
    missing: list[str] = [name for name, there in zip(names, present, strict=True) if not there]
    return _mcp_row(config, found, missing, built_in)


async def mcp_checks(
    servers: list[McpServerConfig], login_env_timeout_s: float, built_in: frozenset[str] = frozenset()
) -> list[CheckResult]:
    """One row per configured MCP server, and per server that comes with Dot (``built_in``, the names of those among
    ``servers``): its command is installed (a server reached at a URL has none) and its secrets are set. Nothing is
    started or contacted."""
    if not servers:
        return [_result("mcp", True, False, f"no servers configured (add [[mcp]] tables to {config_path()})")]
    login_path: str | None = None
    if any(server.enabled and server.command is not None for server in servers):
        try:
            login_env: dict[str, str] = await read_login_env(login_env_timeout_s, ())
        except ToolExecutionError as exc:
            return [
                _result(
                    "login shell", False, True, f"its environment cannot be read, so no tool is found: {exc.message}"
                )
            ]
        login_path = login_env.get("PATH")
    return list(
        await asyncio.gather(*(_mcp_server_row(server, login_path, server.name in built_in) for server in servers))
    )


def _left_out(problems: Sequence[PluginProblem] | Sequence[SkillProblem]) -> str:
    shown: str = "; ".join(f"{problem.path}: {problem.reason}" for problem in problems[:_PROBLEMS_SHOWN])
    more: int = len(problems) - _PROBLEMS_SHOWN
    return shown if more <= 0 else f"{shown}; and {more} more (dotx skills list shows all)"


def _plugins_row(catalog: PluginCatalog) -> CheckResult:
    """The plugins found, and whatever had to be left out; a plugin with a problem never stops Dot."""
    names: str = ", ".join(plugin.name for plugin in catalog.plugins)
    if catalog.problems:
        found: str = f"{len(catalog.plugins)} plugin(s) ({names}); " if catalog.plugins else ""
        return _result("plugins", False, False, f"{found}left out: {_left_out(catalog.problems)}")
    if not catalog.plugins:
        return _result("plugins", True, False, f"none installed (put them in {plugins_dir()} or in [plugins] dirs)")
    servers: int = sum(len(plugin.servers) for plugin in catalog.plugins)
    return _result(
        "plugins", True, False, f"{len(catalog.plugins)} plugin(s): {names}; {servers} MCP server(s) of theirs"
    )


def _skills_row(catalog: SkillCatalog) -> CheckResult:
    """The skills found, and whatever had to be left out; a folder with a problem never stops Dot. A skill another one
    has the name of is said, not counted against the row: nothing is wrong with it, and one of the two is offered."""
    names: str = ", ".join(skill.name for skill in catalog.skills)
    broken: list[SkillProblem] = [problem for problem in catalog.problems if not problem.shadowed]
    shadowed: int = len(catalog.problems) - len(broken)
    if broken:
        found: str = f"{len(catalog.skills)} skill(s) ({names}); " if catalog.skills else ""
        return _result("skills", False, False, f"{found}left out: {_left_out(broken)}")
    if not catalog.skills:
        return _result("skills", True, False, f"none installed (put them in {skills_dir()} or in [skills] dirs)")
    aside: str = (
        f"; {shadowed} not offered, as another skill has the name (dotx skills list shows which)" if shadowed else ""
    )
    return _result("skills", True, False, f"{len(catalog.skills)} skill(s): {names}{aside}")


def _office_row(versions: Mapping[str, str | None]) -> CheckResult:
    """The applications the Office skills drive (item 196), by name and version (``None``: not installed). None of them
    is needed: the skills change Office files with Python's libraries."""
    found: list[str] = [f"{name} {version}".strip() for name, version in versions.items() if version is not None]
    missing: list[str] = [name for name, version in versions.items() if version is None]
    parts: list[str] = [*found, *([f"not installed: {', '.join(missing)}"] if missing else [])]
    return _result("office apps", True, False, "; ".join(parts))


def _onedrive_row(app: str | None, kinds: Sequence[OneDriveKind]) -> CheckResult:
    """The folders OneDrive syncs on this Mac (item 196), counted by the kind of account, and the version of the
    OneDrive app (``None``: not installed). An app that syncs no folder is not signed in."""
    if not kinds:
        if app is None:
            return _result("onedrive", True, False, f"not installed, and no OneDrive folder in {CLOUD_STORAGE}")
        return _result(
            "onedrive", False, False, f"OneDrive {app} syncs no folder to {CLOUD_STORAGE}: sign in in the OneDrive app"
        )
    counted: str = ", ".join(f"{count} {kind}" for kind, count in Counter(kinds).items())
    folders: str = f"{len(kinds)} folder(s) in {CLOUD_STORAGE} ({counted})"
    return _result("onedrive", True, False, folders if app is None else f"OneDrive {app}; {folders}")


def _office_checks() -> list[CheckResult]:
    """The rows of the skills that come with Dot for Office files and OneDrive; nothing is started."""
    return [
        _office_row({name: app_version(APP_ROOTS, name) for name in OFFICE_APPS}),
        _onedrive_row(app_version(APP_ROOTS, ONEDRIVE_APP), onedrive_folders(CLOUD_STORAGE)),
    ]


def _backup_row(config: AppConfig) -> CheckResult:
    """How fresh the copies of the database are (item 526); never required, as Dot runs without them."""
    fresh, detail = backup_state(config.store, db_path(), time.time())
    return _result("backups", fresh, False, detail)


async def _machine_checks(config: AppConfig) -> list[CheckResult]:
    """What this Mac provides: the home directory, tools, desktop support, the browser profile, the plugins and skills,
    the Office applications and OneDrive folders, and the MCP servers (the configured ones, those of the plugins and
    those that come with Dot)."""
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    skills: SkillCatalog = await asyncio.to_thread(load_skills, config.skills, plugins.plugins, playbook_root(config))
    configured: list[McpServerConfig] = [*config.mcp, *plugins.servers()]
    bundled: tuple[McpServerConfig, ...] = builtin_servers(config, frozenset(server.name for server in configured))
    return [
        *await asyncio.to_thread(_local_checks),
        *_check_desktop(config),
        await asyncio.to_thread(_codes_row, config),
        await asyncio.to_thread(_backup_row, config),
        await asyncio.to_thread(_check_browser_mode, config),
        await asyncio.to_thread(_check_browser_profile, config),
        _plugins_row(plugins),
        _skills_row(skills),
        *await asyncio.to_thread(_office_checks),
        *await mcp_checks(
            [*configured, *bundled], config.shell.login_env_timeout_s, frozenset(server.name for server in bundled)
        ),
    ]


async def _service_checks(config: AppConfig) -> list[CheckResult]:
    """The model services and the memory embedder. A failing check never stops the others."""
    configured: tuple[tuple[str, str], ...] = (
        ("main", config.models.main),
        ("vision", config.models.vision),
        ("fast", config.models.fast),
        ("review", config.models.review),
    )
    used: set[str] = {reference.partition(":")[0] for _, reference in configured}
    router: ModelRouter = ModelRouter(config, log_model_call, os.environ)
    openai_present: bool = await asyncio.to_thread(_has_secret, config.providers.openai.api_key_env)
    go_present: bool = await asyncio.to_thread(_has_secret, config.providers.opencode_go.api_key_env)
    anthropic_present: bool = await asyncio.to_thread(_has_secret, config.providers.anthropic.api_key_env)
    openrouter_present: bool = await asyncio.to_thread(_has_secret, config.providers.openrouter.api_key_env)
    ollama_present: bool = await asyncio.to_thread(_has_secret, config.providers.ollama.api_key_env)
    server: ServerCheck = await _check_ollama_server(config, "ollama" in used)
    down: dict[str, str] = {} if server.reached else {"ollama": server.row.detail}
    results: list[CheckResult] = [
        server.row,
        _check_key("openai", config.providers.openai.api_key_env, openai_present, "openai" in used),
        _check_key("opencode go", config.providers.opencode_go.api_key_env, go_present, "opencode_go" in used),
        _check_key("anthropic", config.providers.anthropic.api_key_env, anthropic_present, "anthropic" in used),
        _check_key("openrouter", config.providers.openrouter.api_key_env, openrouter_present, "openrouter" in used),
        _search_row(
            config,
            ollama_present,
            anthropic_present,
            await keyed_engines(),
            config.builtin_mcp.web_research and web_research_installed(),
        ),
    ]
    try:
        # The connectors of OpenAI, OpenCode Go and OpenRouter know a model by its name alone until it is called, so the
        # list of the models each serves is read once, and each configured model of theirs is checked against it.
        served: dict[str, Served] = {
            provider: await _served(router, provider)
            for provider, present in (
                ("openai", openai_present),
                ("opencode_go", go_present),
                ("openrouter", openrouter_present),
            )
            if present
        }
        if openai_present:
            results.append(_openai_api_row(served["openai"], "openai" in used))
        if go_present:
            results.append(await _check_opencode_go_api(router, "opencode_go" in used))
        if anthropic_present:
            results.append(await _check_anthropic_api(router, "anthropic" in used))
        if openrouter_present:
            results.append(await _check_openrouter_api(router, "openrouter" in used))
        results.append(await _check_memory(router, config, down))
        for role, reference in configured:
            results.append(await _check_model(router, role, reference, served, down))
        results.append(_review_row(config.models, config.agent.lessons))
        for name, setting, models in (
            ("helpers", "[models] helpers", config.models.helpers),
            ("standby", "[models] standby", config.models.standby),
        ):
            results.append(await _models_row(name, setting, models, config.providers, router, served, down))
    finally:
        await router.aclose()
    judge_key: bool = await asyncio.to_thread(_has_secret, config.judge.api_key_env)
    results.append(_judge_row(config.judge, judge_key))
    return results


async def run_checks(config: AppConfig) -> list[CheckResult]:
    """Run every diagnostic, including a throwaway start of Chrome, which takes a while."""
    machine: list[CheckResult] = await _machine_checks(config)
    launch: CheckResult = await _check_browser_launch(config)
    return [*machine, launch, *await _service_checks(config)]


async def run_quick_checks(config: AppConfig) -> list[CheckResult]:
    """Every diagnostic except the throwaway Chrome start, so it is fast enough to run whenever the UI asks."""
    return [*await _machine_checks(config), *await _service_checks(config)]
