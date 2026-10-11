"""The shared runtime: the configuration plus every long-lived service a run needs."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.judge import Judge, TypeSafeService, judge_for
from dotagent.browser.pool import BrowserPool
from dotagent.browser.session import BrowserSession
from dotagent.config import AppConfig
from dotagent.core.errors import StoreError
from dotagent.core.logs import get_logger
from dotagent.core.notify import Notifier, show_notification
from dotagent.core.paths import (
    browser_profile_dir,
    chrome_data_dir,
    config_path,
    db_path,
    downloads_dir,
    ensure_layout,
    env_file_path,
    failsafe_path,
    screenshots_dir,
    token_path,
    undo_dir,
    vapid_path,
    workspace_dir,
)
from dotagent.desktop.session import DesktopSession
from dotagent.events import EventBus
from dotagent.llm.calls import CALL_EVENT, CallRecorder, ModelCall, call_event_data, log_model_call
from dotagent.llm.router import ModelRouter
from dotagent.mcp.hub import McpHub, open_hub
from dotagent.memory.service import MemoryIndex, close_memory_index, open_memory_index
from dotagent.notify.push import PushKeys, read_or_create_keys
from dotagent.plugins.catalog import load_plugins
from dotagent.plugins.types import PluginCatalog
from dotagent.policy.paths import expand_protected
from dotagent.scheduler.timing import resolve_zone
from dotagent.secrets import resolve_secret
from dotagent.store.db import Store
from dotagent.terminal.session import Terminals
from dotagent.tools.apple import build_apple_tool
from dotagent.tools.base import RegisteredTool
from dotagent.tools.browser import build_browser_tools
from dotagent.tools.campaign import build_campaign_tools
from dotagent.tools.codes import build_code_tools
from dotagent.tools.consult import build_consult_tools
from dotagent.tools.delegate import build_delegate_tools
from dotagent.tools.desktop import build_desktop_tools, sized_for_agent
from dotagent.tools.files import build_file_tools
from dotagent.tools.judge_items import build_judge_items_tool
from dotagent.tools.mcp import build_mcp_tools
from dotagent.tools.memory import build_memory_tools
from dotagent.tools.meta import build_meta_tools
from dotagent.tools.notes import build_notes_tools
from dotagent.tools.posts import build_post_tools
from dotagent.tools.registry import ToolRegistry, build_registry
from dotagent.tools.schedule import build_schedule_tools
from dotagent.tools.shell import build_shell_tool, child_env, read_login_env
from dotagent.tools.skill import build_skill_tool
from dotagent.tools.terminal import build_terminal_tools
from dotagent.tools.web import build_web_tools
from dotagent.web.client import WebClient

log = get_logger(__name__)


@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    store: Store
    bus: EventBus
    approvals: ApprovalBroker
    router: ModelRouter
    judge: Judge
    # TypeSafe's service, shared by the judge and judge_items; ``None`` when neither is on.
    typesafe: TypeSafeService | None
    # The browser sessions: ``browser`` is the main one (the configured mode), ``browsers`` gives the one of a mode.
    browser: BrowserSession
    browsers: BrowserPool
    desktop: DesktopSession
    terminals: Terminals
    web: WebClient
    # Whether the key of Ollama's hosted search was found when the runtime was built: the first rung of web_search's
    # ladder when [web] search_backend is auto.
    search_key_present: bool
    mcp: McpHub
    memory: MemoryIndex
    registry: ToolRegistry
    plugins: PluginCatalog
    protected: tuple[Path, ...]
    shell_env: dict[str, str]
    # The web push key pair the daemon signs with (item 52).
    push_keys: PushKeys
    workspace: Path
    schedule_zone: ZoneInfo
    undo_dir: Path
    notify: Notifier


def _tool_groups(config: AppConfig, judge_items: bool) -> tuple[tuple[RegisteredTool, ...], ...]:
    """The tools of this installation: the terminal tools only exist when a chat may open a terminal session, the
    code_fetch tool only when [policy] code_sources names an application, judge_items and the post tools of a campaign
    only when TypeSafe's key is saved (``judge_items``), the Mac desktop tools only when the desktop is enabled."""
    core: tuple[tuple[RegisteredTool, ...], ...] = (
        build_file_tools(),
        (build_shell_tool(),),
        build_browser_tools(),
        build_web_tools(),
        build_notes_tools(),
        build_memory_tools(),
        build_schedule_tools(),
        build_campaign_tools(),
        build_delegate_tools(),
        build_consult_tools(),
        build_meta_tools(),
        (build_skill_tool(),),
        *((build_terminal_tools(),) if config.shell.terminal_sessions > 0 else ()),
        *((build_code_tools(),) if config.policy.code_sources else ()),
        *(((build_judge_items_tool(),), build_post_tools()) if judge_items else ()),
    )
    if not config.desktop.enabled:
        return core
    return (*core, build_desktop_tools(), (build_apple_tool(),))


