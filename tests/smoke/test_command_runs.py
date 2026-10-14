"""The runs of ``dotx`` commands on the pages of the daemon (item 188). The daemon relays what the run of a command
stores, so the run works on the pages (Activity, its own page) and its page follows it live, in one stream, to its end;
its Stop asks the command to stop it, which it does; a question of it answered on the pages reaches the command at once
rather than at the end of its wait, and ends the prompt at the command's terminal; the queue of its chat moves on in
the daemon once it ends, not in the command, which ends with it; and a new run on a chat closes first the run that a
command that died left at work there.

No network and no model service is used: the scripted model plays each run, the command's in a child process that goes
through ``follow_run`` as ``dotx run`` does (``tests/fixtures/dot_cli_steps.py``), the daemon's in the test's own,
served by a real server; the page is opened in Chrome, wide and on a phone, light and dark.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import QUEUE_CHANGED_EVENT, RunManager
from dotagent.config import load_default_config
from dotagent.core.errors import StoreError
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord, ThreadRecord, WorkingRun
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Wait for the build."
SUMMARY: str = "Then write the summary."
TOKEN: str = "command-runs-test-token"
WAIT_S: float = 30.0
CLI_STEPS: Path = Path(__file__).resolve().parents[1] / "fixtures" / "dot_cli_steps.py"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Runs until the test creates the file ``stop`` in the workspace, at most 30 seconds.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"
_WAITING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
)
ANSWER: str = "The nightly one."
ELSEWHERE_HINT: str = "The run works in a dotx command, whose browser is not shown here."
_ASKING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": "Which build?"}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The build is done.", "success": True}),)
)
# More replies than any test asks of the daemon's model.
_TURNS: int = 8
# The longest transition of a button (320 ms) and a breath: a shot shows where the colours settle.
_SETTLE_MS: int = 400
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, as ``dotx daemon`` runs it: its runs, the relay of the runs of
    commands, and a real server at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * _TURNS))
        manager: RunManager = RunManager(runtime)
        relay: asyncio.Task[None] = asyncio.create_task(manager.relay_elsewhere())
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


@asynccontextmanager
async def _api(address: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
    ) as client:
        yield client


@asynccontextmanager
async def _command(
    home: Path, approvals: ApprovalMode, thread_id: str, turns: Sequence[ScriptedTurn]
) -> AsyncIterator[asyncio.subprocess.Process]:
    """``dotx run`` in a child process on ``home``, on the chat ``thread_id`` (a new one when empty), whose model plays
    ``turns``, at a terminal where nobody types; whatever the test did, its shell command is let go and the process
    ends with the block."""
    await asyncio.to_thread(_clear_the_stop)
    child: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(CLI_STEPS),
        GOAL,
        approvals,
        thread_id,
        json.dumps([turn.model_dump(mode="json") for turn in turns]),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "DOT_LOG_DIR": str(home / "logs")},
    )
    try:
        yield child
    finally:
        await asyncio.to_thread(_let_the_command_end)
        if child.stdin is not None:
            # A question still asked at its terminal ends unanswered.
            child.stdin.close()
        if child.returncode is None:
            try:
                async with asyncio.timeout(WAIT_S):
                    await child.wait()
            except TimeoutError:
                child.kill()
                await child.wait()


def _let_the_command_end() -> None:
    workspace: Path = workspace_dir()
    if workspace.is_dir():
        (workspace / "stop").touch()


def _clear_the_stop() -> None:
    """Take away the ``stop`` a command before left, which would end the next one's wait at once."""
    (workspace_dir() / "stop").unlink(missing_ok=True)


async def _said(child: asyncio.subprocess.Process, word: str) -> str:
    """Read what the command says until it says ``word``; what follows the word."""
    assert child.stdout is not None
    async with asyncio.timeout(WAIT_S):
        while True:
            line: bytes = await child.stdout.readline()
            assert line, f"the command ended before it said {word!r}"
            said, _, value = line.decode().strip().partition(" ")
            if said == word:
                return value


