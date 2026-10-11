"""Environment diagnostics for ``dotx doctor``: what works, what is missing and how to fix it. The check of one
provider's key is the one the API keys page of the settings runs."""

import asyncio
import os
import platform
import shutil
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from pydantic import JsonValue

from dotagent.agent.helpers import Roster, keyed_providers, roster_for, unavailable_text
from dotagent.browser.attach import DebugEndpoint, read_debug_endpoint
from dotagent.browser.keychain import KeychainMode, read_keychain_mode
from dotagent.browser.login import CHROME_APP
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.config import AppConfig, JudgeConfig, McpServerConfig, MemoryConfig, ModelsConfig, ProvidersConfig
from dotagent.core.errors import BrowserError, ConfigError, ProviderError, ToolExecutionError, WebError
from dotagent.core.paths import (
    browser_profile_dir,
    chrome_data_dir,
    config_path,
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
from dotagent.llm.calls import log_model_call
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.opencode_go import GoUsage, OpenCodeGoProvider
from dotagent.llm.router import ModelRef, ModelRouter, parse_model_ref
from dotagent.llm.types import ChatProvider, ModelInfo
from dotagent.llm.typesafe import NoulQuestion, TypeSafeClient
from dotagent.mcp.launch import referenced_secrets
from dotagent.memory.embedder import OpenAIEmbedder, build_openai_embedder
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import PluginCatalog, PluginProblem
from dotagent.secrets import require_secret, resolve_secret
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import SkillCatalog, SkillProblem
from dotagent.tools.shell import read_login_env
from dotagent.web.client import WebClient
from dotagent.web.search import SearchChoice, search_ladder, search_ollama
from dotagent.web.types import SearchHit

_PROBE_TIMEOUT_S: float = 5.0
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


async def _check_ollama_server(config: AppConfig, required: bool) -> CheckResult:
    url: str = f"{config.providers.ollama.base_url.rstrip('/')}/api/version"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            response: httpx.Response = await client.get(url)
    except httpx.HTTPError as exc:
        return _result("ollama server", False, required, f"{url} is not reachable ({type(exc).__name__}); start Ollama")
    if response.status_code != 200:
        return _result("ollama server", False, required, f"{url} answered HTTP {response.status_code}")
    return _result("ollama server", True, required, f"{url} -> {response.text.strip()}")


async def _check_model(router: ModelRouter, role: str, reference: str) -> CheckResult:
    try:
        ref: ModelRef = parse_model_ref(reference)
        info = await router.model_info(ref)
    except (ConfigError, ProviderError) as exc:
        return _result(f"model {role}", False, True, f"{reference}: {exc.message}")
    capabilities: str = ", ".join(
        name
        for name, present in (("tools", info.tools), ("vision", info.vision), ("thinking", info.thinking))
        if present
    )
    return _result(f"model {role}", True, True, f"{reference} ({capabilities or 'text only'})")


def _review_row(models: ModelsConfig) -> CheckResult:
    """The review model checks the main model's work (goal checks, second opinions, auto approvals). A main model that
    is its own review model is worth a warning, never a failure."""
    if models.review.strip() == models.main.strip():
        return _result(
            "review model",
            False,
            False,
            f"{models.review} is also the main model, so it checks its own work and independent checking is weaker; "
            "a review model of another family is better",
        )
    return _result("review model", True, False, f"{models.review} is not the main model")


async def _models_row(name: str, setting: str, models: Sequence[str], providers: ProvidersConfig) -> CheckResult:
    """Each helper or standby model and whether it can answer, as far as its provider's key tells (the pauses of the
    running daemon are not seen from here). One that cannot answer is worth a warning, never a failure."""
    if not models:
        return _result(name, True, False, f"none ({setting} is empty)")
    try:
        roster: Roster = roster_for(models, await keyed_providers(providers, models), {})
    except ConfigError as exc:
        return _result(name, False, False, f"{exc.message}: {', '.join(models)}")
    states: dict[str, str] = {model: f"{model} ready" for model in roster.available} | {
        entry.model: unavailable_text(entry, time.time()) for entry in roster.unavailable
    }
    in_order: list[str] = [states[label] for label in dict.fromkeys(parse_model_ref(model).label for model in models)]
    return _result(name, bool(roster.available), False, "; ".join(in_order))


def _has_secret(variable: str) -> bool:
    return resolve_secret(variable) is not None


def _check_key(provider: str, variable: str, present: bool, needed: bool) -> CheckResult:
    """A key that a configured model needs and lacks fails; one that nothing uses yet is only a pointer to add it."""
    name: str = f"{provider} key"
    if present:
        return _result(name, True, needed, f"{variable} is set")
    if needed:
        return _result(name, False, True, f"{variable} is missing (dotx auth set {variable})")
    return _result(name, False, False, f"not set; add it to use {provider} models: dotx auth set {variable}")


def _search_row(config: AppConfig, ollama_key_present: bool) -> CheckResult:
    """The engines web_search climbs: what [web] search_backend says, and what auto resolves to on this Mac (the browser
    rung joins when the run's browser is Dot's own Chrome with a visible window)."""
    choice: SearchChoice = config.web.search_backend
    visible_own: bool = config.browser.mode == "own" and not config.browser.headless
    ladder: str = " -> ".join(search_ladder(choice, ollama_key_present, visible_own))
    variable: str = config.providers.ollama.api_key_env
    if choice != "auto":
        return _result("web search", True, False, f"{ladder} ([web] search_backend)")
    reason: str = f"{variable} is set" if ollama_key_present else f"{variable} is not set"
    return _result("web search", True, False, f"{ladder} (auto: {reason})")


async def _check_openai_api(router: ModelRouter, required: bool) -> CheckResult:
    """With a key present, list the models the key can use: proves the key, the project and the network path."""
    try:
        provider: ChatProvider = await router.provider("openai")
        models: tuple[ModelInfo, ...] = await provider.list_models()
    except (ConfigError, ProviderError) as exc:
        return _result("openai api", False, required, exc.message)
    return _result("openai api", True, required, f"{len(models)} chat models are available to this key")


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
    for OpenAI, OpenCode Go and Anthropic cost nothing; Ollama's key is for its hosted web search, so it is checked with
    one search, and TypeSafe's, the typed judge's, with one small judgment."""
    if provider == "ollama":
        return await _check_ollama_search(config)
    if provider == "typesafe":
        return await _check_typesafe_key(config.judge)
    router: ModelRouter = ModelRouter(config, log_model_call)
    try:
        match provider:
            case "openai":
                return await _check_openai_api(router, True)
            case "opencode_go":
                return await _check_opencode_go_api(router, True)
            case "anthropic":
                return await _check_anthropic_api(router, True)
            case _:
                raise ConfigError("no key check exists for this provider", {"provider": provider})
    finally:
        await router.aclose()


def _model_is_installed(installed: tuple[str, ...], wanted: str) -> bool:
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


async def _check_memory(router: ModelRouter, app: AppConfig) -> CheckResult:
    """With embeddings on, the embedding model must be reachable: installed in Ollama (an English-centric one gets a
    hint) or served to the OpenAI key."""
    config: MemoryConfig = app.memory
    if not config.embed_enabled:
        return _result("memory", True, False, "notes are searched by words only ([memory] embed_enabled = false)")
    if config.embed_provider == "openai":
        return await _check_openai_embeddings(app)
    try:
        provider: ChatProvider = await router.provider("ollama")
        if not isinstance(provider, OllamaProvider):
            raise ConfigError("the ollama connector has an unexpected type", {"type": type(provider).__name__})
        installed: tuple[str, ...] = await provider.installed_names()
    except (ConfigError, ProviderError) as exc:
        return _result("memory", False, True, f"the embedding models cannot be listed: {exc.message}")
    if not _model_is_installed(installed, config.embed_model):
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


def _check_browser_profile() -> CheckResult:
    profile: Path = browser_profile_dir()
    holder: int | None = profile_holder_pid(profile)
    keychain: KeychainMode = read_keychain_mode(profile)
    state: str = f"in use by pid {holder}" if holder is not None else "free"
    return _result(
        "browser profile",
        holder is None,
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


def _local_checks() -> list[CheckResult]:
    """Checks that only touch the local filesystem and PATH."""
    chrome_present: bool = CHROME_APP.exists()
    ripgrep: str | None = shutil.which("rg")
    env_state: str = "present" if env_file_path().exists() else "absent"
    return [
        _result("home directory", dot_home().is_dir(), True, str(dot_home())),
        _result("env file", True, False, f"{env_file_path()} ({env_state})"),
        _result("ripgrep", ripgrep is not None, True, ripgrep or "install with: brew install ripgrep"),
        _result(
            "google chrome",
            chrome_present,
            False,
            str(CHROME_APP) if chrome_present else "the browser tools use Chrome; install it",
        ),
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


def _mcp_row(config: McpServerConfig, found: str | None, missing_secrets: list[str]) -> CheckResult:
    """The row of one MCP server from what was found on this Mac. Dot does not start while an enabled one fails."""
    name: str = f"mcp {config.name}"
    if not config.enabled:
        return _result(name, True, False, "switched off ([[mcp]] enabled = false)")
    problems: list[str] = []
    if config.command is not None and found is None:
        problems.append(f"{config.command} is not installed or not on the login shell's PATH")
    if missing_secrets:
        problems.append(f"secrets not set: {', '.join(missing_secrets)} (dotx auth set NAME)")
    if problems:
        return _result(
            name,
            False,
            True,
            f"{'; '.join(problems)}. Dot does not start while an enabled server fails; enabled = false skips it",
        )
    where: str = f"{config.url} over {config.transport_kind()}" if found is None else found
    return _result(name, True, True, f"{where}; connect for real with: dotx mcp list")


async def _mcp_server_row(config: McpServerConfig, login_path: str | None) -> CheckResult:
    if not config.enabled:
        return _mcp_row(config, None, [])
    found: str | None = None
    if config.command is not None:
        found = await asyncio.to_thread(_find_command, config.command, login_path)
    names: list[str] = referenced_secrets(config)
    present: list[bool] = list(await asyncio.gather(*(asyncio.to_thread(_has_secret, name) for name in names)))
    missing: list[str] = [name for name, there in zip(names, present, strict=True) if not there]
    return _mcp_row(config, found, missing)


async def mcp_checks(servers: list[McpServerConfig], login_env_timeout_s: float) -> list[CheckResult]:
    """One row per configured MCP server: its command is installed (a server reached at a URL has none) and its
    secrets are set. Nothing is started or contacted."""
    if not servers:
        return [_result("mcp", True, False, f"no servers configured (add [[mcp]] tables to {config_path()})")]
    login_path: str | None = None
    if any(server.enabled and server.command is not None for server in servers):
        try:
            login_env: dict[str, str] = await read_login_env(login_env_timeout_s)
        except ToolExecutionError as exc:
            return [
                _result(
                    "login shell", False, True, f"its environment cannot be read, so no tool is found: {exc.message}"
                )
            ]
        login_path = login_env.get("PATH")
    return list(await asyncio.gather(*(_mcp_server_row(server, login_path) for server in servers)))


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
    """The skills found, and whatever had to be left out; a folder with a problem never stops Dot."""
    names: str = ", ".join(skill.name for skill in catalog.skills)
    if catalog.problems:
        found: str = f"{len(catalog.skills)} skill(s) ({names}); " if catalog.skills else ""
        return _result("skills", False, False, f"{found}left out: {_left_out(catalog.problems)}")
    if not catalog.skills:
        return _result("skills", True, False, f"none installed (put them in {skills_dir()} or in [skills] dirs)")
    return _result("skills", True, False, f"{len(catalog.skills)} skill(s): {names}")


async def _machine_checks(config: AppConfig) -> list[CheckResult]:
    """What this Mac provides: the home directory, tools, desktop support, the browser profile, the plugins and skills,
    and the MCP servers (the configured ones and those of the plugins)."""
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    skills: SkillCatalog = await asyncio.to_thread(load_skills, config.skills, plugins.plugins, playbook_root(config))
    return [
        *await asyncio.to_thread(_local_checks),
        *_check_desktop(config),
        await asyncio.to_thread(_codes_row, config),
        await asyncio.to_thread(_check_browser_mode, config),
        await asyncio.to_thread(_check_browser_profile),
        _plugins_row(plugins),
        _skills_row(skills),
        *await mcp_checks([*config.mcp, *plugins.servers()], config.shell.login_env_timeout_s),
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
    router: ModelRouter = ModelRouter(config, log_model_call)
    openai_present: bool = await asyncio.to_thread(_has_secret, config.providers.openai.api_key_env)
    go_present: bool = await asyncio.to_thread(_has_secret, config.providers.opencode_go.api_key_env)
    anthropic_present: bool = await asyncio.to_thread(_has_secret, config.providers.anthropic.api_key_env)
    ollama_present: bool = await asyncio.to_thread(_has_secret, config.providers.ollama.api_key_env)
    results: list[CheckResult] = [
        await _check_ollama_server(config, "ollama" in used),
        _check_key("openai", config.providers.openai.api_key_env, openai_present, "openai" in used),
        _check_key("opencode go", config.providers.opencode_go.api_key_env, go_present, "opencode_go" in used),
        _check_key("anthropic", config.providers.anthropic.api_key_env, anthropic_present, "anthropic" in used),
        _search_row(config, ollama_present),
    ]
    try:
        if openai_present:
            results.append(await _check_openai_api(router, "openai" in used))
        if go_present:
            results.append(await _check_opencode_go_api(router, "opencode_go" in used))
        if anthropic_present:
            results.append(await _check_anthropic_api(router, "anthropic" in used))
        results.append(await _check_memory(router, config))
        for role, reference in configured:
            results.append(await _check_model(router, role, reference))
        results.append(_review_row(config.models))
        results.append(await _models_row("helpers", "[models] helpers", config.models.helpers, config.providers))
        results.append(await _models_row("standby", "[models] standby", config.models.standby, config.providers))
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
