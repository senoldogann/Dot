"""Programs on a terminal of their own, through the terminal tools and the gate as the executor runs them: a REPL answers
what is typed, a password prompt takes a secret only in a ``yolo`` run and the secret shows nowhere, a server stays up
until its session is closed, and a chat keeps at most its limit of sessions, each closed once it is idle. No model is
called.
"""

import asyncio
import dataclasses
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.terminal.session import Terminals, TerminalSession
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


async def test_a_password_prompt_takes_a_secret_only_in_yolo_and_the_secret_shows_nowhere(runtime: Runtime) -> None:
    ctx: ToolContext = await _yolo_context(runtime)
    _, opened = await _call(
        ctx, "terminal_open", {"command": 'read -s "pw?Password: "; echo; echo "got ${#pw} characters"'}
    )
    session: str = _session(opened)
    typing: JsonObject = {"session": session, "text": f"{SECRET}\n"}
    with pytest.raises(ToolInputError, match="asks for a password, and only a yolo run types secrets"):
        await runtime.registry.tools["terminal_send"].prepare(typing, dataclasses.replace(ctx, approvals="ask"))
    with pytest.raises(ToolInputError, match="type the password alone"):
        await runtime.registry.tools["terminal_send"].prepare({"session": session, "text": f"{SECRET}\nls\n"}, ctx)
    typed, answer = await _call(ctx, "terminal_send", typing)
    waived: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, "approval_auto", 10)
    assert "Password:" in opened, opened
    assert (typed.action.kind, typed.action.risk, typed.action.flags) == ("shell", "high", ("credential",))
    assert f"got {len(SECRET)} characters" in answer, answer
    assert len(waived) == 1, waived
    for shown in (typed.action.summary, typed.action.target, answer, str(waived[0].data)):
        assert SECRET not in shown, shown


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
    terminals: Terminals = Terminals(3, 1.0, runtime.shell_env)
    watcher: _Watcher = _Watcher()
    terminals.watch(watcher)
    try:
        idle: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), False)
        left: TerminalSession = await terminals.open("chat", "sleep 3; echo done", (), workspace_dir(), True)
        stopped: TerminalSession = await terminals.open("chat", "sleep 30", (), workspace_dir(), True)
        await terminals.close("chat", stopped.id)
        # The reaper looks every half of the idle time.
        await asyncio.sleep(2.5)
        assert [session.id for session in terminals.sessions("chat")] == [left.id], idle.id
        assert await asyncio.wait_for(watcher.ended.get(), 10.0) == ("chat", left.id, 0)
        assert [session.id for session in terminals.sessions("chat")] == [left.id]
        await asyncio.sleep(2.5)
        assert terminals.sessions("chat") == []
        # Opened three times, closed by hand, closed idle twice, and one end: each was heard.
        assert watcher.changed.count("chat") >= 6, watcher.changed
        assert watcher.ended.empty()
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
