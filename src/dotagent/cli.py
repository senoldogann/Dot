"""The ``dotx`` command line: run tasks, chat, inspect models and keys, and diagnose the setup."""

import argparse
import asyncio
import contextlib
import getpass
import secrets
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import Field, TypeAdapter, ValidationError

from dotagent.agent.manager import RunManager
from dotagent.browser.login import open_profile_for_login
from dotagent.cli_browser import (
    BrowserAttachCommand,
    BrowserDetachCommand,
    BrowserImportChromeCommand,
    cmd_browser_attach,
    cmd_browser_detach,
    cmd_browser_import_chrome,
)
from dotagent.cli_daemon import (
    DaemonCommand,
    OpenCommand,
    ServiceInstallCommand,
    ServicePermissionsCommand,
    ServiceStatusCommand,
    ServiceUninstallCommand,
    UrlCommand,
    cmd_daemon,
    cmd_open,
    cmd_service_install,
    cmd_service_permissions,
    cmd_service_status,
    cmd_service_uninstall,
    cmd_url,
)
from dotagent.cli_eval import (
    EvalCheckCommand,
    EvalListCommand,
    EvalReportCommand,
    EvalRunCommand,
    cmd_eval_check,
    cmd_eval_list,
    cmd_eval_report,
    cmd_eval_run,
)
from dotagent.cli_mcp import McpListCommand, cmd_mcp_list
from dotagent.cli_profile import ModelsProfileCommand, cmd_models_profile
from dotagent.cli_prune import PruneCommand, cmd_prune
from dotagent.cli_schedule import (
    ScheduleAddCommand,
    ScheduleDeleteCommand,
    ScheduleDisableCommand,
    ScheduleEnableCommand,
    ScheduleListCommand,
    cmd_schedule_add,
    cmd_schedule_delete,
    cmd_schedule_disable,
    cmd_schedule_enable,
    cmd_schedule_list,
)
from dotagent.cli_skills import SkillsListCommand, cmd_skills_list
from dotagent.cli_stats import StatsCommand, cmd_stats
from dotagent.cli_ui import TerminalUi, UiOptions, read_line
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, DotError
from dotagent.core.logs import configure_logging
from dotagent.core.paths import (
    browser_profile_dir,
    db_path,
    ensure_layout,
    env_file_path,
    failsafe_path,
    log_dir,
    screenshots_dir,
)
from dotagent.core.types import FrozenModel
from dotagent.desktop.perms import (
    Pane,
    check_permissions,
    missing_panes,
    open_settings,
    request_permissions,
    settings_url,
)
from dotagent.desktop.render import render_apps, render_snapshot
from dotagent.desktop.session import DesktopSession
from dotagent.desktop.types import AppView, Permissions, ScreenshotView, SnapshotScope, SnapshotView
from dotagent.desktop.views import ViewRequest
from dotagent.doctor import CheckResult, run_checks
from dotagent.events import Subscription
from dotagent.llm.router import KNOWN_PROVIDERS, ModelRef, ModelRouter
from dotagent.llm.types import Effort, ModelInfo
from dotagent.memory.render import render_note
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    SavedMemory,
    close_memory_index,
    forget_memory,
    open_memory_index,
    recall,
    reindex,
    save_memory,
)
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import resolve_secret, write_env_value
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, RunMode, RunRecord
from dotagent.tools.desktop import sized_for_agent

_NONCE_BYTES: int = 8
_EFFORT_HELP: str = "reasoning level; the levels a model offers are listed by 'dotx models'"
_APPROVALS_HELP: str = (
    "who answers what the policy would ask: ask (you), auto (a review model) or yolo (nobody; secrets, money and "
    "sign-ins still stop the run)"
)
_GOAL_MODE_HELP: str = (
    "a checker sends the run back to work until the goal is really reached (default: [agent] goal_mode)"
)
_CLI_THREAD: str = "cli"

_PROVIDER_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "opencode_go": "OPENCODE_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}


class RunCommand(FrozenModel):
    name: Literal["run"]
    goal: list[str]
    mode: Literal["task", "research"]
    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    goal_mode: bool | None
    thread: str | None
    no_input: bool
    thinking: bool
    verbose: bool


class ResumeCommand(FrozenModel):
    name: Literal["resume"]
    run_id: str
    no_input: bool
    thinking: bool
    verbose: bool


class ChatCommand(FrozenModel):
    name: Literal["chat"]
    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    thread: str | None
    thinking: bool
    verbose: bool


class ModelsCommand(FrozenModel):
    name: Literal["models"]
    provider: str | None


class AuthSetCommand(FrozenModel):
    name: Literal["auth-set"]
    key: str


