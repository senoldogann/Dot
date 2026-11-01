"""A message sent to a chat while its run works (item 915): the fast model says whether it is a request of its own; one
that is starts at once in a chat of its own, and the chat at work gets a line that leads to it; one that is part of the
work under way, or that cannot be told, waits in the chat's queue as before. Served by a real server, the queue's own
route, the run at work held at its first step by a gated scripted model.

No network and no model service is used: the scripted model plays the runs and answers for the fast model too."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.busy import SEPARATE_TOOL
from dotagent.agent.manager import STARTED_BESIDE_EVENT, RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ProviderProtocolError
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
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "busy-test-token"
WAIT_S: float = 30.0
GOAL: str = "Write the quarterly report from the notes."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class _Model(ScriptedProvider):
    """Runs that stay at work until :meth:`release`, and a fast model whose verdict is ``separate`` (``None``: it
    fails), counted in ``asked``."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH] * 16)
        self.separate: bool | None = True
        self.asked: list[str] = []
        self.go: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if any(spec.name == SEPARATE_TOOL for spec in request.tools):
            self.asked.append(request.messages[-1].content)
            if self.separate is None:
                raise ProviderProtocolError("the fast model failed", {})
            verdict: ScriptedTurn = ScriptedTurn(
                text="", calls=(ScriptedCall(tool=SEPARATE_TOOL, arguments={"separate": self.separate}),)
            )
            return await ScriptedProvider(_PROVIDER, [verdict]).complete(request, sink)
        await self.go.wait()
        return await super().complete(request, sink)


def _config(on: bool) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"busy_parallel": on}),
            "models": config.models.model_copy(update={"fast": SCRIPTED_MODEL}),
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(
    login_env: dict[str, str], model: _Model, on: bool
) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(on), login_env)
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
            model.go.set()
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
            await close_runtime(runtime)


async def _send(client: httpx.AsyncClient, thread_id: str, text: str, **more: object) -> JsonObject:
    answer: httpx.Response = await client.post(
        f"/api/threads/{thread_id}/queue", json={"text": text, "mode": "task", "model": SCRIPTED_MODEL, **more}
    )
    assert answer.status_code == 201, answer.text
    body: JsonObject = answer.json()
    return body


def _texts(view: JsonObject) -> list[str]:
    """The texts of the queued messages of a queue's view."""
    messages: object = view["messages"]
    assert isinstance(messages, list)
    texts: list[str] = []
    for message in messages:
        assert isinstance(message, dict) and isinstance(message["text"], str)
        texts.append(message["text"])
    return texts


async def _at_work(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )
    # The run is the chat's at work from its start: its first step waits for the gate.
    assert run.id in manager.active_run_ids()
    return run


async def test_a_request_of_its_own_starts_at_once_in_a_chat_of_its_own_and_the_rest_waits(
    login_env: dict[str, str],
) -> None:
    model: _Model = _Model()
    async with _served(login_env, model, True) as (runtime, manager, client):
        run: RunRecord = await _at_work(manager)
        # Its own: started now, in another chat, and the chat at work has the line that leads to it.
        beside: JsonObject = await _send(client, run.thread_id, "Also find a dentist open on Saturday.")
        assert beside["messages"] == [] and isinstance(beside["beside_run_id"], str)
        started: RunRecord = await runtime.store.get_run(str(beside["beside_run_id"]))
        assert started.thread_id != run.thread_id and started.goal == "Also find a dentist open on Saturday."
        assert started.parent_run_id is None and started.source == UI_SOURCE
        events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 1000)
        (line,) = [event.data for event in events if event.kind == STARTED_BESIDE_EVENT]
        assert line == {
            "run_id": started.id,
            "thread_id": started.thread_id,
            "text": "Also find a dentist open on Saturday.",
        }
        assert "Write the quarterly report" in model.asked[0] and "dentist" in model.asked[0]
        # Part of the work under way: queued as before.
        model.separate = False
        queued: JsonObject = await _send(client, run.thread_id, "Use the March figures too.")
        assert _texts(queued) == ["Use the March figures too."]
        assert queued["beside_run_id"] is None
        # A fast model that fails queues it too.
        model.separate = None
        failed: JsonObject = await _send(client, run.thread_id, "And book a table for two.")
        assert len(_texts(failed)) == 2 and failed["beside_run_id"] is None
        # A message for one level, or with picks, is queued without asking: the queue carries what it chose.
        asked: int = len(model.asked)
        await _send(client, run.thread_id, "Plan the trip.", plan_first=True)
        assert len(model.asked) == asked
        # The same message sent again with its key (its answer was lost) says the chat it started the first time.
        model.separate = True
        first: JsonObject = await _send(client, run.thread_id, "Find a florist too.", key="florist-request-0001")
        again: JsonObject = await _send(client, run.thread_id, "Find a florist too.", key="florist-request-0001")
        assert isinstance(first["beside_run_id"], str) and again["beside_run_id"] == first["beside_run_id"]


async def test_a_message_to_a_teams_chat_at_work_stays_the_teams(login_env: dict[str, str]) -> None:
    """The pre-push review: a chat with a team works as the team, so a request sent there waits in its queue."""
    model: _Model = _Model()
    async with _served(login_env, model, True) as (runtime, manager, client):
        team = await runtime.store.create_team("Writers", "Write the reports", [], None)
        chat = await runtime.store.create_thread(GOAL, UI_SOURCE, team.id)
        run: RunRecord = await manager.start(
            GOAL, "task", SCRIPTED_MODEL, chat.id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        queued: JsonObject = await _send(client, run.thread_id, "Also find a dentist open on Saturday.")
        assert len(_texts(queued)) == 1 and queued["beside_run_id"] is None and model.asked == []


async def test_with_the_switch_off_every_message_waits_and_nothing_is_asked(login_env: dict[str, str]) -> None:
    model: _Model = _Model()
    async with _served(login_env, model, False) as (_runtime, manager, client):
        run: RunRecord = await _at_work(manager)
        queued: JsonObject = await _send(client, run.thread_id, "Also find a dentist open on Saturday.")
        assert len(_texts(queued)) == 1 and queued["beside_run_id"] is None
        assert model.asked == []