async def _relayed(subscription: Subscription, run_id: str, kind: str) -> EventRecord:
    """The next event of ``kind`` of the run ``run_id`` that the daemon tells its pages."""
    async with asyncio.timeout(WAIT_S):
        while True:
            event: EventRecord = await subscription.queue.get()
            if event.run_id == run_id and event.kind == kind:
                return event


async def _working(child: asyncio.subprocess.Process, relayed: Subscription) -> str:
    """The id of the command's run, once its shell command works and the daemon has told its pages so."""
    run_id: str = await _said(child, "run")
    await _said(child, "call")
    await _relayed(relayed, run_id, "tool_call")
    return run_id


async def test_a_command_run_works_on_the_pages_and_its_page_follows_it_live_to_its_end(
    login_env: dict[str, str],
) -> None:
    """Before, the run was not active for the daemon: Activity counted nothing at work, and the stream of its page ended
    after what was stored, so the page asked again and again."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, _manager, address), _api(address) as api:
            async with runtime.bus.subscribe(None) as relayed, _command(home, "yolo", "", [_WAITING, _FINISH]) as child:
                run_id: str = await _working(child, relayed)
                detail: httpx.Response = await api.get(f"/api/runs/{run_id}")
                assert (detail.json()["active"], detail.json()["elsewhere"]) == (False, True)
                count: httpx.Response = await api.get("/api/activity/count")
                assert (count.json()["working"], count.json()["state"]) == (1, "working")
                kinds: list[str] = []
                async with (
                    asyncio.timeout(WAIT_S),
                    api.stream("GET", f"/api/runs/{run_id}/stream", params={"after": 0}) as stream,
                ):
                    async for line in stream.aiter_lines():
                        if line.startswith("event: "):
                            kinds.append(line.removeprefix("event: "))
                            if kinds[-1] == "tool_call":
                                # Stored before the page came; what follows comes live.
                                await asyncio.to_thread(_let_the_command_end)
                assert await _said(child, "ended") == "done"
            assert kinds[0] == "run_queued"
            assert kinds[-1] == "run_finished"
            assert kinds.count("run_finished") == 1
            assert kinds.index("tool_result") > kinds.index("tool_call")
            count = await api.get("/api/activity/count")
            assert count.json()["working"] == 0


async def test_the_stop_of_a_command_run_on_its_page_stops_it_in_the_command(login_env: dict[str, str]) -> None:
    """Before, the Stop answered that the run was not active (409), and the command went on."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, _manager, address), _api(address) as api:
            async with runtime.bus.subscribe(None) as relayed, _command(home, "yolo", "", [_WAITING, _FINISH]) as child:
                run_id: str = await _working(child, relayed)
                stopped: httpx.Response = await api.post(f"/api/runs/{run_id}/cancel")
                assert stopped.status_code == 202, stopped.text
                assert await _said(child, "ended") == "cancelled"
                await _relayed(relayed, run_id, "run_finished")
            assert (await runtime.store.get_run(run_id)).status == "cancelled"
            assert await runtime.store.stops_asked([run_id]) == []