class AuthStatusCommand(FrozenModel):
    name: Literal["auth-status"]


class DoctorCommand(FrozenModel):
    name: Literal["doctor"]


class BrowserLoginCommand(FrozenModel):
    name: Literal["browser-login"]
    url: str | None


class DesktopCheckCommand(FrozenModel):
    name: Literal["desktop-check"]


class DesktopSetupCommand(FrozenModel):
    name: Literal["desktop-setup"]


class DesktopAppsCommand(FrozenModel):
    name: Literal["desktop-apps"]


class DesktopSnapshotCommand(FrozenModel):
    name: Literal["desktop-snapshot"]
    app: str | None
    scope: SnapshotScope
    query: str | None


class DesktopScreenshotCommand(FrozenModel):
    name: Literal["desktop-screenshot"]
    path: str


class MemoryListCommand(FrozenModel):
    name: Literal["memory-list"]
    limit: int = Field(ge=1)


class MemoryAddCommand(FrozenModel):
    name: Literal["memory-add"]
    text: list[str]
    tags: list[str]


class MemorySearchCommand(FrozenModel):
    name: Literal["memory-search"]
    query: list[str]
    limit: int = Field(ge=1)


class MemoryForgetCommand(FrozenModel):
    name: Literal["memory-forget"]
    memory_id: str


class MemoryReindexCommand(FrozenModel):
    name: Literal["memory-reindex"]


