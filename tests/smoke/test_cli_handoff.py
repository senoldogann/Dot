"""``dotx run`` hands its run to the daemon of its Dot home (item 1203): the daemon starts it, as a run of its own and
not one of another process's, so that it shares the daemon's Chrome and is shown on the pages; the command follows it
and prints the answer, and ends 0 for a run that ended done. With ``--no-input`` a question the run waits on is
answered by the terminal with a no, as in a run of the command's own process, so the caller is not kept waiting. With
no daemon of the home, the command keeps the run in its own process.

No network and no model service is used: the scripted model plays the daemon's runs, and the daemon is a real server on
a port of the test's own, in the test's process; ``cmd_run`` is the command itself.
"""

import asyncio
import os
import secrets
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from dotagent.agent.declined import CLI_DECIDER
from dotagent.agent.manager import RunManager
from dotagent.cli_commands import RunCommand
from dotagent.cli_handoff import daemon_of_this_home
from dotagent.cli_run import cmd_run
from dotagent.config import ServerConfig, load_default_config
from dotagent.core.errors import NotFoundError, ServiceError
from dotagent.core.paths import ensure_layout, token_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.guard import read_or_create_token
from dotagent.store.models import EventRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = pytest.mark.asyncio

ANSWER: str = "The build is done."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)
)
_WAITING: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 30"}),))
_ASKING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": "Which build?", "wait": True}),)
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


def _command(approvals: ApprovalMode, review: bool) -> RunCommand:
    return RunCommand(
        name="run",
        goal=["Wait for the build."],
        mode="task",
        model=SCRIPTED_MODEL,
        effort=None,
        approvals=approvals,
        goal_mode=False,
        standing=False,
        browser=None,
        thread=None,
        proof=[],
        proof_timeout_s=300,
        review=review,
        no_input=True,
        thinking=False,
        verbose=False,
    )


