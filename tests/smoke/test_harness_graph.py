"""The provenance of the harness through the API (item 392): the graph, a version's lineage, the steps and the
experiences, read from the real daemon and store."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.harness.components import HarnessText
from dotagent.harness.service import OWNER, adopt, create_version
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import HarnessVersion
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "harness-graph-token"
WAIT_S: float = 30.0


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, httpx.AsyncClient]]:
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
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
                    yield runtime, client
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_the_graph_the_lineage_the_steps_and_the_experiences_are_read_through_the_api(
    login_env: dict[str, str],
) -> None:
    async with _daemon(login_env) as (runtime, client):
        store = runtime.store
        empty = (await client.get("/api/harness/graph")).json()
        assert empty == {"nodes": [], "edges": []}
        assert (await client.get("/api/harness/steps")).json() == []
        assert (await client.get("/api/harness/experiences")).json() == []
        base: HarnessVersion = await create_version(
            store, runtime.config.harness, HarnessText(habits="- a", tool_guidance=""), "A", OWNER, "", ()
        )
        await adopt(store, base.id, 1.0)
        candidate: HarnessVersion = await create_version(
            store, runtime.config.harness, HarnessText(habits="- a\n- b", tool_guidance=""), "B", "proposer", "{}", ()
        )
        step = await store.create_harness_step(2.0, None, base.id)
        await store.finish_harness_step(step.id, 3.0, "keep", json.dumps({"candidates": [candidate.id]}), None)
        experience = await store.add_harness_experience(step.id, None, "cue", "lesson", "evidence", "low", False)
        graph = (await client.get("/api/harness/graph")).json()
        assert {node["id"] for node in graph["nodes"]} == {base.id, candidate.id, step.id, experience.id}
        assert {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]} == {
            (candidate.id, base.id, "made_from"),
            (step.id, candidate.id, "proposed"),
            (experience.id, step.id, "written_in"),
        }
        lineage = (await client.get(f"/api/harness/{candidate.id}/lineage")).json()
        assert lineage["version"]["id"] == candidate.id and [each["id"] for each in lineage["ancestors"]] == [base.id]
        assert [each["id"] for each in lineage["steps"]] == [step.id]
        # The fixed places (steps, experiences, graph) are lists, not a version's lineage or a 404.
        assert [each["id"] for each in (await client.get("/api/harness/steps")).json()] == [step.id]
        assert [each["id"] for each in (await client.get("/api/harness/experiences")).json()] == [experience.id]
        assert (await client.get("/api/harness/hrn_missing/lineage")).status_code == 404