type Command = Annotated[
    RunCommand
    | ResumeCommand
    | ChatCommand
    | ModelsCommand
    | ModelsProfileCommand
    | AuthSetCommand
    | AuthStatusCommand
    | DoctorCommand
    | BrowserLoginCommand
    | BrowserImportChromeCommand
    | BrowserAttachCommand
    | BrowserDetachCommand
    | DesktopCheckCommand
    | DesktopSetupCommand
    | DesktopAppsCommand
    | DesktopSnapshotCommand
    | DesktopScreenshotCommand
    | MemoryListCommand
    | MemoryAddCommand
    | MemorySearchCommand
    | MemoryForgetCommand
    | MemoryReindexCommand
    | ScheduleListCommand
    | ScheduleAddCommand
    | ScheduleDeleteCommand
    | ScheduleEnableCommand
    | ScheduleDisableCommand
    | McpListCommand
    | SkillsListCommand
    | PruneCommand
    | EvalListCommand
    | EvalCheckCommand
    | EvalRunCommand
    | EvalReportCommand
    | StatsCommand
    | DaemonCommand
    | OpenCommand
    | UrlCommand
    | ServiceInstallCommand
    | ServiceUninstallCommand
    | ServiceStatusCommand
    | ServicePermissionsCommand,
    Field(discriminator="name"),
]
_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dotx", description="Dot: an autonomous agent for your Mac.")
    commands = parser.add_subparsers(dest="name", required=True)

    run = commands.add_parser("run", help="carry out a goal and print the result")
    run.add_argument("goal", nargs="+", help="the goal in plain words; '-' reads it from standard input")
    run.add_argument("--mode", choices=["task", "research"], default="task", help="research is read-only")
    run.add_argument("--model", default=None, help="provider:model, e.g. ollama:gpt-oss:20b-cloud")
    run.add_argument("--effort", default=None, choices=list(get_args(Effort)), help=_EFFORT_HELP)
    run.add_argument("--approvals", default=None, choices=list(get_args(ApprovalMode)), help=_APPROVALS_HELP)
    run.add_argument("--yolo", dest="approvals", action="store_const", const="yolo", help="same as --approvals yolo")
    run.add_argument(
        "--auto-approve", dest="approvals", action="store_const", const="auto", help="same as --approvals auto"
    )
    run.add_argument("--goal-mode", action=argparse.BooleanOptionalAction, default=None, help=_GOAL_MODE_HELP)
    run.add_argument("--thread", default=None, help="continue an existing conversation thread")
    run.add_argument("--no-input", action="store_true", help="never prompt; approvals are denied")
    run.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    run.add_argument("--verbose", action="store_true", help="show tool output and token use")

    resume = commands.add_parser(
        "resume", help="go on with a run that a restart or a shutdown of the daemon cut off, and print the result"
    )
    resume.add_argument(
        "run_id",
        help="the id of the run that was cut off: the last line of 'dotx run' and the run page's address show it",
    )
    resume.add_argument("--no-input", action="store_true", help="never prompt; approvals are denied")
    resume.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    resume.add_argument("--verbose", action="store_true", help="show tool output and token use")

    chat = commands.add_parser("chat", help="talk with Dot interactively")
    chat.add_argument("--model", default=None, help="provider:model")
    chat.add_argument("--effort", default=None, choices=list(get_args(Effort)), help=_EFFORT_HELP)
    chat.add_argument("--approvals", default=None, choices=list(get_args(ApprovalMode)), help=_APPROVALS_HELP)
    chat.add_argument("--yolo", dest="approvals", action="store_const", const="yolo", help="same as --approvals yolo")
    chat.add_argument(
        "--auto-approve", dest="approvals", action="store_const", const="auto", help="same as --approvals auto"
    )
    chat.add_argument("--thread", default=None, help="continue an existing conversation thread")
    chat.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    chat.add_argument("--verbose", action="store_true", help="show tool output and token use")

    models = commands.add_parser("models", help="list the models the providers offer, or profile one of them")
    models_commands = models.add_subparsers(dest="models_action", required=False)
    for provider_name in KNOWN_PROVIDERS:
        models_commands.add_parser(provider_name, help=f"list the models {provider_name} offers")
    models_profile = models_commands.add_parser(
        "profile", help="how reliably a model worked in its newest runs, and the help its next run gets"
    )
    models_profile.add_argument("model", nargs="?", default=None, help="provider:model; [models] main when left out")

    auth = commands.add_parser("auth", help="manage API keys")
    auth_commands = auth.add_subparsers(dest="auth_action", required=True)
    auth_set = auth_commands.add_parser("set", help="store an API key (read from the terminal, never echoed)")
    auth_set.add_argument("key", help="variable name, e.g. OPENAI_API_KEY")
    auth_commands.add_parser("status", help="show which API keys are present")

    commands.add_parser("doctor", help="check the setup")

    browser = commands.add_parser("browser", help="manage the browser Dot uses")
    browser_commands = browser.add_subparsers(dest="browser_action", required=True)
    browser_login = browser_commands.add_parser(
        "login", help="open Dot's Chrome profile as a normal window so you can sign in to sites"
    )
    browser_login.add_argument("url", nargs="?", default=None, help="page to open first, e.g. accounts.google.com")
    browser_import = browser_commands.add_parser(
        "import-chrome",
        help="copy the sign-ins (cookies, local storage) of a personal Chrome profile into Dot's profile; quit Chrome first",
    )
    browser_import.add_argument(
        "--profile", default="Default", help="Chrome profile directory, e.g. Default or 'Profile 1'"
    )
    browser_import.add_argument(
        "--replace", action="store_true", help="replace the sign-ins that Dot's own profile already holds"
    )
    browser_commands.add_parser(
        "attach",
        help="work in new tabs of the Chrome you already have open, signed in as you are (Chrome 144 or later)",
    )
    browser_commands.add_parser("detach", help="go back to Dot's own Chrome and profile")

    desktop = commands.add_parser("desktop", help="check and try the desktop tools (screen, mouse, keyboard, apps)")
    desktop_commands = desktop.add_subparsers(dest="desktop_action", required=True)
    desktop_commands.add_parser("check", help="show which macOS privacy permissions Dot has")
    desktop_commands.add_parser("setup", help="ask macOS for the missing permissions and open their Settings panes")
    desktop_commands.add_parser("apps", help="list the open applications and their windows")
    desktop_snapshot = desktop_commands.add_parser(
        "snapshot", help="print an application's window as the agent sees it"
    )
    desktop_snapshot.add_argument("app", nargs="?", default=None, help="application name (default: the one in front)")
    desktop_snapshot.add_argument(
        "--scope", choices=["window", "all_windows", "menu_bar"], default="window", help="what part to show"
    )
    desktop_snapshot.add_argument("--query", default=None, help="only elements whose text contains this")
    desktop_screenshot = desktop_commands.add_parser("screenshot", help="save a screenshot of the main display")
    desktop_screenshot.add_argument("path", help="where to write the JPEG")

    memory = commands.add_parser("memory", help="see and edit the notes Dot remembers between runs")
    memory_commands = memory.add_subparsers(dest="memory_action", required=True)
    memory_list = memory_commands.add_parser("list", help="show the saved notes, the most recently changed first")
    memory_list.add_argument("--limit", type=int, default=50, help="how many notes to show")
    memory_add = memory_commands.add_parser("add", help="save a note: one short fact")
    memory_add.add_argument("text", nargs="+", help="the note")
    memory_add.add_argument(
        "--tag", dest="tags", action="append", default=[], help="a topic word; 'core' puts the note into every run"
    )
    memory_search = memory_commands.add_parser("search", help="find notes by meaning and by words")
    memory_search.add_argument("query", nargs="+", help="what to look for")
    memory_search.add_argument("--limit", type=int, default=8, help="how many notes to show at most")
    memory_forget = memory_commands.add_parser("forget", help="delete a note")
    memory_forget.add_argument("memory_id", help="the id shown in brackets by 'dotx memory list'")
    memory_commands.add_parser("reindex", help="embed every note again, e.g. after changing [memory] embed_model")

    schedule = commands.add_parser("schedule", help="goals that Dot runs later or on a rhythm, while the daemon runs")
    schedule_commands = schedule.add_subparsers(dest="schedule_action", required=True)
    schedule_commands.add_parser("list", help="show every schedule: when it runs, what is next, how the last run went")
    schedule_add = schedule_commands.add_parser("add", help="create a schedule")
    schedule_add.add_argument("--name", dest="title", required=True, help="a short label")
    schedule_timing = schedule_add.add_mutually_exclusive_group(required=True)
    schedule_timing.add_argument(
        "--cron", default=None, help="repeat on five fields in local time, e.g. '0 9 * * 1-5' for 09:00 on weekdays"
    )
    schedule_timing.add_argument(
        "--at", default=None, help="run once at this local date and time, e.g. 2026-10-06T09:30"
    )
    schedule_add.add_argument("--mode", choices=["task", "research"], default="task", help="research is read-only")
    schedule_add.add_argument("goal", nargs="+", help="the complete goal; each run starts without any conversation")
    schedule_delete = schedule_commands.add_parser("delete", help="delete a schedule")
    schedule_delete.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")
    schedule_enable = schedule_commands.add_parser("enable", help="switch a schedule on again")
    schedule_enable.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")
    schedule_disable = schedule_commands.add_parser("disable", help="switch a schedule off without deleting it")
    schedule_disable.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")

    mcp = commands.add_parser("mcp", help="the MCP tool servers that Dot connects to")
    mcp_commands = mcp.add_subparsers(dest="mcp_action", required=True)
    mcp_commands.add_parser(
        "list", help="start each configured server and show the tools it offers, or why it does not start"
    )

    skills = commands.add_parser("skills", help="the skills and plugins the agent can use")
    skills_commands = skills.add_subparsers(dest="skills_action", required=True)
    skills_commands.add_parser("list", help="show every skill and plugin found, and the folders left out and why")

    prune = commands.add_parser(
        "prune", help="find the screenshot files that no chat names any more and the free space in the database file"
    )
    prune.add_argument(
        "--apply",
        action="store_true",
        help="delete those screenshots and give the free space back to the disk (without it, only report)",
    )

    evaluation = commands.add_parser(
        "eval", help="measure how well Dot finishes tasks with a model: tasks with checks, and a score"
    )
    eval_commands = evaluation.add_subparsers(dest="eval_action", required=True)
    eval_commands.add_parser("list", help="list the eval tasks")
    eval_commands.add_parser(
        "check",
        help="prove, offline and without a model, that every task can be passed and cannot be passed by doing nothing",
    )
    eval_run = eval_commands.add_parser(
        "run", help="play the tasks against a model and print the score (this spends the tokens of that model)"
    )
    eval_run.add_argument(
        "--model", default=None, help="provider:model; the main model of the configuration if omitted"
    )
    eval_run.add_argument(
        "--task", dest="tasks", action="append", default=[], help="play only this task; give it again for more"
    )
    eval_run.add_argument(
        "--repeat", type=int, default=1, help="play every task this many times: one attempt says little about a model"
    )
    eval_report = eval_commands.add_parser(
        "report", help="show the score of saved eval runs and compare them (all saved runs if no file is given)"
    )
    eval_report.add_argument("files", nargs="*", help="report files written by 'dotx eval run'")

    stats = commands.add_parser(
        "stats", help="numbers about the runs that really happened: how they ended, tokens and tool errors per model"
    )
    stats.add_argument("--days", type=int, default=30, help="only the runs of the last N days")

    commands.add_parser("daemon", help="run Dot in this terminal: the API, the web UI, the agent and the schedules")
    commands.add_parser("open", help="open the web UI of the running daemon in your default browser, signed in")
    commands.add_parser("url", help="print the address of the web UI, with the sign-in token in it")

    service = commands.add_parser("service", help="keep the daemon running in the background (a launchd job)")
    service_commands = service.add_subparsers(dest="service_action", required=True)
    service_commands.add_parser("install", help="start the daemon at every login and after a crash; also restarts it")
    service_commands.add_parser("uninstall", help="stop the daemon and remove the launchd job")
    service_commands.add_parser("status", help="show whether the job is loaded and the daemon answers")
    service_commands.add_parser(
        "permissions", help="make macOS ask, in the name of the service, for the desktop permissions it lacks"
    )
    return parser


