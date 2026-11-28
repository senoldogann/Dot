"""A chat's own choices for a message that leaves them out (item 2071): the iPhone's follow-up names no model, and a
chat whose settings showed Claude Haiku 5.5 ran three times on the daemon's default model. Each path that starts a run
in such a chat starts it on the chat's model: a queued follow-up (an agent's chat too, as the owner's was), a message
that names its chat, a message whose words a model is named for (``[models] kinds``), and a resume. A model the message
names, the daemon's default (``null``) among them, stays the message's own; a chat whose model's provider has no key
refuses the message plainly and starts nothing. Scripted models only; nothing leaves this Mac."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import token_hex

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import AgentRecord, ChatChoices, RunRecord, ThreadRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

TOKEN: str = "chat-choices-test-token"
# The daemon's default model, which the owner's runs fell back to (DeepSeek on their Mac).
MAIN: str = SCRIPTED_MODEL
# The model the owner picked for the chat.
CHOSEN: str = "anthropic:claude-haiku-5-5"
# A model whose provider has no key on this daemon.
KEYLESS: str = "openai:gpt-5.5"
_STAND_IN_KEY: str = "stand-in-key-of-the-test"
_TIME_LIMIT_S: float = 60.0
_TURNS: int = 4


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _kept(model: str | None) -> ChatChoices:
    return ChatChoices(mode="auto", model=model, effort=None, speed="standard", approvals="yolo", goal_mode=False)


def _config(kinds: dict[str, str]) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    models = config.models.model_copy(update={"main": MAIN, "review": SCRIPTED_MODEL, "kinds": kinds})
    return config.model_copy(update={"models": models})


@asynccontextmanager
async def _daemon(kinds: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    config: AppConfig = _config(kinds)
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {config.providers.anthropic.api_key_env: _STAND_IN_KEY}):
        runtime: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(runtime)
        await runtime.router.register("ollama", ScriptedProvider("ollama", [_finish("On the default.")] * _TURNS))
        await runtime.router.register("anthropic", ScriptedProvider("anthropic", [_finish("On Haiku.")] * _TURNS))
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


async def _chat(
    runtime: Runtime, kept: ChatChoices, agent_id: str | None, status: str
) -> tuple[ThreadRecord, RunRecord]:
    """A chat that keeps ``kept`` and whose run so far (on the default model) ended ``status``."""
    thread: ThreadRecord = await runtime.store.create_thread("Jobs", "ui")
    first: RunRecord = await runtime.store.create_run(
        thread.id, "Find jobs.", "task", MAIN, None, "ui", None, "standard", None, "yolo", False, None, agent_id, None
    )
    await runtime.store.finish_run(first.id, status, "Done." if status == "done" else "It stopped.")
    if agent_id is not None:
        await runtime.store.bind_thread_agent(thread.id, agent_id)
    await runtime.store.set_chat_choices(thread.id, kept)
    return thread, first


async def _newest(runtime: Runtime, manager: RunManager, thread_id: str) -> RunRecord:
    runs: list[RunRecord] = await runtime.store.list_runs(1, thread_id)
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await manager.wait(runs[0].id)


async def test_a_follow_up_that_names_no_choice_starts_on_the_chats_model_in_an_agents_chat_too() -> None:
    async with _daemon({}) as (runtime, manager, client):
        agent: AgentRecord = await runtime.store.create_agent(
            "JobHunter", "Finds jobs", "", "", None, None, (), None, None
        )
        for agent_id in (None, agent.id):
            thread, first = await _chat(runtime, _kept(CHOSEN), agent_id, "done")
            # What the iPhone sends: the text and the key, no choice.
            sent: httpx.Response = await client.post(
                f"/api/threads/{thread.id}/queue",
                json={"text": "Apply to them.", "key": f"phone-follow-up-{token_hex(8)}"},
            )
            assert sent.status_code == 201, sent.text
            run: RunRecord = await _newest(runtime, manager, thread.id)
            assert run.id != first.id
            assert (run.model, run.approvals, run.agent_id, run.status) == (CHOSEN, "yolo", agent_id, "done"), run


async def test_a_message_to_a_chat_keeps_the_chats_model_over_a_kind_and_its_own_model_over_the_chats() -> None:
    # Code work is named a model of its own; a chat that keeps one still starts on it.
    async with _daemon({"code": MAIN}) as (runtime, manager, client):
        thread, _ = await _chat(runtime, _kept(CHOSEN), None, "done")
        goal: str = "Fix the failing tests in the repo."
        named_none: httpx.Response = await client.post("/api/runs", json={"goal": goal, "thread_id": thread.id})
        assert named_none.status_code == 201, named_none.text
        async with asyncio.timeout(_TIME_LIMIT_S):
            on_chats: RunRecord = await manager.wait(named_none.json()["id"])
        assert on_chats.model == CHOSEN, on_chats
        # The web composer names the daemon's default as null: that stays the message's.
        named_default: httpx.Response = await client.post(
            "/api/runs", json={"goal": "Read the notes.", "thread_id": thread.id, "model": None}
        )
        assert named_default.status_code == 201, named_default.text
        async with asyncio.timeout(_TIME_LIMIT_S):
            on_default: RunRecord = await manager.wait(named_default.json()["id"])
        assert on_default.model == MAIN, on_default


async def test_a_resume_starts_on_the_model_the_chat_names_since() -> None:
    async with _daemon({}) as (runtime, manager, client):
        thread, failed = await _chat(runtime, _kept(CHOSEN), None, "failed")
        resumed: httpx.Response = await client.post(f"/api/runs/{failed.id}/resume")
        assert resumed.status_code == 201, resumed.text
        async with asyncio.timeout(_TIME_LIMIT_S):
            run: RunRecord = await manager.wait(resumed.json()["id"])
        assert (run.thread_id, run.model, run.source) == (thread.id, CHOSEN, "resume"), run


async def test_a_chat_whose_models_provider_has_no_key_refuses_the_message_plainly() -> None:
    async with _daemon({}) as (runtime, _manager, client):
        thread, first = await _chat(runtime, _kept(KEYLESS), None, "done")
        sent: httpx.Response = await client.post(
            f"/api/threads/{thread.id}/queue", json={"text": "Apply to them.", "key": "phone-follow-up-keyless-1"}
        )
        assert sent.status_code >= 400, sent.text
        assert f"{KEYLESS} cannot answer this message: no openai key is set" in sent.json()["message"], sent.text
        assert [run.id for run in await runtime.store.list_runs(5, thread.id)] == [first.id]
        assert await runtime.store.list_queued(thread.id) == []
        failed: RunRecord = await runtime.store.create_run(
            thread.id, "Again.", "task", MAIN, None, "ui", None, "standard", None, "yolo", False, None, None, None
        )
        await runtime.store.finish_run(failed.id, "failed", "It stopped.")
        resumed: httpx.Response = await client.post(f"/api/runs/{failed.id}/resume")
        assert resumed.status_code >= 400 and "no openai key is set" in resumed.json()["message"], resumed.text