@asynccontextmanager
async def _daemon(home: Path, turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """The daemon of ``home``, with the installation's token and the scripted model playing ``turns``, served at a port
    of its own that ``home``'s config.toml names, as ``dotx run`` reads it."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), dict(os.environ))
    manager: RunManager = RunManager(runtime)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        token: str = read_or_create_token(token_path())
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        await asyncio.to_thread(
            (home / "config.toml").write_text, f'[server]\nhost = "127.0.0.1"\nport = {port}\n', encoding="utf-8"
        )
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=token,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _started(subscription: Subscription) -> list[str]:
    """The runs whose start the daemon's own bus told: those it drives, not those another process does."""
    seen: list[EventRecord] = []
    while not subscription.queue.empty():
        seen.append(subscription.queue.get_nowait())
    return [event.run_id for event in seen if event.kind == "run_started"]


async def test_dotx_run_hands_its_run_to_the_daemon_and_prints_its_answer(
    own_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with _daemon(own_home, [_FINISH]) as (runtime, manager):
        async with runtime.bus.subscribe(None) as subscription:
            code: int = await cmd_run(_command("ask", False))
            started: list[str] = await _started(subscription)
        printed = capsys.readouterr()
        (run,) = await runtime.store.list_runs(10, None)
        assert code == 0, printed.err
        # The log of the test's process is not set up, and its lines go to standard output too.
        assert printed.out.endswith(f"\n{ANSWER}\n")
        assert "[done] 1 steps" in printed.err and f"run {run.id}" in printed.err
        assert started == [run.id], "the daemon drives the run itself"
        assert manager.elsewhere_run_ids() == []
        assert run.source == "cli" and run.status == "done"


async def test_a_review_is_handed_with_its_own_source(own_home: Path) -> None:
    """``dotx run --review`` (the pre-push hook's) starts a run whose chat the sidebar keeps under Code reviews."""
    async with _daemon(own_home, [_FINISH]) as (runtime, _manager):
        assert await cmd_run(_command("ask", True)) == 0
        (run,) = await runtime.store.list_runs(10, None)
        assert run.source == "review"
        assert (await runtime.store.get_thread(run.thread_id)).source == "review"


async def test_a_review_whose_chat_was_deleted_starts_in_a_new_chat(
    own_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pre-push hook continues a branch's review chat (``--thread``); one the owner deleted since starts the review
    in a new chat rather than refusing it (item 1986), and a run that is not a review is still refused."""
    async with _daemon(own_home, [_FINISH]) as (runtime, _manager):
        with pytest.raises(NotFoundError):
            await cmd_run(_command("ask", False).model_copy(update={"thread": "thr_deleted_000000"}))
        assert await cmd_run(_command("ask", True).model_copy(update={"thread": "thr_deleted_000000"})) == 0
        (run,) = await runtime.store.list_runs(10, None)
    assert run.source == "review" and run.status == "done" and run.thread_id != "thr_deleted_000000"
    printed: str = capsys.readouterr().err
    assert "the chat thr_deleted_000000 is gone; the review starts in a new chat" in printed
    assert f"thread {run.thread_id}" in printed


async def test_a_question_of_a_handed_run_is_answered_no_by_a_terminal_without_input(
    own_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with _daemon(own_home, [_ASKING, _FINISH]) as (runtime, _):
        code: int = await asyncio.wait_for(cmd_run(_command("ask", False)), 30.0)
        printed = capsys.readouterr()
        (run,) = await runtime.store.list_runs(10, None)
        (question,) = [
            approval for approval in await runtime.store.list_approvals(None, 10) if approval.run_id == run.id
        ]
    assert code == 0, printed.err
    assert "? Which build?" in printed.err
    assert (question.status, question.decided_by, question.note) == ("denied", CLI_DECIDER, "no answer was given")


async def test_ctrl_c_stops_the_handed_run_in_the_daemon(own_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    async with _daemon(own_home, [_WAITING]) as (runtime, manager):
        running: asyncio.Task[int] = asyncio.create_task(cmd_run(_command("yolo", False)))
        deadline: float = time.monotonic() + 20.0
        # The command shows the run's first event once its Ctrl-C is its own: a SIGINT before would end pytest.
        shown: str = ""
        while "▶ " not in shown:
            assert time.monotonic() < deadline, f"the command did not show the run: {shown}"
            assert not running.done(), shown
            shown += capsys.readouterr().err
            await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)
        code: int = await asyncio.wait_for(running, 20.0)
        (run,) = await runtime.store.list_runs(10, None)
        shown += capsys.readouterr().err
    assert code == 1
    assert "stopping the run" in shown
    assert run.status not in ("running", "done"), run.status
    assert manager.active_run_ids() == []


async def test_with_no_daemon_of_the_home_the_run_stays_in_the_command(own_home: Path) -> None:
    server: ServerConfig = load_default_config().server.model_copy(update={"host": "127.0.0.1", "port": 9})
    # No daemon ever started in this home: there is no token, and nothing is asked.
    assert await daemon_of_this_home(server) is None
    read_or_create_token(token_path())
    # A token, and nothing listening at the address.
    assert await daemon_of_this_home(server) is None


async def test_a_daemon_of_another_home_at_the_address_is_said_and_sent_nothing(own_home: Path) -> None:
    async with _daemon(own_home, []) as (runtime, _):
        server: ServerConfig = runtime.config.server.model_copy(
            update={"host": "127.0.0.1", "port": _port_of(own_home)}
        )
        token_path().unlink()
        token_path().write_text(secrets.token_hex(32), encoding="utf-8")
        token_path().chmod(0o600)
        with pytest.raises(ServiceError, match="does not hold the token"):
            await daemon_of_this_home(server)


def _port_of(home: Path) -> int:
    text: str = (home / "config.toml").read_text(encoding="utf-8")
    return int(text.split("port = ")[1].split()[0])