def parse_command(parser: argparse.ArgumentParser, arguments: list[str]) -> Command:
    namespace: argparse.Namespace = parser.parse_args(arguments)
    raw: dict[str, object] = dict(vars(namespace))
    action: object = raw.pop("auth_action", None)
    browser_action: object = raw.pop("browser_action", None)
    desktop_action: object = raw.pop("desktop_action", None)
    memory_action: object = raw.pop("memory_action", None)
    schedule_action: object = raw.pop("schedule_action", None)
    mcp_action: object = raw.pop("mcp_action", None)
    skills_action: object = raw.pop("skills_action", None)
    service_action: object = raw.pop("service_action", None)
    eval_action: object = raw.pop("eval_action", None)
    models_action: object = raw.pop("models_action", None)
    if raw["name"] == "models" and models_action == "profile":
        raw["name"] = "models-profile"
    elif raw["name"] == "models":
        raw["provider"] = models_action
    if raw["name"] == "auth":
        raw["name"] = f"auth-{action}"
    if raw["name"] == "browser":
        raw["name"] = f"browser-{browser_action}"
    if raw["name"] == "desktop":
        raw["name"] = f"desktop-{desktop_action}"
    if raw["name"] == "memory":
        raw["name"] = f"memory-{memory_action}"
    if raw["name"] == "schedule":
        raw["name"] = f"schedule-{schedule_action}"
    if raw["name"] == "mcp":
        raw["name"] = f"mcp-{mcp_action}"
    if raw["name"] == "skills":
        raw["name"] = f"skills-{skills_action}"
    if raw["name"] == "service":
        raw["name"] = f"service-{service_action}"
    if raw["name"] == "eval":
        raw["name"] = f"eval-{eval_action}"
    try:
        return _COMMAND_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        parser.error(str(exc))