async def test_a_question_of_a_command_run_answered_on_its_page_reaches_the_command_at_once(
    login_env: dict[str, str],
) -> None:
    """The question is asked at the command's terminal too, where nobody answers: the answer given on the page ends
    that prompt, and the run goes on with it. Before, the command saw the answer only when its wait for it ended
    (``approval_timeout_s``, 15 minutes), and its terminal went on asking."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, _manager, address), _api(address) as api:
            async with runtime.bus.subscribe(None) as relayed, _command(home, "ask", "", [_ASKING, _FINISH]) as child:
                run_id: str = await _said(child, "run")
                approval_id: str = await _said(child, "asked")
                await _relayed(relayed, run_id, "approval_requested")
                waiting: httpx.Response = await api.get("/api/activity")
                assert [row["kind"] for row in waiting.json()["waiting"]] == ["question"]
                decided: httpx.Response = await api.post(
                    f"/api/approvals/{approval_id}/decision", json={"approve": True, "note": ANSWER}
                )
                assert decided.status_code == 200, decided.text
                assert await _said(child, "ended") == "done"
            answered: ApprovalRecord = await runtime.store.get_approval(approval_id)
            assert (answered.status, answered.note) == ("approved", ANSWER)


async def test_an_answer_typed_at_the_command_after_its_page_answered_leaves_the_pages_answer(
    login_env: dict[str, str],
) -> None:
    """An answer typed at the terminal in the moment the page answers comes second: the command goes on with the page's
    answer. Before, the typed answer failed the command, its question being decided already."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, _manager, address), _api(address) as api:
            async with _command(home, "ask", "", [_ASKING, _FINISH]) as child:
                approval_id: str = await _said(child, "asked")
                decided: httpx.Response = await api.post(
                    f"/api/approvals/{approval_id}/decision", json={"approve": True, "note": ANSWER}
                )
                assert decided.status_code == 200, decided.text
                assert child.stdin is not None
                child.stdin.write(b"The weekly one.\n")
                await child.stdin.drain()
                assert await _said(child, "ended") == "done"
                assert await child.wait() == 0
            answered: ApprovalRecord = await runtime.store.get_approval(approval_id)
            assert (answered.status, answered.note) == ("approved", ANSWER)


async def _queued_beside_a_command(runtime: Runtime, manager: RunManager, home: Path) -> None:
    """A message queued on the chat of a command's run at work stays queued while that run works, and starts in the
    daemon once it has ended done."""
    async with (
        runtime.bus.subscribe(None) as relayed,
        runtime.bus.subscribe_kinds(frozenset({QUEUE_CHANGED_EVENT})) as changes,
        _command(home, "yolo", "", [_WAITING, _FINISH]) as child,
    ):
        run: RunRecord = await runtime.store.get_run(await _working(child, relayed))
        await manager.queue(run.thread_id, SUMMARY, (), "task", SCRIPTED_MODEL, None, "yolo", False, None, UI_SOURCE)
        assert [other.id for other in await runtime.store.list_runs(10, run.thread_id)] == [run.id]
        await asyncio.to_thread(_let_the_command_end)
        assert await _said(child, "ended") == "done"
        async with asyncio.timeout(WAIT_S):
            change: EventRecord = await changes.queue.get()
            while change.data["started_run_id"] is None:
                change = await changes.queue.get()
    assert change.data["after_run_id"] == run.id
    started: str = str(change.data["started_run_id"])
    async with asyncio.timeout(WAIT_S):
        assert (await manager.wait(started)).status == "done"
    assert (await runtime.store.get_run(started)).goal == SUMMARY
    assert await runtime.store.list_queued(run.thread_id) == []


async def test_the_queue_of_a_command_runs_chat_moves_on_in_the_daemon_once_that_run_ends_done(
    login_env: dict[str, str],
) -> None:
    """Before, the message stayed in the queue: the command ended with its run, and the daemon did not learn of that
    end."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, _address):
            await _queued_beside_a_command(runtime, manager, home)


async def test_the_relay_outlives_a_read_the_store_refused_and_moves_a_queue_it_could_not_move_at_once(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relay's first read fails as a disk error would, and so does its first read of the command's run once it
    has ended: the relay goes on, and the queue moves at the next relay. Before the review of this item, the read
    ended the relay for the daemon's life, and the queue whose move failed was not moved."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, _address):
            store: Store = runtime.store
            reading = store.working_runs_of_others
            getting = store.get_run
            refused: list[str] = []

            async def refuse_the_first_read(owner: str) -> list[WorkingRun]:
                if "read" not in refused:
                    refused.append("read")
                    raise sqlite3.OperationalError("disk I/O error")
                return await reading(owner)

            async def refuse_the_first_end(run_id: str) -> RunRecord:
                run: RunRecord = await getting(run_id)
                if run.source == "cli" and run.status == "done" and "end" not in refused:
                    refused.append("end")
                    raise StoreError("the store refused for now", {"run_id": run_id})
                return run

            monkeypatch.setattr(store, "working_runs_of_others", refuse_the_first_read)
            monkeypatch.setattr(store, "get_run", refuse_the_first_end)
            await _queued_beside_a_command(runtime, manager, home)
            assert refused == ["read", "end"]


async def test_a_new_run_on_a_chat_closes_first_the_run_a_dead_command_left_at_work_there(
    login_env: dict[str, str],
) -> None:
    """A chat takes one run at work at a time, whichever process drives it; one of a command that died, which the
    daemon's half-minute check has not closed yet, is closed by the start of the next."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, _address):
            thread: ThreadRecord = await runtime.store.create_thread("Build", "test")
            # A process that is gone: its lock file is not there.
            left: RunRecord = await runtime.store.create_run(
                thread.id, GOAL, "task", SCRIPTED_MODEL, None, "cli", "medium", "yolo", False, None, None, "0" * 32
            )
            run: RunRecord = await manager.start(
                SUMMARY, "task", SCRIPTED_MODEL, thread.id, UI_SOURCE, None, None, "yolo", False, ()
            )
            assert (await runtime.store.get_run(left.id)).status == "failed"
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(run.id)).status == "done"


