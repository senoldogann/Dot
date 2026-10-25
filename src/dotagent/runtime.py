"""The shared runtime: the configuration plus every long-lived service a run needs."""

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.gate import SiteLocks
from dotagent.agent.judge import SwitchedJudge, TypeSafeService, judge_for
from dotagent.agent.peers import Peers
from dotagent.agent.sizes import ContextSizes
from dotagent.assistant import Assistant, mac_first_name
from dotagent.awake import KeepAwake
from dotagent.browser.pool import BrowserPool
from dotagent.browser.session import RECONNECT_NOTICE, BrowserSession
from dotagent.config import AppConfig, McpServerConfig
from dotagent.core.errors import ElsewhereError, McpError, StoreError
from dotagent.core.logs import get_logger
from dotagent.core.notify import Notifier, show_notification
from dotagent.core.paths import (
    added_servers_path,
    browser_incognito_dir,
    browser_profile_dir,
    browser_profiles_dir,
    chrome_data_dir,
    config_path,
    connected_servers_path,
    db_path,
    dot_home,
    downloads_dir,
    ensure_layout,
    env_file_path,
    failsafe_path,
    mcp_sign_ins_dir,
    opencode_dir,
    playbooks_dir,
    plugins_data_dir,
    run_owners_dir,
    screenshots_dir,
    skills_dir,
    token_path,
    undo_dir,
    vapid_path,
    voice_dir,
    web_research_dir,
    workspace_dir,
)
from dotagent.desktop.session import DesktopSession
from dotagent.desktop.snapshots import SnapshotService
from dotagent.dot_app import app_path, build_path, runtime_path
from dotagent.events import EventBus
from dotagent.help.added import AddedServers, added_server_configs, read_added_servers
from dotagent.help.meaning import HelpVectors
from dotagent.launchd import launch_agent_path
from dotagent.llm.calls import CALL_EVENT, CallRecorder, ModelCall, call_event_data, log_model_call
from dotagent.llm.roles import ModelRoles
from dotagent.llm.router import ModelRouter
from dotagent.mcp.builtin import builtin_servers
from dotagent.mcp.connected import ConnectedServers, read_connected
from dotagent.mcp.hub import McpHub, open_hub
from dotagent.memory.service import MemoryIndex, close_memory_index, open_memory_index
from dotagent.notify.push import PushKeys, read_or_create_keys
from dotagent.oauth.accounts import Accounts
from dotagent.oauth.keychain import Keychain, keychain_file
from dotagent.plugins.catalog import PluginShelf, load_plugins, plugin_roots
from dotagent.plugins.types import PluginCatalog
from dotagent.policy.paths import expand_protected
from dotagent.policy.sandbox import Sandbox, SwitchedSandbox, build_sandbox, check_sandbox, hard_linked
from dotagent.scheduler.timing import resolve_zone
from dotagent.secrets import env_lock_path, optional_secret
from dotagent.store.backup import backup_folder, partial_folder
from dotagent.store.db import Store
from dotagent.terminal.session import Terminals
from dotagent.tools.apple import build_apple_tool
from dotagent.tools.base import RegisteredTool
from dotagent.tools.browser import build_browser_tools
from dotagent.tools.campaign import build_campaign_tools
from dotagent.tools.codes import build_code_tools
from dotagent.tools.consult import build_consult_tools
from dotagent.tools.delegate import build_delegate_tools
from dotagent.tools.desktop import build_desktop_tools
from dotagent.tools.drafts import build_draft_tool
from dotagent.tools.files import build_file_tools
from dotagent.tools.help import build_find_help_tool
from dotagent.tools.judge_items import build_judge_items_tool
from dotagent.tools.mail_calendar import build_mail_calendar_tools
from dotagent.tools.mcp import build_mcp_tools
from dotagent.tools.memory import build_memory_tools
from dotagent.tools.meta import build_meta_tools
from dotagent.tools.notes import build_notes_tools
from dotagent.tools.pages import build_page_tools
from dotagent.tools.posts import build_post_tools
from dotagent.tools.registry import ToolRegistry, build_registry
from dotagent.tools.schedule import build_schedule_tools
from dotagent.tools.shell import build_shell_tool, child_env, read_login_env
from dotagent.tools.skill import build_skill_tool
from dotagent.tools.team import build_team_tools
from dotagent.tools.terminal import build_terminal_tools
from dotagent.tools.web import build_web_tools
from dotagent.vault.onepassword import OnePassword
from dotagent.web.client import WebClient
from dotagent.web.keyed import keyed_engines
from dotagent.web.ladder import SearchEngine