async def _pump(subscription: Subscription, ui: TerminalUi) -> None:
    while True:
        await ui.handle(await subscription.queue.get())


async def _drain(subscription: Subscription, ui: TerminalUi) -> None:
    while not subscription.queue.empty():
        await ui.handle(subscription.queue.get_nowait())


async def execute_run(
    runtime: Runtime,
    manager: RunManager,
    ui: TerminalUi,
    goal: str,
    mode: RunMode,
    model: str | None,
    effort: Effort | None,
    approvals: ApprovalMode | None,
    goal_mode: bool | None,
    thread_id: str | None,
) -> RunRecord:
    """Start one run, show its events live and return the finished record. Ctrl-C stops the run."""
    return await follow_run(
        runtime,
        manager,
        ui,
        lambda: manager.start(goal, mode, model, thread_id, "cli", None, effort, approvals, goal_mode, ()),
    )


async def follow_run(
    runtime: Runtime, manager: RunManager, ui: TerminalUi, launch: Callable[[], Awaitable[RunRecord]]
) -> RunRecord:
    """Start a run with ``launch``, show its events live and return the finished record. Ctrl-C stops the run. The
    events are followed before the run starts, so none of them is missed."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    main_task: asyncio.Task[object] | None = asyncio.current_task()
    async with runtime.bus.subscribe(None) as subscription:
        pump: asyncio.Task[None] = asyncio.create_task(_pump(subscription, ui))
        run: RunRecord = await launch()
        presses: list[int] = []

        def on_interrupt() -> None:
            presses.append(1)
            if len(presses) == 1 and run.id in manager.active_run_ids():
                sys.stderr.write("\nstopping the run (press Ctrl-C again to quit immediately)\n")
                manager.cancel(run.id)
            elif main_task is not None:
                main_task.cancel()

        loop.add_signal_handler(signal.SIGINT, on_interrupt)
        try:
            finished: RunRecord = await manager.wait(run.id)
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        await _drain(subscription, ui)
    return finished


async def _read_goal(parts: list[str]) -> str:
    if parts == ["-"]:
        text: str = await asyncio.to_thread(sys.stdin.read)
        if not text.strip():
            raise ConfigError("the goal read from standard input is empty", {})
        return text.strip()
    return " ".join(parts)


def _print_result(run: RunRecord) -> None:
    sys.stdout.write(f"{run.result}\n")
    sys.stdout.flush()
    sys.stderr.write(
        f"[{run.status}] {run.steps} steps, {run.input_tokens} tokens in / {run.output_tokens} out, "
        f"run {run.id}, thread {run.thread_id}\n"
    )


async def cmd_run(command: RunCommand) -> int:
    goal: str = await _read_goal(command.goal)
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        options: UiOptions = UiOptions(
            interactive=sys.stdin.isatty() and not command.no_input,
            stream_text=False,
            show_thinking=command.thinking,
            verbose=command.verbose,
        )
        run: RunRecord = await execute_run(
            runtime,
            RunManager(runtime),
            TerminalUi(runtime, options),
            goal,
            command.mode,
            command.model,
            command.effort,
            command.approvals,
            command.goal_mode,
            command.thread,
        )
    finally:
        await close_runtime(runtime)
    _print_result(run)
    return 0 if run.status == "done" else 1


async def cmd_resume(command: ResumeCommand) -> int:
    """Resume a run that the daemon cut off in this process, like ``dotx run``: see ``RunManager.resume``."""
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        options: UiOptions = UiOptions(
            interactive=sys.stdin.isatty() and not command.no_input,
            stream_text=False,
            show_thinking=command.thinking,
            verbose=command.verbose,
        )
        manager: RunManager = RunManager(runtime)
        run: RunRecord = await follow_run(
            runtime, manager, TerminalUi(runtime, options), lambda: manager.resume(command.run_id)
        )
    finally:
        await close_runtime(runtime)
    _print_result(run)
    return 0 if run.status == "done" else 1


async def cmd_chat(command: ChatCommand) -> int:
    if not sys.stdin.isatty():
        raise ConfigError("chat needs an interactive terminal; use 'dotx run' for scripts", {})
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        manager: RunManager = RunManager(runtime)
        ui: TerminalUi = TerminalUi(
            runtime,
            UiOptions(interactive=True, stream_text=True, show_thinking=command.thinking, verbose=command.verbose),
        )
        thread_id: str | None = command.thread
        sys.stderr.write("Dot chat. /new starts a fresh conversation, /exit leaves.\n")
        while True:
            line: str | None = await read_line("\nyou> ")
            if line is None or line.strip() in ("/exit", "/quit"):
                return 0
            text: str = line.strip()
            if text == "/new":
                thread_id = None
                continue
            if not text:
                continue
            run: RunRecord = await execute_run(
                runtime, manager, ui, text, "chat", command.model, command.effort, command.approvals, None, thread_id
            )
            thread_id = run.thread_id
            if not ui.streamed(run.result):
                sys.stderr.write(f"{run.result}\n")
            if run.status != "done":
                sys.stderr.write(f"[{run.status}]\n")
    finally:
        await close_runtime(runtime)


async def cmd_models(command: ModelsCommand) -> int:
    config: AppConfig = load_user_config()
    router: ModelRouter = ModelRouter(config)
    failures: int = 0
    try:
        for provider_name in [command.provider] if command.provider is not None else list(KNOWN_PROVIDERS):
            try:
                models: tuple[ModelInfo, ...] = await (await router.provider(provider_name)).list_models()
            except DotError as exc:
                failures += 1
                sys.stdout.write(f"{provider_name}: unavailable - {exc.message}\n")
                continue
            sys.stdout.write(f"{provider_name}:\n")
            for info in models:
                ref: ModelRef = ModelRef(provider=provider_name, model=info.id)
                traits: str = ", ".join(
                    name
                    for name, present in (("tools", info.tools), ("vision", info.vision), ("thinking", info.thinking))
                    if present
                )
                levels: str = f"  reasoning: {' '.join(info.efforts)}" if len(info.efforts) > 1 else ""
                sys.stdout.write(f"  {ref.label}  [{traits}]{levels}\n")
    finally:
        await router.aclose()
    return 1 if failures and command.provider is not None else 0


async def cmd_auth_set(command: AuthSetCommand) -> int:
    if command.key not in _PROVIDER_KEYS.values():
        raise ConfigError("unknown key name", {"key": command.key, "known": list(sorted(_PROVIDER_KEYS.values()))})
    if sys.stdin.isatty():
        value: str = await asyncio.to_thread(getpass.getpass, f"{command.key}: ")
    else:
        value = (await asyncio.to_thread(sys.stdin.read)).strip()
    if not value.strip():
        raise ConfigError("no value was provided", {"key": command.key})
    await asyncio.to_thread(write_env_value, env_file_path(), command.key, value.strip())
    sys.stdout.write(f"{command.key} stored in {env_file_path()} (mode 0600)\n")
    return 0


async def cmd_auth_status(command: AuthStatusCommand) -> int:
    for provider_name, variable in _PROVIDER_KEYS.items():
        state: str = "set" if await asyncio.to_thread(resolve_secret, variable) is not None else "missing"
        sys.stdout.write(f"{provider_name:<12} {variable:<18} {state}\n")
    return 0


async def cmd_doctor(command: DoctorCommand) -> int:
    results: list[CheckResult] = await run_checks(load_user_config())
    for result in results:
        mark: str = "ok  " if result.ok else ("FAIL" if result.required else "warn")
        sys.stdout.write(f"{mark}  {result.name:<22} {result.detail}\n")
    return 1 if any(not result.ok and result.required for result in results) else 0


async def cmd_browser_login(command: BrowserLoginCommand) -> int:
    sys.stdout.write(
        "Chrome opens with Dot's own profile. Sign in to the sites Dot should be able to use, then quit Chrome "
        "(Cmd+Q). Dot keeps the cookies; it never sees your passwords.\n"
    )
    code: int = await open_profile_for_login(browser_profile_dir(), command.url)
    sys.stdout.write("Chrome closed; the sign-ins are saved in Dot's profile.\n")
    return code


@contextlib.asynccontextmanager
async def _desktop_session() -> AsyncIterator[DesktopSession]:
    """A desktop session sized the way the agent's is, closed again afterwards."""
    session: DesktopSession = DesktopSession(sized_for_agent(load_user_config()), failsafe_path())
    try:
        yield session
    finally:
        await session.close()


