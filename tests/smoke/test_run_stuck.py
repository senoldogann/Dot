"""The daemon's side of the watchdog (item 925): a run the watchdog saw working with no step is told to the owner, with
its page to open; one that no longer works is not."""

import asyncio
import time

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.events import Subscription
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

TOKEN: str = "run-stuck-test-token"


async def _told(subscription: Subscription) -> list[EventRecord]:
    told: list[EventRecord] = []
    while not subscription.queue.empty():
        event: EventRecord = subscription.queue.get_nowait()
        if event.kind == OWNER_TOLD_EVENT:
            told.append(event)
    return told


async def test_a_stuck_run_is_told_with_its_page_and_an_ended_one_is_not() -> None:
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(runtime)
        try:
            thread = await runtime.store.create_thread("Deploy", "ui")
            run: RunRecord = await runtime.store.create_run(
                thread.id, "Build the site and deploy it", "task", "ollama:none", None, "ui", "medium", "standard",
                None, "yolo", False, None, None, None,
            )  # fmt: skip
            await runtime.store.set_run_status(run.id, "running")
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=TOKEN,
                host="127.0.0.1",
                port=8765,
                started_at=time.time(),
                stopping=asyncio.Event(),
            )
            async with (
                runtime.bus.subscribe(None) as subscription,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(context)),
                    base_url="http://127.0.0.1:8765",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                ) as client,
            ):
                answer: httpx.Response = await client.post(f"/api/runs/{run.id}/stuck", json={"quiet_s": 2400})
                assert answer.status_code == 200 and answer.json() == {"told": True}, answer.text
                await runtime.store.finish_run(run.id, "done", "Deployed.")
                ended: httpx.Response = await client.post(f"/api/runs/{run.id}/stuck", json={"quiet_s": 2400})
                assert ended.json() == {"told": False}
                await asyncio.sleep(0.05)
                told: list[EventRecord] = await _told(subscription)
            assert [event.data["title"] for event in told] == ["A task makes no progress"]
            assert told[0].data["url"] == f"#/runs/{run.id}" and "40 min with no step" in str(told[0].data["text"])
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