log = get_logger(__name__)


class SearchKeys:
    """Whether the keys of the hosted rungs of web_search's ladder are set: Ollama's hosted search, the first rung when
    [web] search_backend is auto, and the Anthropic relay. Found when the runtime is built, and again when Settings ›
    Providers saves or removes one of them, which retires the provider's connector (:meth:`ModelRouter.on_retire`), so
    that a key works from the next search and a removed one is no longer climbed to (item 558)."""

    def __init__(
        self,
        ollama: bool,
        anthropic: bool,
        keyed: frozenset[SearchEngine] = frozenset(),
        order: tuple[SearchEngine, ...] = (),
    ) -> None:
        self.ollama: bool = ollama
        self.anthropic: bool = anthropic
        # The search APIs whose keys are set (item 655), read again when Settings › Web search saves or removes one.
        self.keyed: frozenset[SearchEngine] = keyed
        # The order of web_search's engines that Settings › Web search keeps, as ``[web] search_order`` (empty: the
        # ladder of ``search_backend``); a new order holds from the next search, with no restart.
        self.order: tuple[SearchEngine, ...] = order

    async def read_keyed(self) -> None:
        """Read again which of the search APIs' keys are set."""
        self.keyed = await keyed_engines()


async def _refresh_search_key(config: AppConfig, keys: SearchKeys, provider: str) -> None:
    """Read again whether the key of ``provider`` is set when its connector is retired (a key saved or removed in
    Settings › Providers), if it is one of web_search's hosted rungs (:class:`SearchKeys`)."""
    if provider == "ollama":
        keys.ollama = await asyncio.to_thread(optional_secret, config.providers.ollama.api_key_env) is not None
    elif provider == "anthropic":
        keys.anthropic = await asyncio.to_thread(optional_secret, config.providers.anthropic.api_key_env) is not None