async def cmd_desktop_check(command: DesktopCheckCommand) -> int:
    permissions: Permissions = check_permissions()
    rows: tuple[tuple[str, bool], ...] = (
        ("accessibility", permissions.accessibility),
        ("input events", permissions.post_event),
        ("screen recording", permissions.screen_recording),
    )
    for name, granted in rows:
        sys.stdout.write(f"{'ok  ' if granted else 'FAIL'}  {name:<18} {'granted' if granted else 'not granted'}\n")
    if missing_panes(permissions):
        sys.stdout.write("Run `dotx desktop setup` to be asked for what is missing.\n")
        return 1
    return 0


async def cmd_desktop_setup(command: DesktopSetupCommand) -> int:
    missing: tuple[Pane, ...] = missing_panes(check_permissions())
    if not missing:
        sys.stdout.write("Accessibility, input events and screen recording are all granted.\n")
        return 0
    await asyncio.to_thread(request_permissions)
    for pane in missing:
        await asyncio.to_thread(open_settings, pane)
        sys.stdout.write(f"opened {settings_url(pane)}\n")
    sys.stdout.write(
        "Switch on the app that runs Dot (Terminal, iTerm, Claude or the Dot service) in each pane, "
        "then start Dot again.\n"
    )
    return 0


async def cmd_desktop_apps(command: DesktopAppsCommand) -> int:
    async with _desktop_session() as session:
        views: tuple[AppView, ...] = await session.list_apps()
    sys.stdout.write(render_apps(views, secrets.token_hex(_NONCE_BYTES)) + "\n")
    return 0


