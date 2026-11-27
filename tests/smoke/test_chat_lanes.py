"""Several tasks at once in one chat (item 1984, slice 1 of item 1982's design): with ``[agent] chat_tasks`` above 1, a
request of its own sent while the chat works starts in a lane of the chat (a thread the chat list does not show), given
what the chat said so far, up to the cap; the answer to a question card waits for the task that asked when another run
works on its thread; and one Stop stops every task of the chat. Served by a real server, the queue's own route, the
runs held at their first step by a gated scripted model.

No network and no model service is used: the scripted model plays the runs and answers for the fast model too."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.busy import SEPARATE_TOOL
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import NotFoundError
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import ApprovalRecord, QueuedMessage, RunRecord, ThreadRecord, ThreadSummary
from dotagent.tools.meta import ASK_USER_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "lanes-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class _Model(ScriptedProvider):
    """Runs that finish at their first step, each once the gate named by a word of its goal is open (``gates``: a goal
    with none of the words finishes at once), and a fast model whose verdict is always ``separate``."""

    def __init__(self, gates: dict[str, asyncio.Event]) -> None:
        super().__init__(_PROVIDER, [_FINISH] * 32)
        self.gates: dict[str, asyncio.Event] = gates

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if any(spec.name == SEPARATE_TOOL for spec in request.tools):
            verdict: ScriptedTurn = ScriptedTurn(
                text="", calls=(ScriptedCall(tool=SEPARATE_TOOL, arguments={"separate": True}),)
            )
            return await ScriptedProvider(_PROVIDER, [verdict]).complete(request, sink)
        said: str = "\n".join(message.content for message in request.messages if message.role == "user")
        for word, gate in self.gates.items():
            if word in said:
                await gate.wait()
        return await super().complete(request, sink)


def _config(chat_tasks: int) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"busy_parallel": True, "chat_tasks": chat_tasks}),
            "models": config.models.model_copy(update={"fast": SCRIPTED_MODEL}),
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(
    login_env: dict[str, str], model: _Model, chat_tasks: int
) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(chat_tasks), login_env)
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
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
        try:
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
            headers: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=headers) as client:
                yield runtime, manager, client
        finally:
            for gate in model.gates.values():
                gate.set()
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
            await close_runtime(runtime)


async def _send(client: httpx.AsyncClient, thread_id: str, text: str) -> JsonObject:
    answer: httpx.Response = await client.post(
        f"/api/threads/{thread_id}/queue", json={"text": text, "mode": "task", "model": SCRIPTED_MODEL}
    )
    assert answer.status_code == 201, answer.text
    body: JsonObject = answer.json()
    return body


async def _start(manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )
    assert run.id in manager.active_run_ids()
    return run


async def _until(read: Callable[[], object], what: str) -> None:
    """Wait until ``read`` (a coroutine function) returns true, for at most ``WAIT_S``."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        got: object = read()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    async def done() -> bool:
        return (await runtime.store.get_run(run_id)).status in ("done", "failed", "cancelled")

    await _until(done, f"the end of {run_id}")
    return await runtime.store.get_run(run_id)


