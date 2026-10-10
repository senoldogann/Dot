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
from dotagent.terminal.session import Terminals
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Work in a terminal."
SECRET: str = "correct-horse-7"


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
        thread.id, GOAL, "task", "ollama:none", None, "test", "medium", "yolo", False, None
    )
    return ToolContext(
        runtime=runtime,
        run_id=run.id,
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
            "yolo",
            _nobody,
            (GOAL,),
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


async def test_a_server_stays_up_between_calls_and_stops_with_its_session(runtime: Runtime) -> None:
    ctx: ToolContext = await _yolo_context(runtime)
    _, opened = await _call(ctx, "terminal_open", {"command": "python3 -m http.server 0 --bind 127.0.0.1"})
    session: str = _session(opened)
    port: re.Match[str] | None = re.search(r"Serving HTTP on 127\.0\.0\.1 port (\d+)", opened)
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


async def test_a_chat_keeps_its_limit_of_sessions_and_an_idle_session_is_closed(runtime: Runtime) -> None:
    terminals: Terminals = Terminals(1, 1.0, runtime.shell_env)
    try:
        first: str = (await terminals.open("chat", "sleep 30", workspace_dir())).id
        with pytest.raises(ToolInputError, match="the most it may have"):
            await terminals.open("chat", "sleep 30", workspace_dir())
        await terminals.open("another chat", "sleep 30", workspace_dir())
        # The reaper looks every half of the idle time.
        await asyncio.sleep(2.5)
        with pytest.raises(ToolInputError, match=f"no terminal session {first}"):
            terminals.get("chat", first)
    finally:
        await terminals.aclose()
