"""Programs on a terminal of their own, through the terminal tools and the gate as the executor runs them: a REPL
answers what is typed, a password prompt takes no secret in any mode, ``yolo`` too, and the secret shows nowhere, a
server stays up until its session is closed, and a chat keeps at most its limit of sessions, each closed once it is
idle. No model is called.
"""

import asyncio
import contextlib
import dataclasses
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.terminal.session import Terminals, TerminalSession, ended_notices
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Work in a terminal."
SECRET: str = "correct-horse-7"
# A server for a terminal to keep running: Python's http.server, bound without the reverse lookup of its address that
# `python3 -m http.server` makes before it says where it serves, which kept CI's runner silent for over 30 s.
_SERVER: str = """\
import http.server
import socketserver


class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


with Server(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler) as served:
    print(f"Serving HTTP on 127.0.0.1 port {served.server_address[1]}", flush=True)
    served.serve_forever()
"""
# What the server says once it serves, with its port.
_SERVING: str = r"Serving HTTP on 127\.0\.0\.1 port (\d+)"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _yolo_context(runtime: Runtime) -> ToolContext:
    thread: ThreadRecord = await runtime.store.create_thread("terminal", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "yolo",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _call(ctx: ToolContext, tool: str, args: JsonObject) -> tuple[PreparedCall, str]:
    """``tool`` through the gate, as the executor runs it: what it was classified as and what it returned."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[tool].prepare(args, ctx)
    await ctx.gate.authorize(prepared.action)
    return prepared, (await prepared.run(ctx)).text


def _session(opened: str) -> str:
    match: re.Match[str] | None = re.match(r"\[session (t\d+): ", opened)
    assert match is not None, opened
    return match.group(1)


async def test_a_repl_answers_what_is_typed_and_its_session_closes_when_it_ends(runtime: Runtime) -> None:
    ctx: ToolContext = await _yolo_context(runtime)
    _, opened = await _call(ctx, "terminal_open", {"command": "python3 -i -q"})
    session: str = _session(opened)
    _, answer = await _call(ctx, "terminal_send", {"session": session, "text": "6 * 7\n"})
    _, ended = await _call(ctx, "terminal_send", {"session": session, "text": "exit()\n"})
    assert opened.rstrip().endswith(">>>"), opened
    assert "42" in answer.splitlines(), answer
    assert ended.endswith(f"[the program ended with exit 0; session {session} is closed]"), ended
    with pytest.raises(ToolInputError, match=f"no terminal session {session}"):
        runtime.terminals.get(ctx.thread_id, session)


async def test_a_password_prompt_is_the_run_s_in_yolo_and_refused_where_the_modes_ask(runtime: Runtime) -> None:
    """Outside yolo credentials are the user's (item 429) and nobody else can type into the session; a yolo run asks
    nothing (item 443) and types the password itself."""
    ctx: ToolContext = await _yolo_context(runtime)
    _, opened = await _call(
        ctx, "terminal_open", {"command": 'read -s "pw?Password: "; echo; echo "got ${#pw} characters"'}
    )
    session: str = _session(opened)
    typing: JsonObject = {"session": session, "text": f"{SECRET}\n"}
    assert "Password:" in opened, opened
    with pytest.raises(
        ToolInputError, match="asks for a password, and this run does not type one into a terminal"
    ) as refused:
        await runtime.registry.tools["terminal_send"].prepare(typing, dataclasses.replace(ctx, approvals="ask"))
    assert SECRET not in str(refused.value)
    # Nothing of the password is in what the step is called by, which the events and any record of a waived step carry.
    prepared: PreparedCall = await runtime.registry.tools["terminal_send"].prepare(typing, ctx)
    assert prepared.action.summary == f"Type into {session}: a password", prepared.action.summary
    assert SECRET not in prepared.action.summary
    # One line is the password; a second line would be a command the step never assessed, and a yolo run refuses it.
    for second in ("\nrm -rf /tmp/nothing-here\n", "\rrm -rf /tmp/nothing-here\r"):
        with pytest.raises(ToolInputError, match="a line is all this step types"):
            await runtime.registry.tools["terminal_send"].prepare(
                {"session": session, "text": f"{SECRET}{second}"}, ctx
            )
    # In yolo the same line goes in, and the program answers with the length of what it read.
    _, typed = await _call(ctx, "terminal_send", typing)
    assert f"got {len(SECRET)} characters" in typed, typed
    assert SECRET not in typed
    # The step is waived with the reason it would have asked for, as the browser's secret fields are (item 443).
    waived: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, "approval_auto", 10)
    assert [(event.data["decided_by"], event.data["note"]) for event in waived] == [
        ("yolo", f"credential actions are handed to the user: Type into {session}: a password")
    ]
    # The program read the line and ended with it: the session closed itself, as an ended program's does; the close is
    # for the one time it had not.
    with contextlib.suppress(ToolInputError):
        await _call(ctx, "terminal_close", {"session": session})


async def test_a_server_stays_up_between_calls_and_stops_with_its_session(runtime: Runtime, tmp_path: Path) -> None:
    ctx: ToolContext = await _yolo_context(runtime)
    server: Path = tmp_path / "serve.py"
    await asyncio.to_thread(server.write_text, _SERVER, encoding="utf-8")
    _, opened = await _call(ctx, "terminal_open", {"command": f"python3 {server}"})
    session: str = _session(opened)
    port: re.Match[str] | None = re.search(_SERVING, opened)
    if port is None:
        # A cold Python can take more than the 2 s terminal_open waits to say where it serves.
        _, more = await _call(ctx, "terminal_read", {"session": session, "wait_s": 30})
        opened += more
        port = re.search(_SERVING, opened)
    assert port is not None, opened
    url: str = f"http://127.0.0.1:{port.group(1)}/"
    async with httpx.AsyncClient(trust_env=False) as client:
        served: httpx.Response = await client.get(url)
        _, log = await _call(ctx, "terminal_read", {"session": session, "wait_s": 5})
        _, closed = await _call(ctx, "terminal_close", {"session": session})
        with pytest.raises(httpx.ConnectError):
            await client.get(url)
    assert served.status_code == 200
    assert '"GET / HTTP/1.1" 200' in log, log
    assert closed == f"[session {session} is closed]"


class _Watcher:
    """What a run manager hears of the sessions: the chats whose sessions changed, and the commands in the background
    that ended, as ``(chat, session, exit status)``."""

    def __init__(self) -> None:
        self.changed: list[str] = []
        self.ended: asyncio.Queue[tuple[str, str, int | None]] = asyncio.Queue()

    async def sessions_changed(self, thread_id: str) -> None:
        self.changed.append(thread_id)

    async def command_ended(self, thread_id: str, session: TerminalSession) -> None:
        self.ended.put_nowait((thread_id, session.id, session.exit_status))


async def test_a_command_in_the_background_runs_unread_and_its_end_alone_is_told(runtime: Runtime) -> None:
    """Item 229: the idle are closed, but not a command in the background while it runs; its end is told with its exit
    status, and it is kept the idle time more for what it printed to be read. A session closed by hand tells nothing."""
    # The idle time leaves a second of slack around each check, so a busy Mac (the tests run spread over its cores)
    # does not let the reaper overtake one.
    terminals: Terminals = Terminals(3, 2.0, runtime.shell_env)
    watcher: _Watcher = _Watcher()
    terminals.watch(watcher)
    try:
        idle: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), False)
        left: TerminalSession = await terminals.open("chat", "sleep 5; echo done", (), workspace_dir(), True)
        stopped: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), True)
        await terminals.close("chat", stopped.id)
        # The reaper looks every half of the idle time.
        await asyncio.sleep(4.0)
        assert [session.id for session in terminals.sessions("chat")] == [left.id], idle.id
        assert await asyncio.wait_for(watcher.ended.get(), 10.0) == ("chat", left.id, 0)
        assert [session.id for session in terminals.sessions("chat")] == [left.id]
        await asyncio.sleep(4.0)
        assert terminals.sessions("chat") == []
        # Opened three times, closed by hand, closed idle twice, and one end: each was heard.
        assert watcher.changed.count("chat") >= 6, watcher.changed
        assert watcher.ended.empty()
    finally:
        await terminals.aclose()


async def test_the_end_of_a_command_in_the_background_is_taken_once(runtime: Runtime) -> None:
    """Item 437: the end of a command in the background is handed out once, to the run at work that is told in a note or
    to the run started to tell it, with how long the command ran; a command still running, a session that is no command
    in the background and one that was closed are never handed out; one put back is handed out again."""
    terminals: Terminals = Terminals(4, 60.0, runtime.shell_env)
    watcher: _Watcher = _Watcher()
    terminals.watch(watcher)
    try:
        ended: TerminalSession = await terminals.open("chat", "exit 3", (), workspace_dir(), True)
        plain: TerminalSession = await terminals.open("chat", "exit 4", (), workspace_dir(), False)
        running: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), True)
        assert await asyncio.wait_for(watcher.ended.get(), 10.0) == ("chat", ended.id, 3)
        # The plain session's program ended too, and it is no command in the background.
        await asyncio.sleep(1.0)
        assert (plain.exit_status, running.exit_status) == (4, None)
        assert terminals.take_ended("other") == []
        assert terminals.has_untold_ends("chat") and not terminals.has_untold_ends("other")
        assert terminals.take_ended("chat") == [ended]
        assert terminals.take_ended("chat") == [] and not terminals.has_untold_ends("chat")
        assert ended.ended_at is not None and 0 <= ended.ended_at - ended.started_at < 10
        assert ended_notices([ended]).startswith(f"The command in the background {ended.id} ended with exit 3 after ")
        terminals.put_back_ended([ended])
        assert terminals.take_ended("chat") == [ended]
        terminals.put_back_ended([ended])
        await terminals.close("chat", ended.id)
        assert terminals.take_ended("chat") == []
    finally:
        await terminals.aclose()


async def test_a_close_cancelled_while_it_waits_still_releases_the_terminal(runtime: Runtime) -> None:
    """Item 351: a close cancelled while it gave the program its grace left the session's terminal open and its
    program running; both go now."""
    terminals: Terminals = Terminals(3, 1.0, runtime.shell_env)
    try:
        stubborn: TerminalSession = await terminals.open("chat", "trap '' TERM; sleep 30", (), workspace_dir(), False)
        # Let the shell set its trap first, so that the close waits out its grace.
        await asyncio.sleep(0.5)
        closing: asyncio.Task[None] = asyncio.create_task(terminals.close("chat", stubborn.id))
        await asyncio.sleep(0.3)
        closing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await closing
        assert stubborn.finished.is_set() and stubborn.ended
        assert await asyncio.wait_for(stubborn.exit_code(), 5.0) != 0
    finally:
        await terminals.aclose()


async def test_the_log_line_of_an_opened_terminal_masks_a_key_in_its_command(runtime: Runtime) -> None:
    """Item 690: the command is logged at info as the session opens, so what it carries (a password given to an option,
    a token in its environment) is masked there as it is in the events."""
    terminals: Terminals = Terminals(3, 1.0, runtime.shell_env)
    try:
        with capture_logs() as logs:
            session: TerminalSession = await terminals.open(
                "chat", "MYSQL_PASSWORD=hunter2hunter2 true --api-key abc123def456", (), workspace_dir(), False
            )
        opened: list[str] = [str(line["command"]) for line in logs if line["event"] == "terminal_opened"]
        assert opened == ["MYSQL_PASSWORD=*** true --api-key ***"]
        assert session.command.startswith("MYSQL_PASSWORD=hunter2hunter2")
    finally:
        await terminals.aclose()


async def _read_to_end(session: TerminalSession) -> str:
    """What the session's program printed, read until its end and once more after it, for what came with the end."""
    text: str = ""
    while not session.ended:
        text += await session.read(1.0)
    return text + await session.read(0.0)


async def test_a_program_that_ends_at_once_leaves_what_it_printed_and_its_session_ends(runtime: Runtime) -> None:
    """Many programs that print and end at once, side by side: what each printed is read and each session ends. While
    the program's exit closed the terminal's other end last, macOS kept what it printed from the read side, and a
    program waited in its exit for that output to drain, for good."""
    terminals: Terminals = Terminals(1, 60.0, runtime.shell_env)
    try:
        sessions: list[TerminalSession] = list(
            await asyncio.gather(
                *(
                    terminals.open(f"chat{index}", "echo said-$((40 + 2)) >&2; exit 3", (), workspace_dir(), False)
                    for index in range(40)
                )
            )
        )
        texts: list[str] = list(
            await asyncio.gather(*(asyncio.wait_for(_read_to_end(session), 10.0) for session in sessions))
        )
        assert [text.strip() for text in texts] == ["said-42"] * len(sessions), texts
        assert [await session.exit_code() for session in sessions] == [3] * len(sessions)
    finally:
        await terminals.aclose()


async def test_a_session_closed_while_its_program_prints_on_its_way_out_closes_in_time(runtime: Runtime) -> None:
    """A program that prints a lot as it is told to stop (a server logging its shutdown): its session closes, and no
    close of the terminal waits on output that nobody reads any more."""
    terminals: Terminals = Terminals(1, 60.0, runtime.shell_env)
    try:
        talker: TerminalSession = await terminals.open(
            "chat", "trap 'yes stopping | head -c 400000; exit 0' TERM; sleep 30", (), workspace_dir(), False
        )
        # Let the shell set its trap first.
        await asyncio.sleep(0.5)
        await asyncio.wait_for(terminals.close("chat", talker.id), 10.0)
        assert talker.finished.is_set() and talker.ended
        assert terminals.sessions("chat") == []
    finally:
        await terminals.aclose()


async def test_a_command_in_the_background_needs_a_chat_to_tell(runtime: Runtime) -> None:
    """Item 229: a delegated run opens no command in the background, since no chat of its own would hear of its end;
    nor does a process whose chats hear of no command's end, as a dotx command's, which ends with its run."""
    ctx: ToolContext = await _yolo_context(runtime)
    helper: RunRecord = await runtime.store.create_run(
        ctx.thread_id,
        GOAL,
        "task",
        "ollama:none",
        ctx.run_id,
        "delegate",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    background: JsonObject = {"command": "sleep 5", "background": True}
    with pytest.raises(ToolInputError, match="a delegated run or a campaign's session has none"):
        await _call(dataclasses.replace(ctx, run_id=helper.id), "terminal_open", background)
    # No run manager watches this runtime's sessions.
    with pytest.raises(ToolInputError, match="tells no chat"):
        await _call(ctx, "terminal_open", background)
    assert runtime.terminals.sessions(ctx.thread_id) == []


async def test_a_chat_keeps_its_limit_of_sessions_and_an_idle_session_is_closed(runtime: Runtime) -> None:
    terminals: Terminals = Terminals(1, 1.0, runtime.shell_env)
    try:
        first: str = (await terminals.open("chat", "sleep 30", (), workspace_dir(), False)).id
        with pytest.raises(ToolInputError, match="the most it may have"):
            await terminals.open("chat", "sleep 30", (), workspace_dir(), False)
        await terminals.open("another chat", "sleep 30", (), workspace_dir(), False)
        # The reaper looks every half of the idle time.
        await asyncio.sleep(2.5)
        with pytest.raises(ToolInputError, match=f"no terminal session {first}"):
            terminals.get("chat", first)
    finally:
        await terminals.aclose()


async def test_the_emergency_stop_closes_every_terminal_and_drops_what_waits_to_be_typed(runtime: Runtime) -> None:
    """Item 683: what a run sent a program that has not read it yet would be typed after the stop, and what it left
    running would go on; the stop closes every session of every chat."""
    terminals: Terminals = Terminals(3, 60.0, runtime.shell_env)
    try:
        first: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), False)
        second: TerminalSession = await terminals.open("other", "cat", (), workspace_dir(), False)
        second.send("x" * 20_000 + "\n")
        assert await terminals.close_all() == 2
        assert terminals.sessions("chat") == [] and terminals.sessions("other") == []
        assert first.ended and second.ended
    finally:
        await terminals.aclose()