@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    store: Store
    bus: EventBus
    approvals: ApprovalBroker
    # The gates' locks per site (pace and spending), shared by every run of this process.
    site_locks: SiteLocks
    router: ModelRouter
    # The models of Dot's own work, as the config file names them or as the owner chose them since (item 232).
    roles: ModelRoles
    # The context sizes chosen for models, as the config file names them or as the owner chose them since (item 250).
    context_sizes: ContextSizes
    judge: SwitchedJudge
    # TypeSafe's service, shared by the judge and judge_items; ``None`` when neither is on.
    typesafe: TypeSafeService | None
    # The browser sessions: ``browser`` is the main one (the configured mode), ``browsers`` gives the one of a mode.
    browser: BrowserSession
    browsers: BrowserPool
    desktop: DesktopSession
    # The daemon's hold on the Mac's sleep while it charges (item 271); off until the daemon starts it.
    awake: KeepAwake
    # The name the owner calls Dot by and the owner's own (item 420), as the web UI changes them.
    assistant: Assistant
    # The accounts of Google and Microsoft signed in through their official sign-ins (item 422).
    accounts: Accounts
    # The owner's 1Password, through its command line (item 493): the logins a run fills without seeing them.
    vault: OnePassword
    terminals: Terminals
    web: WebClient
    # Whether the key of Ollama's hosted search (the first rung of web_search's ladder when [web] search_backend is
    # auto) and ANTHROPIC_API_KEY (or the configured variable: the Anthropic rung joins the ladder with it) are set.
    search_keys: SearchKeys
    mcp: McpHub
    # The names of the MCP servers that came with Dot at its start, switched on or off (item 583); a server of the same
    # name that someone else set up is not among them.
    builtin_names: frozenset[str]
    memory: MemoryIndex
    # SnapShots (item 405): the watch of the Command key and the captures waiting for a composer.
    snapshots: SnapshotService
    # Help found by meaning with the memory's embeddings (item 169); ``None`` when they are off.
    help_vectors: HelpVectors | None
    registry: ToolRegistry
    plugins: PluginShelf
    protected: tuple[Path, ...]
    # What the agent may read but never change (:func:`_dot_paths`).
    dot_paths: tuple[Path, ...]
    # Where the processes of a run start outside yolo (:mod:`dotagent.policy.sandbox`), as [shell] sandbox said at start
    # or as the owner switched it since (item 262).
    sandbox: SwitchedSandbox
    shell_env: dict[str, str]
    # The web push key pair the daemon signs with (item 52).
    push_keys: PushKeys
    workspace: Path
    schedule_zone: ZoneInfo
    undo_dir: Path
    notify: Notifier
    # The runs at work in this process and what each holds (item 301), so that they leave each other's alone.
    peers: Peers

    @property
    def search_key_present(self) -> bool:
        """Whether Ollama's hosted search has its key now: web_search's ladder starts with that rung."""
        return self.search_keys.ollama

    @property
    def anthropic_key_present(self) -> bool:
        """Whether the key of the Anthropic search relay is set now: its rung joins the ladder."""
        return self.search_keys.anthropic


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
        (build_draft_tool(),),
        build_mail_calendar_tools(),
        build_schedule_tools(),
        build_campaign_tools(),
        build_delegate_tools(),
        build_team_tools(),
        build_consult_tools(),
        build_meta_tools(),
        build_page_tools(),
        (build_skill_tool(), build_find_help_tool()),
        *((build_terminal_tools(),) if config.shell.terminal_sessions > 0 else ()),
        *((build_code_tools(),) if config.policy.code_sources else ()),
        *(((build_judge_items_tool(),), build_post_tools()) if judge_items else ()),
    )
    if not config.desktop.enabled:
        return core
    return (*core, build_desktop_tools(), (build_apple_tool(),))


def _guarded_files(config: AppConfig) -> tuple[Path, ...]:
    """What the agent must neither read nor change: secrets and the lock of their file, what signing in to an MCP server
    gave Dot (its tokens), the browser profiles (Dot's own and the Chrome of the user), the settings that decide what it
    may do (policy, rules, MCP servers), its own database and its copies (item 526: a copy is the whole database, and
    one planted in their folder would be what the owner goes back to), the journals of file changes (``dotx undo``
    writes back what a journal says) and the locks that tell which process drives a run."""
    database: Path = db_path()
    return (
        env_file_path(),
        env_lock_path(env_file_path()),
        token_path(),
        vapid_path(),
        mcp_sign_ins_dir(),
        browser_profile_dir(),
        browser_incognito_dir(),
        browser_profiles_dir(),
        chrome_data_dir(),
        config_path(),
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        backup_folder(config.store),
        partial_folder(),
        undo_dir(),
        run_owners_dir(),
    )


def _own_code() -> tuple[Path, ...]:
    """The code Dot itself runs: the folder its package is imported from, the Python environment it runs in and the
    Python under that, the file of the launchd job that starts the daemon, and the program the job starts it with
    (Dot.app in Dot's home, and the folder it is built in; item 561), and the runtime folder the desktop app installs
    Dot in (item 575): the programs, the cache and the marker the app runs and trusts at its own start, outside the
    sandbox. A program a command put there would run outside the sandbox at the daemon's next start, which a command
    can bring about itself by ending the daemon: launchd keeps its job."""
    return (
        Path(__file__).resolve().parent.parent,
        Path(sys.prefix),
        Path(sys.base_prefix),
        launch_agent_path(Path.home()),
        app_path(dot_home()),
        build_path(dot_home()),
        runtime_path(dot_home()),
    )