async def cmd_desktop_snapshot(command: DesktopSnapshotCommand) -> int:
    request: ViewRequest = ViewRequest(scope=command.scope, ref=None, query=command.query, full=True)
    async with _desktop_session() as session:
        view: SnapshotView = await session.snapshot(_CLI_THREAD, command.app, request)
    sys.stdout.write(render_snapshot(view, secrets.token_hex(_NONCE_BYTES)) + "\n")
    return 0


def _write_screenshot(path_text: str, image: bytes) -> Path:
    target: Path = Path(path_text).expanduser()
    target.write_bytes(image)
    return target


async def cmd_desktop_screenshot(command: DesktopScreenshotCommand) -> int:
    async with _desktop_session() as session:
        shot: ScreenshotView = await session.screenshot()
    target: Path = await asyncio.to_thread(_write_screenshot, command.path, shot.image)
    sys.stdout.write(f"{target} ({shot.width}x{shot.height} px, {len(shot.image)} bytes)\n")
    return 0


@contextlib.asynccontextmanager
async def _memory_index(config: AppConfig) -> AsyncIterator[MemoryIndex]:
    """The note memory alone (store and embedder), opened without the rest of the runtime and closed afterwards."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        index: MemoryIndex = await open_memory_index(store, config)
        try:
            yield index
        finally:
            await close_memory_index(index)
    finally:
        await store.close()


async def cmd_memory_list(command: MemoryListCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        records: list[MemoryRecord] = await index.store.list_memories(command.limit, 0)
        total: int = await index.store.count_memories()
    for record in records:
        sys.stdout.write(f"{render_note(record)}\n")
    sys.stderr.write(f"{len(records)} of {total} notes\n")
    return 0


async def cmd_memory_add(command: MemoryAddCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        saved: SavedMemory = await save_memory(index, " ".join(command.text), command.tags, None)
    sys.stdout.write(f"{'Saved' if saved.created else 'Already saved'}: {render_note(saved.record)}\n")
    if saved.similar:
        sys.stdout.write("These notes say nearly the same; remove an outdated one with 'dotx memory forget <id>':\n")
        for hit in saved.similar:
            sys.stdout.write(f"  {render_note(hit.record)}\n")
    return 0


async def cmd_memory_search(command: MemorySearchCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        hits: list[MemoryHit] = await recall(index, " ".join(command.query), command.limit)
    for hit in hits:
        score: str = "    -" if hit.similarity is None else f"{hit.similarity:5.2f}"
        sys.stdout.write(f"{score}  {render_note(hit.record)}\n")
    if not hits:
        sys.stderr.write("no saved notes match\n")
    return 0


async def cmd_memory_forget(command: MemoryForgetCommand) -> int:
    async with _memory_index(load_user_config()) as index:
        forgotten: MemoryRecord = await forget_memory(index, command.memory_id)
    sys.stdout.write(f"Forgot: {render_note(forgotten)}\n")
    return 0


async def cmd_memory_reindex(command: MemoryReindexCommand) -> int:
    config: AppConfig = load_user_config()
    if not config.memory.embed_enabled:
        raise ConfigError("embeddings are switched off ([memory] embed_enabled = false); nothing to embed", {})
    async with _memory_index(config) as index:
        count: int = await reindex(index)
    sys.stdout.write(f"{count} notes embedded with {config.memory.embed_provider}:{config.memory.embed_model}\n")
    return 0


async def dispatch(command: Command) -> int:
    match command:
        case RunCommand():
            return await cmd_run(command)
        case ResumeCommand():
            return await cmd_resume(command)
        case ChatCommand():
            return await cmd_chat(command)
        case ModelsCommand():
            return await cmd_models(command)
        case ModelsProfileCommand():
            return await cmd_models_profile(command)
        case AuthSetCommand():
            return await cmd_auth_set(command)
        case AuthStatusCommand():
            return await cmd_auth_status(command)
        case DoctorCommand():
            return await cmd_doctor(command)
        case BrowserLoginCommand():
            return await cmd_browser_login(command)
        case BrowserImportChromeCommand():
            return await cmd_browser_import_chrome(command)
        case BrowserAttachCommand():
            return await cmd_browser_attach(command)
        case BrowserDetachCommand():
            return await cmd_browser_detach(command)
        case DesktopCheckCommand():
            return await cmd_desktop_check(command)
        case DesktopSetupCommand():
            return await cmd_desktop_setup(command)
        case DesktopAppsCommand():
            return await cmd_desktop_apps(command)
        case DesktopSnapshotCommand():
            return await cmd_desktop_snapshot(command)
        case DesktopScreenshotCommand():
            return await cmd_desktop_screenshot(command)
        case MemoryListCommand():
            return await cmd_memory_list(command)
        case MemoryAddCommand():
            return await cmd_memory_add(command)
        case MemorySearchCommand():
            return await cmd_memory_search(command)
        case MemoryForgetCommand():
            return await cmd_memory_forget(command)
        case MemoryReindexCommand():
            return await cmd_memory_reindex(command)
        case ScheduleListCommand():
            return await cmd_schedule_list(command)
        case ScheduleAddCommand():
            return await cmd_schedule_add(command)
        case ScheduleDeleteCommand():
            return await cmd_schedule_delete(command)
        case ScheduleEnableCommand():
            return await cmd_schedule_enable(command)
        case ScheduleDisableCommand():
            return await cmd_schedule_disable(command)
        case McpListCommand():
            return await cmd_mcp_list(command)
        case SkillsListCommand():
            return await cmd_skills_list(command)
        case PruneCommand():
            return await cmd_prune(command)
        case EvalListCommand():
            return await cmd_eval_list(command)
        case EvalCheckCommand():
            return await cmd_eval_check(command)
        case EvalRunCommand():
            return await cmd_eval_run(command)
        case EvalReportCommand():
            return await cmd_eval_report(command)
        case StatsCommand():
            return await cmd_stats(command)
        case DaemonCommand():
            return await cmd_daemon(command)
        case OpenCommand():
            return await cmd_open(command)
        case UrlCommand():
            return await cmd_url(command)
        case ServiceInstallCommand():
            return await cmd_service_install(command)
        case ServiceUninstallCommand():
            return await cmd_service_uninstall(command)
        case ServiceStatusCommand():
            return await cmd_service_status(command)
        case ServicePermissionsCommand():
            return await cmd_service_permissions(command)


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    command: Command = parse_command(parser, sys.argv[1:])
    ensure_layout()
    configure_logging("info", False, log_dir() / "dotx.log")
    try:
        code: int = asyncio.run(dispatch(command))
    except DotError as exc:
        sys.stderr.write(f"error: {exc}\n")
        code = 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
