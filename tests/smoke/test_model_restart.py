"""The owner's side of a model's refusal (item 1889): a run that ended on its model's no is started again in its chat on
the model the owner picked, and the old run records the pick as theirs; a run still at work is not. Settings › Models
names a model for security research within a scope, the config file keeps it, and the next such run that names no model
starts on it while any other starts on the default. Scripted models only; nothing leaves this Mac."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.agent.refusal import RESTARTED_ON_MODEL_EVENT
from dotagent.config import AppConfig, ReconConfig, ReconScope, load_default_config
from dotagent.core.paths import config_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.endings import PREFIX_MODEL_REFUSED
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

TOKEN: str = "model-restart-test-token"
MAIN: str = "anthropic:scripted-main"
GOAL: str = "Find reflected XSS on app.example.com and write it up for the program."
_STAND_IN_KEY: str = "stand-in-key-of-the-test"
_TIME_LIMIT_S: float = 60.0


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _config() -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"main": MAIN, "review": SCRIPTED_MODEL}),
            "recon": ReconConfig(owned_hosts=(), scopes=(ReconScope(program="Example VDP", hosts=("example.com",)),)),
        }
    )


@asynccontextmanager
async def _daemon() -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    config: AppConfig = _config()
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {config.providers.anthropic.api_key_env: _STAND_IN_KEY}):
        runtime: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(runtime)
        await runtime.router.register(
            "ollama", ScriptedProvider("ollama", [_finish("Found one; the report is ready.")])
        )
        await runtime.router.register("anthropic", ScriptedProvider("anthropic", [_finish("Done on the default.")]))
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
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client:
                yield runtime, manager, client
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


async def test_a_refused_run_restarts_in_its_chat_on_the_model_the_owner_picked() -> None:
    async with _daemon() as (runtime, manager, client):
        thread = await runtime.store.create_thread("XSS", "ui")
        # A yolo run: it starts again yolo, so it asks nothing new.
        old: RunRecord = await runtime.store.create_run(
            thread.id, GOAL, "task", MAIN, None, "ui", "high", "standard", None, "yolo", False, None, None, None
        )
        busy: httpx.Response = await client.post(f"/api/runs/{old.id}/restart", json={"model": SCRIPTED_MODEL})
        assert busy.status_code == 422, busy.text
        await runtime.store.finish_run(old.id, "failed", f"{PREFIX_MODEL_REFUSED} ({MAIN}). Its words:\n\nNo.")
        same: httpx.Response = await client.post(f"/api/runs/{old.id}/restart", json={"model": MAIN})
        assert same.status_code == 422 and "pick another model" in same.json()["message"], same.text
        done = await runtime.store.create_run(
            thread.id, GOAL, "task", MAIN, None, "ui", None, "standard", None, "ask", False, None, None, None
        )
        await runtime.store.finish_run(done.id, "done", "Done.")
        not_refused: httpx.Response = await client.post(f"/api/runs/{done.id}/restart", json={"model": SCRIPTED_MODEL})
        assert not_refused.status_code == 422, not_refused.text
        answer: httpx.Response = await client.post(f"/api/runs/{old.id}/restart", json={"model": SCRIPTED_MODEL})
        assert answer.status_code == 201, answer.text
        new_id: str = answer.json()["id"]
        async with asyncio.timeout(_TIME_LIMIT_S):
            new: RunRecord = await manager.wait(new_id)
        events: list[EventRecord] = await runtime.store.list_events(old.id, 0, 1000)
    assert (new.goal, new.thread_id, new.model, new.source) == (GOAL, thread.id, SCRIPTED_MODEL, UI_SOURCE)
    assert (new.status, new.result) == ("done", "Found one; the report is ready."), new
    assert (new.approvals, new.goal_mode, new.speed) == ("yolo", False, "standard")
    assert [event.data for event in events if event.kind == RESTARTED_ON_MODEL_EVENT] == [
        {"model": SCRIPTED_MODEL, "run_id": new_id, "chosen_by": "owner"}
    ]


async def test_a_model_named_for_security_research_takes_such_a_run_and_no_other() -> None:
    async with _daemon() as (_runtime, manager, client):
        chosen: httpx.Response = await client.put(
            "/api/models/kinds", json={"kind": "security", "model": SCRIPTED_MODEL}
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["kinds"] == {"security": SCRIPTED_MODEL}
        kept: dict[str, object] = tomllib.loads(config_path().read_text(encoding="utf-8"))
        security: RunRecord = await manager.start(
            GOAL, "task", None, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
        )
        other: RunRecord = await manager.start(
            "Write a short note about app.example.com's launch.",
            "task",
            None,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            await manager.wait(security.id)
            await manager.wait(other.id)
        cleared: httpx.Response = await client.put("/api/models/kinds", json={"kind": "security", "model": ""})
    assert kept["models"] == {"kinds": {"security": SCRIPTED_MODEL}}
    assert (security.model, other.model) == (SCRIPTED_MODEL, MAIN)
    assert cleared.json()["kinds"] == {}