def _dot_paths(config: AppConfig) -> tuple[Path, ...]:
    """What Dot loads and runs, which the agent may read (a skill's files are read and run by path) and change only with
    its own file tools once the user agrees (item 398):
    its own code, which the daemon runs at its next start, its plugins and their own files, whose MCP servers start
    with the daemon and are given keys from the key store, and so does each server of its record of those added from
    other agents (item 165) and of its record of those added on Customize › Connectors, the data of the web-research
    server that comes with Dot, which starts with the daemon and reads it back (item 583), the folder the daemon builds
    the program that writes recordings down in and runs it from (item 626), the home of the OpenCode
    server it starts (its config says what OpenCode's agent may do), its skills (the copies of those added from other
    agents too) and the playbooks it learned, which shape later runs, and the file of its emergency stop."""
    return (
        *_own_code(),
        *plugin_roots(config.plugins),
        plugins_data_dir(),
        web_research_dir(),
        voice_dir(),
        added_servers_path(),
        connected_servers_path(),
        opencode_dir(),
        skills_dir(),
        *(Path(entry).expanduser() for entry in config.skills.dirs),
        playbooks_dir(),
        failsafe_path(),
    )


async def _confined_paths(config: AppConfig) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """``(the locations a run may not read, what Dot loads and runs)``, resolved: ``[policy] protected_paths`` and
    ``sealed_paths`` with Dot's own files, and :func:`_dot_paths`."""
    protected: tuple[Path, ...] = await asyncio.to_thread(
        expand_protected, config.policy.refused_paths, _guarded_files(config)
    )
    dot_paths: tuple[Path, ...] = await asyncio.to_thread(expand_protected, [], _dot_paths(config))
    return protected, dot_paths


