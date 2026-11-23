"""A run's stream goes on after the ``Last-Event-ID`` a client sends (item 1352), as it does after ``after``: the
native phone app reconnects with both, and must not be sent the run's whole history again. A real server on a scripted
model, the stream of a finished run read with ``stored=true``."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

TOKEN: str = "last-event-id-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_ANSWER: str = "All done."
_SCRIPT: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo checked"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": _ANSWER, "success": True}),)),
)
_ID: re.Pattern[str] = re.compile(r"^id: (\d+)$", re.MULTILINE)


@asynccontextmanager
async def _served() -> AsyncIterator[tuple[RunManager, str]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(
            eval_config(load_default_config()),
            await read_login_env(load_default_config().shell.login_env_timeout_s, ()),
        )
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _SCRIPT))
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
                yield manager, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _ids(address: str, run_id: str, query: str, headers: dict[str, str]) -> list[int]:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}", **headers}, timeout=20) as client:
        answer: httpx.Response = await client.get(f"{address}/api/runs/{run_id}/stream?stored=true{query}")
    assert answer.status_code == 200
    return [int(found) for found in _ID.findall(answer.text)]


async def test_a_stream_goes_on_after_the_last_event_id_a_client_sends() -> None:
    async with _served() as (manager, address):
        run: RunRecord = await manager.start(
            "Check it", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        every: list[int] = await _ids(address, run.id, "", {})
        assert len(every) > 4
        middle: int = every[len(every) // 2]
        expected: list[int] = [event for event in every if event > middle]
        assert await _ids(address, run.id, "", {"Last-Event-ID": str(middle)}) == expected
        assert await _ids(address, run.id, f"&after={middle}", {}) == expected
        # The later of the two wins: a header behind the query changes nothing, one ahead of it moves the stream on.
        assert await _ids(address, run.id, f"&after={middle}", {"Last-Event-ID": "1"}) == expected
        assert await _ids(address, run.id, "&after=1", {"Last-Event-ID": str(middle)}) == expected
        # A header that is no number is no id: the whole stream comes, as it did before the header was read.
        assert await _ids(address, run.id, "", {"Last-Event-ID": "not-a-number"}) == every
