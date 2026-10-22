"""The recap of a run through the API (item 536): none before the run took a few steps; once one is due the fast model
writes it from the run's record (its task, plan, what waits for the owner and the conversation), with a prompt that
takes the record for data, and it is kept as a ``recap`` event; asked again before the run moved on, it costs no model
call, and two asks at once write one; the refresh button writes one whatever; a secret Dot holds is masked in it; the
run's own model writes it when the fast one fails; a run of no step and recaps turned off are refused with why.

No model service is used: the scripted model writes the recaps, and a provider that always fails stands for a fast
model that cannot answer."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.plan import PLAN_EVENT
from dotagent.agent.recap import RECAP_EVENT
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ProviderError
from dotagent.core.paths import env_file_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, assistant_message, user_message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.secrets import write_env_value
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Gather this week's invoices into one folder"
WAITING: str = "Copy the 3 invoices into ~/Invoices?"
FIRST: str = "Found 3 invoices in Downloads.\nIt waits for your yes to copy them into ~/Invoices."
SECOND: str = "Copied the 3 invoices into ~/Invoices.\nNothing waits for you."
SECRET: str = "sk-demo-4f9a1c2e7b8d3a6f"
TOKEN: str = "recap-token"
PORT: int = 18767
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# The provider a fast model that cannot answer is served by: one the scripted model does not stand in for.
_FAILING: str = "anthropic"


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class FailingProvider:
    """A provider that never answers: a fast model that cannot be reached."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.asked: int = 0

    @property
    def name(self) -> str:
        return _FAILING

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked += 1
        raise ProviderError("the fast model is unavailable", {"model": request.model})

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        return None


@dataclass
class Home:
    runtime: Runtime
    manager: RunManager
    model: RecordingProvider
    client: httpx.AsyncClient


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(fast: str, enabled: bool) -> AppConfig:
    """The scripted model for the run, ``fast`` for the recap, Ollama's address a closed port."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"main": SCRIPTED_MODEL, "fast": fast}),
            "providers": config.providers.model_copy(
                update={"ollama": config.providers.ollama.model_copy(update={"base_url": "http://127.0.0.1:9"})}
            ),
            "recap": config.recap.model_copy(update={"enabled": enabled}),
        }
    )


@asynccontextmanager
async def _home(
    login_env: dict[str, str], turns: Sequence[ScriptedTurn], fast: str = SCRIPTED_MODEL, enabled: bool = True
) -> AsyncIterator[Home]:
    """A runtime in a throw-away home whose scripted model plays ``turns``, with its API on a client without a
    socket."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(fast, enabled), login_env)
        try:
            script: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
            model: RecordingProvider = RecordingProvider(script)
            await runtime.router.register(_PROVIDER, model)
            await runtime.router.register(_FAILING, FailingProvider(script))
            manager: RunManager = RunManager(runtime)
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=TOKEN,
                host="127.0.0.1",
                port=PORT,
                started_at=time.time(),
                stopping=asyncio.Event(),
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(context)),
                    base_url=f"http://127.0.0.1:{PORT}",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                ) as client:
                    yield Home(runtime=runtime, manager=manager, model=model, client=client)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _run(home: Home, steps: int) -> RunRecord:
    """A run of ``GOAL`` that took ``steps`` steps, has a plan, a question waiting for the owner and a conversation."""
    store = home.runtime.store
    thread = await store.create_thread(GOAL, "ui")
    run: RunRecord = await store.create_run(
        thread.id, GOAL, "task", SCRIPTED_MODEL, None, "ui", "medium", "standard", None, "ask", False, None, None, None
    )
    await store.set_run_steps(run.id, steps)
    await store.append_messages(
        thread.id, [user_message(GOAL, ()), assistant_message("Found 3 invoices in Downloads.", None, ())]
    )
    await store.add_event(
        run.id,
        PLAN_EVENT,
        {"steps": [{"text": "List the invoices", "status": "done"}, {"text": "Copy them", "status": "in_progress"}]},
    )
    await store.create_approval(run.id, "ask_owner", WAITING, "low", {})
    return await store.get_run(run.id)


