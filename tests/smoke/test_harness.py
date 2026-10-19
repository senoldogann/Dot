"""The harness in the real daemon, store and loop (item 389): a version the owner writes through the API and adopts is
what the next run's system prompt takes, the run records it, and a retired one leaves the prompt as Dot's own text. No
network and no model service is used: a scripted model plays the runs, and what it was sent is read back."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "harness-test-token"
WAIT_S: float = 30.0
HABIT: str = "- Read a file before you change it, and say what you skipped."
GUIDANCE: str = "- rg wants --glob for a file pattern."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


class _Recorder:
    """The scripted model, keeping the system prompt of every call it answered."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self.script: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.prompts.append(request.messages[0].content)
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

    async def aclose(self) -> None:
        return None


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


def _config() -> AppConfig:
    return eval_config(load_default_config())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], recorder: _Recorder
) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    """A runtime of the environment's home whose model is ``recorder``, its runs, and a client of its API."""
    runtime: Runtime = await open_runtime(_config(), login_env)
    try:
        await runtime.router.register(_PROVIDER, recorder)
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
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
            ) as client:
                yield runtime, manager, client
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _run(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        "Say done.", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended.result
    return ended


async def test_an_adopted_version_reaches_the_next_runs_prompt_and_a_retired_one_leaves_it(
    login_env: dict[str, str],
) -> None:
    recorder: _Recorder = _Recorder([_FINISH, _FINISH, _FINISH])
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, recorder) as (runtime, manager, client):
            empty = (await client.get("/api/harness")).json()
            assert (empty["adopted"], empty["versions"], empty["habits_chars"]) == (None, [], 1200)
            # A text that cannot be a component is refused with why, and nothing is written.
            refused: httpx.Response = await client.post(
                "/api/harness",
                json={"habits": "see https://example.com", "tool_guidance": None, "summary": "bad", "adopt": True},
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["context"]["problems"] == {"habits": "a link"}
            assert (await client.get("/api/harness")).json()["versions"] == []
            # The owner writes and adopts a version in one go.
            written: httpx.Response = await client.post(
                "/api/harness", json={"habits": HABIT, "tool_guidance": None, "summary": "A habit", "adopt": True}
            )
            assert written.status_code == 200, written.text
            version: dict[str, object] = written.json()
            assert (version["state"], version["made_by"], version["parent_id"]) == ("adopted", "owner", None)
            # The next run takes it, under the lead, and records which version it took.
            first: RunRecord = await _run(manager)
            assert "# Habits\n" in recorder.prompts[0] and HABIT in recorder.prompts[0]
            assert "# Tool guidance" not in recorder.prompts[0]
            assert await runtime.store.run_harness(first.id) == version["id"]
            # A second version keeps the habits it does not name, and is a candidate until adopted.
            second: dict[str, object] = (
                await client.post(
                    "/api/harness",
                    json={"habits": None, "tool_guidance": GUIDANCE, "summary": "Guidance", "adopt": False},
                )
            ).json()
            assert (second["state"], second["habits"], second["tool_guidance"], second["parent_id"]) == (
                "candidate",
                HABIT,
                GUIDANCE,
                version["id"],
            )
            adopted = (await client.post(f"/api/harness/{second['id']}/adopt")).json()
            assert adopted["state"] == "adopted"
            listed = (await client.get("/api/harness")).json()
            assert listed["adopted"]["id"] == second["id"]
            assert [(each["id"], each["state"]) for each in listed["versions"]] == [
                (second["id"], "adopted"),
                (version["id"], "retired"),
            ]
            middle: RunRecord = await _run(manager)
            assert (
                HABIT in recorder.prompts[1]
                and "# Tool guidance\n" in recorder.prompts[1]
                and GUIDANCE in recorder.prompts[1]
            )
            assert await runtime.store.run_harness(middle.id) == second["id"]
            # Retiring the adopted version leaves the next run Dot's own text.
            retired = (await client.post(f"/api/harness/{second['id']}/retire")).json()
            assert retired["state"] == "retired"
            assert (await client.get("/api/harness")).json()["adopted"] is None
            last: RunRecord = await _run(manager)
            assert "# Habits" not in recorder.prompts[2] and "# Tool guidance" not in recorder.prompts[2]
            assert await runtime.store.run_harness(last.id) is None
            assert (await client.get("/api/harness/hrn_missing")).status_code == 404