async def test_a_request_of_its_own_starts_in_a_lane_of_the_chat_up_to_the_cap_and_one_stop_stops_them_all(
    login_env: dict[str, str],
) -> None:
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "DENTIST": asyncio.Event()}
    async with _served(login_env, _Model(gates), 2) as (runtime, manager, client):
        run: RunRecord = await _start(manager, "Write the REPORT from the notes.", None)
        beside: JsonObject = await _send(client, run.thread_id, "Also find a DENTIST open on Saturday.")
        assert beside["messages"] == [] and isinstance(beside["beside_run_id"], str)
        started: RunRecord = await runtime.store.get_run(str(beside["beside_run_id"]))
        assert started.goal == "Also find a DENTIST open on Saturday." and started.parent_run_id is None
        lane: ThreadRecord = await runtime.store.get_thread(started.thread_id)
        assert lane.lane_of == run.thread_id and lane.source == UI_SOURCE
        assert beside["beside_thread_id"] == lane.id
        assert await runtime.store.lanes_of(run.thread_id) == [lane.id]

        # The lane's first run was given what the chat said so far, in front of the owner's words.
        async def opened() -> bool:
            return any(message.role == "user" for message in await runtime.store.load_messages(lane.id, 0))

        await _until(opened, "the lane's first message")
        history = await runtime.store.load_messages(lane.id, 0)
        opening: str = next(message.content for message in history if message.role == "user")
        assert "Write the REPORT from the notes." in opening and "(still at work)" in opening
        assert opening.endswith("Also find a DENTIST open on Saturday.")
        # The chat list shows the chat, not its lane.
        listed: list[ThreadSummary] = await runtime.store.list_thread_summaries(50)
        assert [summary.id for summary in listed] == [run.thread_id]
        # Two tasks work: a third request of its own waits in the chat's queue, as before lanes.
        third: JsonObject = await _send(client, run.thread_id, "And book a table for two.")
        assert third["beside_run_id"] is None
        assert [message.text for message in await runtime.store.list_queued(run.thread_id)] == [
            "And book a table for two."
        ]
        # One Stop, sent to the lane, stops every task of its chat.
        stopped: httpx.Response = await client.post(f"/api/threads/{lane.id}/stop")
        assert stopped.status_code == 202, stopped.text
        assert set(stopped.json()["stopped"]) == {run.id, started.id}
        assert (await _ended(runtime, run.id)).status == "cancelled"
        assert (await _ended(runtime, started.id)).status == "cancelled"
        # The stop held the chat's queue: the third request did not start.
        assert await runtime.store.queue_held(run.thread_id)
        assert len(await runtime.store.list_runs(10, run.thread_id)) == 1
        # Deleting the chat deletes its lane.
        await runtime.store.delete_threads([run.thread_id])
        with pytest.raises(NotFoundError):
            await runtime.store.get_thread(lane.id)


async def test_an_answer_waits_for_the_task_that_asked_while_another_works_on_its_thread(
    login_env: dict[str, str],
) -> None:
    gates: dict[str, asyncio.Event] = {"FORM": asyncio.Event(), "XCOM": asyncio.Event()}
    async with _served(login_env, _Model(gates), 2) as (runtime, manager, client):
        form: RunRecord = await _start(manager, "Fill the FORM on the site.", None)
        question: ApprovalRecord = await runtime.approvals.leave(
            form.id, ASK_USER_TOOL, "Which e-mail address?", "low", {"question": "Which e-mail address?"}
        )
        gates["FORM"].set()
        await _ended(runtime, form.id)
        other: RunRecord = await _start(manager, "Go to XCOM and improve my account.", form.thread_id)
        await manager.decide(question.id, True, UI_SOURCE, "me@example.com")
        # The answer is the form task's: it waits in the thread's queue rather than steer the run at work.
        (waiting,) = await runtime.store.list_queued(form.thread_id)
        assert isinstance(waiting, QueuedMessage)
        assert waiting.answers == question.id and waiting.text == "me@example.com"
        assert (await runtime.store.get_approval(question.id)).status == "approved"
        steer: httpx.Response = await client.post(f"/api/queue/{waiting.id}/steer")
        assert steer.status_code == 422, steer.text
        # Once the run at work has ended, the answer starts the form task's continuation.
        gates["XCOM"].set()
        await _ended(runtime, other.id)

        async def continued() -> bool:
            newest: list[RunRecord] = await runtime.store.list_runs(1, form.thread_id)
            return newest[0].id not in (form.id, other.id)

        await _until(continued, "the continuation of the form task")
        (after,) = await runtime.store.list_runs(1, form.thread_id)
        assert after.goal.startswith("Fill the FORM on the site.") and "me@example.com" in after.goal
        assert await runtime.store.list_queued(form.thread_id) == []


async def test_with_one_task_a_chat_the_answer_goes_to_the_run_at_work_as_before(login_env: dict[str, str]) -> None:
    gates: dict[str, asyncio.Event] = {"FORM": asyncio.Event(), "XCOM": asyncio.Event()}
    async with _served(login_env, _Model(gates), 1) as (runtime, manager, _client):
        form: RunRecord = await _start(manager, "Fill the FORM on the site.", None)
        question: ApprovalRecord = await runtime.approvals.leave(
            form.id, ASK_USER_TOOL, "Which e-mail address?", "low", {"question": "Which e-mail address?"}
        )
        gates["FORM"].set()
        await _ended(runtime, form.id)
        await _start(manager, "Go to XCOM and improve my account.", form.thread_id)
        await manager.decide(question.id, True, UI_SOURCE, "me@example.com")
        assert await runtime.store.list_queued(form.thread_id) == []