async def _run_sandbox(config: AppConfig, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> SwitchedSandbox:
    """The sandbox the processes of a run start in outside yolo: a command kept out of Dot's guarded files and of the
    ``[policy] sealed_paths`` (the data of the browsers, Mail and Messages: item 531) and from changing ``dot_paths``, a
    search out of every ``protected`` location too. It is built whatever [shell] sandbox says, so that Settings can
    switch it on later, and checked to start when it is on."""
    sealed: tuple[Path, ...] = await asyncio.to_thread(
        expand_protected, config.policy.sealed_paths, _guarded_files(config)
    )
    launch_agents: Path = Path(await asyncio.to_thread(os.path.realpath, Path.home() / "Library" / "LaunchAgents"))
    built: Sandbox = build_sandbox(sealed, protected, dot_paths, launch_agents)
    # The profile matches paths: a hard link to a sealed file made outside the sandbox is a path it does not name.
    for linked, names in (await asyncio.to_thread(hard_linked, sealed)).items():
        log.warning("sealed_file_has_other_names", path=str(linked), names=names)
    if config.shell.sandbox:
        await check_sandbox(built)
    return SwitchedSandbox(built, config.shell.sandbox)


@dataclass(frozen=True)
class _Confinement:
    """What keeps the processes of a run in, made once a start: the locations a run may not read, what Dot loads and
    runs, and the sandbox they start in."""

    protected: tuple[Path, ...]
    dot_paths: tuple[Path, ...]
    sandbox: SwitchedSandbox


async def _confinement(config: AppConfig) -> _Confinement:
    """The on-disk layout made, then :func:`_confined_paths` resolved and :func:`_run_sandbox` built (and checked to
    start when it is on)."""
    await asyncio.to_thread(ensure_layout)
    protected, dot_paths = await _confined_paths(config)
    sandbox: SwitchedSandbox = await _run_sandbox(config, protected, dot_paths)
    return _Confinement(protected=protected, dot_paths=dot_paths, sandbox=sandbox)


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
    """Read the environment of the login shell, then open every service (:func:`open_runtime`). The login shell runs
    its startup files, which a run's commands may change, so it starts in the sandbox of a run's commands: what a run
    put there reaches no further at Dot's start than the run could. The services take the same sandbox, so it is
    built and checked once."""
    confinement: _Confinement = await _confinement(config)
    login_env: dict[str, str] = await read_login_env(
        config.shell.login_env_timeout_s, confinement.sandbox.current().commands
    )
    return await _open_services(config, login_env, confinement)


def _added_servers(config: AppConfig, plugins: PluginCatalog) -> tuple[McpServerConfig, ...]:
    """The MCP servers that the owner added from other agents (item 165), read now from their agents' files. One that
    cannot be used is logged and left out, as a plugin's is, and so are all of them when Dot's record cannot be read."""
    path: Path = added_servers_path()
    try:
        record: AddedServers = read_added_servers(path)
    except ElsewhereError as error:
        log.warning("added_servers_unreadable", path=str(path), reason=error.message)
        return ()
    taken: frozenset[str] = frozenset([*(server.name for server in config.mcp), *(s.name for s in plugins.servers())])
    configs, problems = added_server_configs(Path.home(), record, taken)
    for problem in problems:
        log.warning("added_server_left_out", path=str(problem.path), reason=problem.reason)
    return configs


def _connected_servers(taken: frozenset[str]) -> tuple[McpServerConfig, ...]:
    """The MCP servers that the owner added on Customize › Connectors (items 279 and 316). One whose name another server
    has (``taken``) is logged and left out, and so are all of them when the record cannot be read."""
    path: Path = connected_servers_path()
    try:
        record: ConnectedServers = read_connected(path)
    except McpError as error:
        log.warning("connected_servers_unreadable", path=str(path), reason=error.message)
        return ()
    for server in record.servers:
        if server.name in taken:
            log.warning("connected_server_left_out", server=server.name, reason="another MCP server has its name")
    return tuple(server for server in record.servers if server.name not in taken)


async def open_runtime(config: AppConfig, login_env: dict[str, str]) -> Runtime:
    """Create the on-disk layout and open every service, with the environment of the login shell already read. Pair
    with :func:`close_runtime`.

    When a service fails to open, the ones opened before it are closed again before the error is raised.
    """
    return await _open_services(config, login_env, await _confinement(config))


async def _open_services(config: AppConfig, login_env: dict[str, str], confinement: _Confinement) -> Runtime:
    """Every service of :func:`open_runtime`, in the confinement already made."""
    protected, dot_paths, sandbox = confinement.protected, confinement.dot_paths, confinement.sandbox
    schedule_zone: ZoneInfo = await asyncio.to_thread(resolve_zone, config.scheduler.timezone)
    shell_env: dict[str, str] = child_env(login_env, config.shell.pass_env)
    # Keys of services the daemon starts without: a Keychain that does not answer does not stop it (item 558).
    ollama_key: str | None = await asyncio.to_thread(optional_secret, config.providers.ollama.api_key_env)
    anthropic_key: str | None = await asyncio.to_thread(optional_secret, config.providers.anthropic.api_key_env)
    typesafe_key: str | None = await asyncio.to_thread(optional_secret, config.judge.api_key_env)
    plugins: PluginCatalog = await asyncio.to_thread(load_plugins, config.plugins, config.mcp)
    push_keys: PushKeys = await asyncio.to_thread(read_or_create_keys, vapid_path())
    for problem in plugins.problems:
        log.warning("plugin_skipped", path=str(problem.path), reason=problem.reason)
    async with AsyncExitStack() as cleanup:
        store: Store = await Store.open(db_path(), screenshots_dir())
        cleanup.push_async_callback(store.close)
        added: tuple[McpServerConfig, ...] = await asyncio.to_thread(_added_servers, config, plugins)
        others: tuple[McpServerConfig, ...] = (*config.mcp, *plugins.servers(), *added)
        connected: tuple[McpServerConfig, ...] = await asyncio.to_thread(
            _connected_servers, frozenset(server.name for server in others)
        )
        # The servers that come with Dot start last, and a server of any other place with the name of one replaces it
        # (item 583).
        bundled: tuple[McpServerConfig, ...] = builtin_servers(
            config, frozenset(server.name for server in (*others, *connected))
        )
        mcp: McpHub = await open_hub([*others, *connected, *bundled], login_env)
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
        peers: Peers = Peers()
        browsers: BrowserPool = BrowserPool(config.browser, config.server, downloads_dir(), peers.threads)
        router: ModelRouter = ModelRouter(config, _call_recorder(store, bus), login_env)
        search_keys: SearchKeys = SearchKeys(
            ollama=ollama_key is not None,
            anthropic=anthropic_key is not None,
            keyed=await keyed_engines(),
            order=tuple(config.web.search_order),
        )
        router.on_retire(partial(_refresh_search_key, config, search_keys))
        runtime: Runtime = Runtime(
            config=config,
            store=store,
            bus=bus,
            approvals=ApprovalBroker(store, bus, config.policy.approval_timeout_s),
            site_locks=SiteLocks(),
            awake=KeepAwake(),
            assistant=Assistant(config.assistant, await asyncio.to_thread(mac_first_name)),
            accounts=Accounts(store, Keychain(keychain_file()), None),
            vault=OnePassword(None),
            router=router,
            roles=ModelRoles(config.models),
            context_sizes=router.context_sizes,
            judge=judge_for(config, typesafe),
            typesafe=typesafe,
            browser=browsers.main,
            browsers=browsers,
            desktop=DesktopSession(config.desktop, failsafe_path(), browsers.chrome_pids),
            terminals=Terminals(config.shell.terminal_sessions, config.shell.terminal_idle_s, shell_env),
            web=WebClient(config.web, config.browser.allowed_hosts, config.browser.blocked_hosts),
            search_keys=search_keys,
            mcp=mcp,
            builtin_names=frozenset(server.name for server in bundled),
            memory=memory,
            snapshots=SnapshotService(config.snapshots, bus),
            help_vectors=(
                None if memory.embedder is None else HelpVectors(memory.embedder, config.memory.min_similarity, store)
            ),
            registry=registry,
            plugins=PluginShelf(plugins),
            protected=protected,
            dot_paths=dot_paths,
            sandbox=sandbox,
            push_keys=push_keys,
            shell_env=shell_env,
            workspace=workspace_dir(),
            schedule_zone=schedule_zone,
            undo_dir=undo_dir(),
            notify=show_notification,
            peers=peers,
        )
        # Chrome asks for its Allow again when Dot connects again to the Chrome of the user it lost, maybe behind
        # another window while a step waits on it: the owner is told on the Mac (item 341).
        browsers.main.tell_reconnects(partial(show_notification, "Chrome asks to allow Dot", RECONNECT_NOTICE))
        cleanup.pop_all()
    return runtime


async def close_runtime(runtime: Runtime) -> None:
    """Close every part of the runtime: the terminals first, the store last. A part that fails to close does not keep
    the ones after it open; the error is raised once all were tried."""
    async with AsyncExitStack() as closing:
        # Callbacks run last pushed first.
        closing.push_async_callback(runtime.store.close)
        closing.push_async_callback(close_memory_index, runtime.memory)
        if runtime.help_vectors is not None:
            closing.push_async_callback(runtime.help_vectors.aclose)
        if runtime.typesafe is not None:
            closing.push_async_callback(runtime.typesafe.aclose)
        closing.push_async_callback(runtime.judge.aclose)
        closing.push_async_callback(runtime.router.aclose)
        closing.push_async_callback(runtime.web.aclose)
        closing.push_async_callback(runtime.mcp.close)
        closing.push_async_callback(runtime.browsers.aclose)
        closing.push_async_callback(runtime.desktop.close)
        closing.push_async_callback(runtime.awake.stop)
        closing.push_async_callback(runtime.terminals.aclose)
