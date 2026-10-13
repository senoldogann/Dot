"""``dotx run``, ``dotx resume`` and ``dotx chat``: a run followed in the terminal, with its events, its questions and
its answer."""

import asyncio
import contextlib
import signal
import sys
from collections.abc import Awaitable, Callable

from dotagent.agent.lessons import RunLessons, run_lessons
from dotagent.agent.manager import RunManager
from dotagent.cli_campaign import (
    render_first_session_end,
)
from dotagent.cli_commands import (
    ChatCommand,
    Command,
    ResumeCommand,
    RunCommand,
)
from dotagent.cli_ui import TerminalUi, UiOptions, read_line
from dotagent.config import load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError
from dotagent.events import Subscription
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunMode, RunRecord

# How long a second Ctrl-C waits for the stop that the first one asked for to be recorded before the command quits.
_QUIT_WAIT_S: float = 5.0


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
    """Start a run with ``launch``, show its events live and return the finished record. Ctrl-C stops the run; a closed
    terminal (SIGHUP) and SIGTERM cut it off (:meth:`RunManager.cut_off`), so that it can be resumed. Either way the
    run's end is recorded and the daemon does not take it for a run that still works; a second Ctrl-C quits once the
    stop is recorded, or after ``_QUIT_WAIT_S`` (the daemon then closes the run as its command's death). The events are
    followed before the run starts, so none of them is missed."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    main_task: asyncio.Task[object] | None = asyncio.current_task()
    async with runtime.bus.subscribe(None) as subscription:
        pump: asyncio.Task[None] = asyncio.create_task(_pump(subscription, ui))
        run: RunRecord = await launch()
        presses: list[int] = []

        def on_interrupt() -> None:
            presses.append(1)
            if len(presses) == 1 and run.id in manager.active_run_ids():
                sys.stderr.write("\nstopping the run (press Ctrl-C again to quit as soon as the stop is recorded)\n")
                manager.cancel(run.id)
            elif main_task is not None:
                main_task.cancel()

        def on_end() -> None:
            # Nothing is written: after SIGHUP the terminal is gone.
            manager.cut_off(run.id)

        loop.add_signal_handler(signal.SIGINT, on_interrupt)
        loop.add_signal_handler(signal.SIGHUP, on_end)
        loop.add_signal_handler(signal.SIGTERM, on_end)
        try:
            finished: RunRecord = await manager.wait(run.id)
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGHUP)
            loop.remove_signal_handler(signal.SIGTERM)
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            if run.id in manager.active_run_ids():
                # A second Ctrl-C: the store has to hold the stop the first one asked for before the command closes it.
                await _until_recorded(manager, run.id)
        await _drain(subscription, ui)
    return finished


async def _until_recorded(manager: RunManager, run_id: str) -> None:
    """Wait at most ``_QUIT_WAIT_S`` for the end of a stopped run to be recorded, and say so when it is not."""
    try:
        async with asyncio.timeout(_QUIT_WAIT_S):
            await manager.wait(run_id)
    except TimeoutError:
        sys.stderr.write(f"run {run_id} has not recorded its stop yet; the Dot daemon closes it\n")


async def _read_goal(parts: list[str]) -> str:
    if parts == ["-"]:
        text: str = await asyncio.to_thread(sys.stdin.read)
        if not text.strip():
            raise ConfigError("the goal read from standard input is empty", {})
        return text.strip()
    return " ".join(parts)


def _say_left_out_servers(runtime: Runtime) -> None:
    """Name each MCP server that could not start, with the reason: the run goes on without its tools."""
    for failure in runtime.mcp.failures:
        sys.stderr.write(f"MCP server {failure.server} is left out: {failure.error} (dotx mcp list shows more)\n")


def _print_result(run: RunRecord) -> None:
    sys.stdout.write(f"{run.result}\n")
    sys.stdout.flush()
    sys.stderr.write(
        f"[{run.status}] {run.steps} steps, {run.input_tokens} tokens in / {run.output_tokens} out, "
        f"run {run.id}, thread {run.thread_id}\n"
    )


async def _show_lessons(runtime: Runtime, manager: RunManager, ui: TerminalUi, run_id: str) -> None:
    """Wait until the lessons of a run that ended are drawn, then show what became of them. Ctrl-C drops the ones not
    drawn or saved yet, and the command still ends with the run's status."""
    if manager.learning(run_id):
        sys.stderr.write("drawing the lessons of this run (Ctrl-C skips it)\n")
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        # Cancelling the wait cancels the drawing (see RunManager.learned).
        waiting: asyncio.Task[None] = asyncio.create_task(manager.learned(run_id))
        loop.add_signal_handler(signal.SIGINT, waiting.cancel)
        try:
            await asyncio.wait({waiting})
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            waiting.cancel()
    lessons: RunLessons | None = await run_lessons(runtime.store, run_id)
    if lessons is not None:
        ui.show_lessons(lessons)


async def cmd_run(command: RunCommand) -> int:
    goal: str = await _read_goal(command.goal)
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        _say_left_out_servers(runtime)
        options: UiOptions = UiOptions(
            interactive=sys.stdin.isatty() and not command.no_input,
            stream_text=False,
            show_thinking=command.thinking,
            verbose=command.verbose,
        )
        manager: RunManager = RunManager(runtime)
        ui: TerminalUi = TerminalUi(runtime, options)
        run: RunRecord = (
            await follow_run(
                runtime,
                manager,
                ui,
                lambda: manager.start_campaign(
                    goal,
                    command.mode,
                    command.model,
                    command.thread,
                    "cli",
                    command.effort,
                    command.approvals,
                    command.goal_mode,
                    (),
                    command.browser,
                ),
            )
            if command.standing
            else await execute_run(
                runtime,
                manager,
                ui,
                goal,
                command.mode,
                command.model,
                command.effort,
                command.approvals,
                command.goal_mode,
                command.thread,
            )
        )
        # The answer comes first: the lessons are drawn after the run's end.
        _print_result(run)
        ui.show_open_ends(ui.needs_from_user, await runtime.store.list_left_questions(run.id))
        if run.campaign_id is not None:
            sys.stderr.write(f"{render_first_session_end(await runtime.store.get_campaign(run.campaign_id))}\n")
        await _show_lessons(runtime, manager, ui, run.id)
    finally:
        await close_runtime(runtime)
    return 0 if run.status == "done" else 1


async def cmd_resume(command: ResumeCommand) -> int:
    """Resume a run that was cut off in this process, like ``dotx run``: see ``RunManager.resume``."""
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        _say_left_out_servers(runtime)
        options: UiOptions = UiOptions(
            interactive=sys.stdin.isatty() and not command.no_input,
            stream_text=False,
            show_thinking=command.thinking,
            verbose=command.verbose,
        )
        manager: RunManager = RunManager(runtime)
        ui: TerminalUi = TerminalUi(runtime, options)
        run: RunRecord = await follow_run(runtime, manager, ui, lambda: manager.resume(command.run_id))
        _print_result(run)
        ui.show_open_ends(ui.needs_from_user, await runtime.store.list_left_questions(run.id))
        await _show_lessons(runtime, manager, ui, run.id)
    finally:
        await close_runtime(runtime)
    return 0 if run.status == "done" else 1


async def cmd_chat(command: ChatCommand) -> int:
    if not sys.stdin.isatty():
        raise ConfigError("chat needs an interactive terminal; use 'dotx run' for scripts", {})
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        _say_left_out_servers(runtime)
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


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case RunCommand():
            return await cmd_run(command)
        case ResumeCommand():
            return await cmd_resume(command)
        case ChatCommand():
            return await cmd_chat(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
