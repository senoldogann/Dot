"""The manager review of the team (item 2068) and the bots' load (item 2067) through the daemon's routes, in the real
runtime and store, the manager's model played by the scripted provider: the settings, a review asked for now whose
proposal names what the team has, the owner's Accept, and each bot's load in `GET /api/agents`. No model service is
used and nothing leaves the Mac."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.review import TEAM_REVIEW_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import AgentRecord, ScheduleOptions, ScheduleRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "team-review-token"
PORT: int = 18771
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager


@pytest_asyncio.fixture(loop_scope="module")
async def home() -> AsyncIterator[Home]:
    with scratch_home() as scratch, eval_environment(scratch, {}):
        config: AppConfig = eval_config(load_default_config())
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        try:
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _client(home: Home) -> AsyncIterator[httpx.AsyncClient]:
    context: ServerContext = ServerContext(
        runtime=home.runtime,
        manager=home.manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(context)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


async def test_the_manager_reviews_the_team_and_the_owner_accepts_its_proposal(home: Home) -> None:
    store = home.runtime.store
    bot: AgentRecord = await store.create_agent("Hunter", "Finds jobs", "", "", None, None, (), None, None)
    options: ScheduleOptions = ScheduleOptions(
        model=None,
        effort=None,
        speed=None,
        approvals=None,
        goal_mode=None,
        timezone=None,
        history_runs=None,
        same_chat=None,
        stop_after_failures=None,
        notify=None,
        agent_id=bot.id,
    )
    routine: ScheduleRecord = await store.add_schedule(
        "Apply", "0 9 * * *", None, "Apply to jobs", "task", None, options, None, True
    )
    thread = await store.create_thread("t", "ui")
    for _ in range(3):
        run = await store.create_run(
            thread.id,
            "g",
            "task",
            SCRIPTED_MODEL,
            None,
            "schedule",
            "low",
            "standard",
            None,
            "ask",
            False,
            None,
            bot.id,
            None,
        )
        await store.finish_run(run.id, "failed", "It broke.")
    proposal: dict[str, object] = {
        "finding": 1,
        "kind": "retire_routine",
        "why": "Every run of it failed this week.",
        "orders": [],
        "to_bot": "",
        "bot": "",
        "parts": [],
        "routine": routine.id,
    }
    # The manager's model answers with the proposal, naming the routine by its id.
    turn: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=TEAM_REVIEW_TOOL,
                arguments={"summary": "Hunter fails; its routine wastes runs.", "proposals": [proposal]},
            ),
        ),
    )
    await home.runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [turn]))
    async with _client(home) as client:
        listed = (await client.get("/api/agents")).json()
        hunter = next(entry for entry in listed if entry["record"]["id"] == bot.id)
        assert hunter["load"]["runs"] == 3 and hunter["load"]["done_share"] == 0.0
        assert hunter["load"]["active_routines"] == 1 and hunter["load"]["reasons"] == []

        assert (await client.get("/api/team-review")).json()["every"] == "off"
        assert (
            await client.put("/api/team-review", json={"manager": "agt_nobody", "every": "weekly"})
        ).status_code == 404
        settings = (await client.put("/api/team-review", json={"manager": bot.id, "every": "weekly"})).json()
        assert (settings["manager"], settings["every"]) == (bot.id, "weekly")

        started = await client.post("/api/team-review/run")
        assert started.status_code == 202
        view: dict[str, object] = {}
        for _ in range(200):
            view = (await client.get("/api/team-review")).json()
            if view["review"] is not None and not view["running"]:
                break
            await asyncio.sleep(0.05)
        assert view["error"] == ""
        review = view["review"]
        assert isinstance(review, dict)
        assert [finding["kind"] for finding in review["findings"]] == ["failing"]
        assert review["summary"] == "Hunter fails; its routine wastes runs."
        card = review["proposals"][0]
        assert (card["kind"], card["routine_name"], card["status"]) == ("retire_routine", "Apply", "waiting")
        assert (await store.get_schedule(routine.id)).enabled

        accepted = (await client.post(f"/api/team-review/proposals/{card['id']}/accept")).json()
        assert accepted["review"]["proposals"][0]["status"] == "accepted"
        assert not (await store.get_schedule(routine.id)).enabled
        again = await client.post(f"/api/team-review/proposals/{card['id']}/skip")
        assert again.status_code == 409