async def _held(runtime: Runtime, thread_id: str) -> None:
    """Wait until the queue of the chat ``thread_id`` is held: the daemon holds it as it relays the end of the run."""
    async with asyncio.timeout(WAIT_S):
        while True:
            if await runtime.store.queue_held(thread_id):
                return
            await asyncio.sleep(0.1)


async def _shot(page: Page, path: Path) -> None:
    await page.mouse.move(0, 0)
    await page.wait_for_timeout(_SETTLE_MS)
    await page.screenshot(path=str(path))


@pytest.mark.browser
async def test_the_page_of_a_command_run_shows_it_at_work_queues_a_message_and_stops_it(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """The page of a command's run follows it as the daemon's own runs: its Stop is offered, a message waits in the
    queue (it takes no steer), and the Stop ends the run in the command, which the page shows."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, _manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    async with (
                        runtime.bus.subscribe(None) as relayed,
                        _command(home, "yolo", "", [_WAITING, _FINISH]) as child,
                    ):
                        run_id: str = await _working(child, relayed)
                        page: Page = await browser.new_page(
                            viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                        )
                        await page.goto(ui_url(host, port, TOKEN))
                        await page.goto(f"{address}/#/runs/{run_id}")
                        box: Locator = page.locator(".dock textarea")
                        send: Locator = page.locator(".dock .btn.send")
                        await expect(send).to_have_attribute("aria-label", "Stop")
                        await box.fill(SUMMARY)
                        await expect(send).to_have_attribute("aria-label", "Queue")
                        await box.press("Enter")
                        await expect(box).to_have_value("")
                        await expect(page.locator(".dock .queue .queue-text")).to_have_text([SUMMARY])
                        await expect(page.locator(".dock .queue .queue-steer")).to_be_disabled()
                        await _shot(page, tmp_path / f"command-run-{width}-{scheme}.png")
                        # Its computer says where its browser is, and offers no takeover.
                        side: Page = await browser.new_page(
                            viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                        )
                        await side.goto(ui_url(host, port, TOKEN))
                        await side.goto(f"{address}/#/runs/{run_id}")
                        await side.get_by_role("button", name="Run details").click()
                        await side.get_by_role("tab", name="Screen").click()
                        card: Locator = side.locator("section.computer")
                        await expect(card.locator(".computer-screen .hint")).to_have_text(ELSEWHERE_HINT)
                        await expect(card.locator(".computer-pill")).to_be_hidden()
                        await _shot(side, tmp_path / f"command-computer-{width}-{scheme}.png")
                        await side.close()
                        await send.click()
                        assert await _said(child, "ended") == "cancelled"
                        await expect(send).not_to_have_attribute("aria-label", "Stop")
                        await page.close()
                    # The stop holds the chat's queue, as a stop of a run of the daemon does.
                    await _held(runtime, (await runtime.store.get_run(run_id)).thread_id)
            finally:
                await browser.close()