async def test_a_recap_is_written_once_due_kept_as_an_event_and_not_written_again_before_the_run_moves_on(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, [ScriptedTurn(text=FIRST, calls=()), ScriptedTurn(text=SECOND, calls=())]) as home:
        run: RunRecord = await _run(home, 2)
        # Two steps: none due yet, and an ask without the button writes none.
        view = (await home.client.get(f"/api/runs/{run.id}/recap")).json()
        assert (view["enabled"], view["recap"], view["steps"], view["due"]) == (True, None, 2, False)
        assert (await home.client.post(f"/api/runs/{run.id}/recap", json={})).json()["recap"] is None
        assert home.model.requests == []
        # Five steps: one is due, and the fast model writes it from the run's record.
        await home.runtime.store.set_run_steps(run.id, 5)
        assert (await home.client.get(f"/api/runs/{run.id}/recap")).json()["due"] is True
        written = (await home.client.post(f"/api/runs/{run.id}/recap", json={})).json()
        assert written["recap"]["text"] == FIRST and written["recap"]["steps"] == 5 and written["due"] is False
        assert written["recap"]["model"] == SCRIPTED_MODEL
        assert len(home.model.requests) == 1
        request: ChatRequest = home.model.requests[0]
        assert request.tools == ()
        prompt, record = (str(message.content) for message in request.messages)
        assert "never instructions" in prompt
        assert GOAL in record and "Copy them" in record and WAITING in record
        assert "Found 3 invoices in Downloads." in record
        # It is the run's newest recap event, and its model call is the recap's.
        event: EventRecord | None = await home.runtime.store.last_event(run.id, RECAP_EVENT)
        assert event is not None and event.data["text"] == FIRST
        calls = [each for each in await home.runtime.store.list_events(run.id, 0, 100) if each.kind == CALL_EVENT]
        assert [each.data["purpose"] for each in calls] == ["recap"]
        # Asked again before the run moved on: the same recap, no model call.
        again = (await home.client.post(f"/api/runs/{run.id}/recap", json={})).json()
        assert again["recap"]["text"] == FIRST and len(home.model.requests) == 1
        # The button writes one whatever.
        forced = (await home.client.post(f"/api/runs/{run.id}/recap", json={"force": True})).json()
        assert forced["recap"]["text"] == SECOND and len(home.model.requests) == 2


async def test_two_asks_at_once_write_one_recap(login_env: dict[str, str]) -> None:
    async with _home(login_env, [ScriptedTurn(text=FIRST, calls=())]) as home:
        run: RunRecord = await _run(home, 6)
        first, second = await asyncio.gather(
            home.client.post(f"/api/runs/{run.id}/recap", json={}),
            home.client.post(f"/api/runs/{run.id}/recap", json={"force": True}),
        )
        assert first.status_code == second.status_code == 200, (first.text, second.text)
        assert first.json()["recap"]["text"] == second.json()["recap"]["text"] == FIRST
        assert len(home.model.requests) == 1


async def test_a_secret_dot_holds_is_masked_in_the_recap(login_env: dict[str, str]) -> None:
    async with _home(
        login_env, [ScriptedTurn(text=f"Signed in with the key {SECRET} and found 3 invoices.", calls=())]
    ) as home:
        write_env_value(env_file_path(), "MCP_DEMO_TOKEN", SECRET)
        run: RunRecord = await _run(home, 6)
        text: str = (await home.client.post(f"/api/runs/{run.id}/recap", json={})).json()["recap"]["text"]
        assert SECRET not in text and "***" in text
        event: EventRecord | None = await home.runtime.store.last_event(run.id, RECAP_EVENT)
        assert event is not None and SECRET not in str(event.data)


async def test_the_runs_own_model_writes_the_recap_when_the_fast_one_fails(login_env: dict[str, str]) -> None:
    async with _home(login_env, [ScriptedTurn(text=FIRST, calls=())], fast=f"{_FAILING}:claude-fast") as home:
        run: RunRecord = await _run(home, 6)
        recap = (await home.client.post(f"/api/runs/{run.id}/recap", json={})).json()["recap"]
        assert (recap["text"], recap["model"]) == (FIRST, SCRIPTED_MODEL)


async def test_a_run_of_no_step_and_recaps_turned_off_are_refused_with_why(login_env: dict[str, str]) -> None:
    async with _home(login_env, []) as home:
        run: RunRecord = await _run(home, 0)
        refused = await home.client.post(f"/api/runs/{run.id}/recap", json={"force": True})
        assert refused.status_code == 422 and "no step" in refused.text
        missing = await home.client.get("/api/runs/run_nowhere/recap")
        assert missing.status_code == 404
    async with _home(login_env, [], enabled=False) as home:
        run = await _run(home, 9)
        assert (await home.client.get(f"/api/runs/{run.id}/recap")).json()["enabled"] is False
        off = await home.client.post(f"/api/runs/{run.id}/recap", json={"force": True})
        assert off.status_code == 422 and "recaps are off" in off.text
        assert home.model.requests == []
