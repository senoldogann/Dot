"""A chat is the agent's own: the chat a message to an agent was first sent in is bound to that agent, a message the
owner sends in it goes to the agent without the page naming it, and the chat is its home chat though many are newer.
A real server on a scripted model, without a browser."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import NO_PICKS
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "agent-chat-binding-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
_AGENT: dict[str, object] = {
    "name": "Scout",
    "role": "Finds and checks sources",
    "instructions": "Prefer primary sources.",
    "model": "",
    "effort": None,
    "approvals": "yolo",
    "tools": None,
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\nlessons = "off"\nplaybooks = false\n\n'
        '[policy]\napprovals = "yolo"\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * 12))
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as client:
            yield manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _ended(manager: RunManager, run_id: str) -> None:
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)


async def test_a_message_in_an_agents_chat_goes_to_that_agent(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]

    first = await client.post("/api/runs", json={"goal": "Find two sources", "agent_id": agent_id})
    assert first.status_code == 201, first.text
    run = first.json()
    assert run["agent_id"] == agent_id
    await _ended(manager, run["id"])
    thread_id: str = run["thread_id"]

    # The same chat, messaged without naming the agent — as a chat opened from the list or the phone does.
    second = await client.post("/api/runs", json={"goal": "Now one more", "thread_id": thread_id})
    assert second.status_code == 201, second.text
    later = second.json()
    assert later["thread_id"] == thread_id
    assert later["agent_id"] == agent_id, "the chat is the agent's, so its answer is the agent's"
    await _ended(manager, later["id"])

    listed = (await client.get("/api/agents")).json()
    scout = next(view for view in listed if view["record"]["id"] == agent_id)
    assert scout["home"]["id"] == thread_id


async def test_any_run_started_in_the_chat_is_the_agents(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    first = await client.post("/api/runs", json={"goal": "Find two sources", "agent_id": agent_id})
    assert first.status_code == 201, first.text
    run = first.json()
    await _ended(manager, run["id"])
    thread_id: str = run["thread_id"]

    # A message that reaches the chat by another path than a page's composer — a queue that drains, a reaction's
    # follow-up, a background command that ends — is the agent's too, without any of them naming it.
    started = await manager._start_in_chat(
        thread_id,
        "One more, from the queue",
        (),
        "chat",
        None,
        None,
        "standard",
        None,
        None,
        False,
        NO_PICKS,
        None,
        "queue",
    )
    assert started.agent_id == agent_id
    await _ended(manager, started.id)


async def test_a_message_from_the_chat_list_takes_the_agents_bounds(
    served: tuple[RunManager, httpx.AsyncClient],
) -> None:
    manager, client = served
    made = await client.post("/api/agents", json={**_AGENT, "approvals": "ask"})
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    first = await client.post("/api/runs", json={"goal": "Find two sources", "agent_id": agent_id})
    assert first.status_code == 201, first.text
    run = first.json()
    await _ended(manager, run["id"])

    # The same chat, messaged with no approval mode of its own: the agent's ask is the run's, not the configured one.
    later = await client.post("/api/runs", json={"goal": "One more", "thread_id": run["thread_id"]})
    assert later.status_code == 201, later.text
    assert later.json()["approvals"] == "ask"
    await _ended(manager, later.json()["id"])


async def test_a_campaign_started_in_an_agents_chat_is_dots(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    first = await client.post("/api/runs", json={"goal": "Find two sources", "agent_id": agent_id})
    assert first.status_code == 201, first.text
    run = first.json()
    await _ended(manager, run["id"])

    # Keep going, sent from the API in the agent's chat: a campaign is one owner's from its opening session, so that
    # session is Dot's, not the chat's agent's.
    standing = await client.post(
        "/api/runs", json={"goal": "Watch the sources daily", "standing": True, "thread_id": run["thread_id"]}
    )
    assert standing.status_code == 201, standing.text
    opening = standing.json()
    assert opening["agent_id"] is None, "a campaign is Dot's own, whatever chat its opening session is in"
    await _ended(manager, opening["id"])


async def test_dots_chat_stays_dots(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/runs", json={"goal": "A question for Dot"})
    assert made.status_code == 201, made.text
    run = made.json()
    await _ended(manager, run["id"])
    later = await client.post("/api/runs", json={"goal": "And another", "thread_id": run["thread_id"]})
    assert later.status_code == 201, later.text
    assert later.json()["agent_id"] is None
    await _ended(manager, later.json()["id"])