def _guarded_files() -> tuple[Path, ...]:
    """What the agent must neither read nor change: secrets, the browser profiles (Dot's own and the Chrome of the
    user), the settings that decide what it may do (policy, rules, MCP servers), its own database and the journals of
    file changes (``dotx undo`` writes back what a journal says)."""
    database: Path = db_path()
    return (
        env_file_path(),
        token_path(),
        vapid_path(),
        browser_profile_dir(),
        chrome_data_dir(),
        config_path(),
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        undo_dir(),
    )


def _call_recorder(store: Store, bus: EventBus) -> CallRecorder:
    """What becomes of a measured model call: its tokens join the totals of its run, which gets a ``model_call`` event;
    a call outside any run is logged."""

    async def record(call: ModelCall) -> None:
        if call.run_id is None:
            await log_model_call(call)
            return
        try:
            await store.add_run_tokens(call.run_id, call.input_tokens, call.output_tokens)
        except StoreError as error:
            # Bookkeeping: a run must not die because its token totals could not be written this once. The loss is
            # logged with the numbers, so the totals of the run are known to be short by this call.
            log.error(
                "model_call_not_recorded",
                run_id=call.run_id,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                error=error.message,
            )
        await bus.emit(call.run_id, CALL_EVENT, call_event_data(call), True)

    return record


async def build_runtime(config: AppConfig) -> Runtime:
    """Read the environment of the login shell, then open every service (:func:`open_runtime`)."""
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s)
    return await open_runtime(config, login_env)


async def open_runtime(config: AppConfig, login_env: dict[str, str]) -> Runtime:
    """Create the on-disk layout and open every service, with the environment of the login shell already read. Pair
    with :func:`close_runtime`.

    When a service fails to open, the ones opened before it are closed again before the error is raised.
    """
    await asyncio.to_thread(ensure_layout)
    protected: tuple[Path, ...] = await asyncio.to_thread(
        expand_protected, config.policy.protected_paths, _guarded_files()
    )
    schedule_zone: ZoneInfo = await asyncio.to_thread(resolve_zone, config.scheduler.timezone)
    shell_env: dict[str, str] = child_env(login_env, config.shell.pass_env)
    ollama_key: str | None = await asyncio.to_thread(resolve_secret, config.providers.ollama.api_key_env)
    typesafe_key: str | None = await asyncio.to_thread(resolve_secret, config.judge.api_key_env)
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    push_keys: PushKeys = await asyncio.to_thread(read_or_create_keys, vapid_path())
    for problem in plugins.problems:
        log.warning("plugin_skipped", path=str(problem.path), reason=problem.reason)
    async with AsyncExitStack() as cleanup:
        store: Store = await Store.open(db_path(), screenshots_dir())
        cleanup.push_async_callback(store.close)
        mcp: McpHub = await open_hub([*config.mcp, *plugins.servers()], login_env)
        cleanup.push_async_callback(mcp.close)
        memory: MemoryIndex = await open_memory_index(store, config)
        cleanup.push_async_callback(close_memory_index, memory)
        registry: ToolRegistry = build_registry((*_tool_groups(config, typesafe_key is not None), build_mcp_tools(mcp)))
        bus: EventBus = EventBus(store)
        typesafe: TypeSafeService | None = (
            TypeSafeService(config.judge, config.models.helper_cooldown_s, bus)
            if typesafe_key is not None or config.judge.provider == "typesafe"
            else None
        )
        browsers: BrowserPool = BrowserPool(config.browser, downloads_dir())
        runtime: Runtime = Runtime(
            config=config,
            store=store,
            bus=bus,
            approvals=ApprovalBroker(store, bus, config.policy.approval_timeout_s),
            router=ModelRouter(config, _call_recorder(store, bus)),
            judge=judge_for(config, typesafe),
            typesafe=typesafe,
            browser=browsers.main,
            browsers=browsers,
            desktop=DesktopSession(sized_for_agent(config), failsafe_path()),
            terminals=Terminals(config.shell.terminal_sessions, config.shell.terminal_idle_s, shell_env),
            web=WebClient(config.web, config.browser.allowed_hosts, config.browser.blocked_hosts),
            search_key_present=ollama_key is not None,
            mcp=mcp,
            memory=memory,
            registry=registry,
            plugins=plugins,
            protected=protected,
            push_keys=push_keys,
            shell_env=shell_env,
            workspace=workspace_dir(),
            schedule_zone=schedule_zone,
            undo_dir=undo_dir(),
            notify=show_notification,
        )
        cleanup.pop_all()
    return runtime


async def close_runtime(runtime: Runtime) -> None:
    await runtime.terminals.aclose()
    await runtime.desktop.close()
    await runtime.browsers.aclose()
    await runtime.mcp.close()
    await runtime.web.aclose()
    await runtime.router.aclose()
    await runtime.judge.aclose()
    if runtime.typesafe is not None:
        await runtime.typesafe.aclose()
    await close_memory_index(runtime.memory)
    await runtime.store.close()
