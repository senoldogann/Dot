"""Teams (item 610) through the real daemon and the real loop: a team is a record of the owner's agents, made by Use
team (a second Use keeps the one record and adds the member who was missing), by hand from the agents the owner has,
changed and removed without touching its agents; and a chat with a team works as the team: a message starts a run of
Dot's in a thread that names the team, and every run of that thread, a follow-up too, is told at the head of its prompt
that it leads the team, as the team is when the run starts. No network and no model service: the scripted model
answers."""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.templates import TEAMS, TEMPLATES, AgentTeam
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
ANSWER: ScriptedTurn = ScriptedTurn(text="Handled.", calls=())


class _Recorder:
    """The scripted model, keeping every request it is sent."""

    def __init__(self) -> None:
        self.script: ScriptedProvider = ScriptedProvider(_PROVIDER, [])
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
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
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


async def _agent(client: httpx.AsyncClient, name: str, role: str) -> JsonObject:
    made = await client.post(
        "/api/agents",
        json={
            "name": name,
            "role": role,
            "instructions": f"Secret instructions of {name}.",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": ["shell"],
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


async def _team(client: httpx.AsyncClient, name: str, purpose: str, agents: list[str]) -> JsonObject:
    made = await client.post("/api/teams", json={"name": name, "purpose": purpose, "agents": agents})
    assert made.status_code == 201, made.text
    return made.json()


async def _message(client: httpx.AsyncClient, manager: RunManager, body: JsonObject) -> JsonObject:
    """A message of the owner's, in a chat: the run it starts, once it has ended."""
    MODEL.script = ScriptedProvider(_PROVIDER, [ANSWER])
    started = await client.post("/api/runs", json={"mode": "chat", **body})
    assert started.status_code == 201, started.text
    run: JsonObject = started.json()
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(str(run["id"]))
    return run


def _system(request: ChatRequest) -> str:
    return next(message.content for message in request.messages if message.role == "system")


async def _clear(client: httpx.AsyncClient) -> None:
    """Every team and every agent gone, so that the next test starts as a new home."""
    for team in (await client.get("/api/teams")).json():
        assert (await client.delete(f"/api/teams/{team['record']['id']}")).status_code == 204
    for row in (await client.get("/api/agents")).json():
        if row["record"]["id"] != "dot":
            assert (await client.delete(f"/api/agents/{row['record']['id']}")).status_code == 204


async def test_the_routes_make_list_change_and_remove_a_team_and_a_removed_team_keeps_its_agents(
    client: httpx.AsyncClient,
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes copy")
    assert (await client.get("/api/teams")).json() == []
    team: JsonObject = await _team(client, "Desk", "Writes with sources", [str(writer["id"]), str(scout["id"])])
    assert team["agents"] == [writer["id"], scout["id"]] and team["ready_team"] is None
    assert str(team["id"]).startswith("team_") and team["id"] not in (scout["id"], writer["id"])
    listed: list[JsonObject] = (await client.get("/api/teams")).json()
    assert [row["record"] for row in listed] == [team]
    assert [member["name"] for member in listed[0]["members"]] == ["Writer", "Scout"] and listed[0]["latest"] is None
    detail: JsonObject = (await client.get(f"/api/teams/{team['id']}")).json()
    assert (detail["record"], [member["id"] for member in detail["members"]], detail["chats"]) == (
        team,
        [writer["id"], scout["id"]],
        [],
    )
    # Renamed, said again and given other agents (in the order the pages show them), it is the same team.
    changed = await client.put(
        f"/api/teams/{team['id']}", json={"name": "Newsroom", "purpose": "Checks", "agents": [scout["id"]]}
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["id"], changed.json()["created_at"]) == (team["id"], team["created_at"])
    assert (changed.json()["name"], changed.json()["purpose"], changed.json()["agents"]) == (
        "Newsroom",
        "Checks",
        [scout["id"]],
    )
    refused: list[tuple[str, JsonObject]] = [
        ("an agent that is not there", {"name": "x", "purpose": "", "agents": ["agt_nobody"]}),
        ("Dot", {"name": "x", "purpose": "", "agents": ["dot"]}),
        ("an agent twice", {"name": "x", "purpose": "", "agents": [scout["id"], scout["id"]]}),
        ("no agent", {"name": "x", "purpose": "", "agents": []}),
        ("no name", {"name": "  ", "purpose": "", "agents": [scout["id"]]}),
    ]
    for what, body in refused:
        for answer in (
            await client.post("/api/teams", json=body),
            await client.put(f"/api/teams/{team['id']}", json=body),
        ):
            assert answer.status_code == 422, (what, answer.text)
    assert (await client.get(f"/api/teams/{team['id']}")).json()["record"]["name"] == "Newsroom"
    for answer in (
        await client.get("/api/teams/team_gone"),
        await client.put("/api/teams/team_gone", json={"name": "x", "purpose": "", "agents": [scout["id"]]}),
        await client.delete("/api/teams/team_gone"),
    ):
        assert answer.status_code == 404, answer.text
    # Removing the team removes the record and nothing else.
    assert (await client.delete(f"/api/teams/{team['id']}")).status_code == 204
    assert (await client.get(f"/api/teams/{team['id']}")).status_code == 404
    assert [row["record"]["name"] for row in (await client.get("/api/agents")).json()] == ["Dot", "Scout", "Writer"]
    await _clear(client)


async def test_a_deleted_agent_is_no_longer_in_the_team_that_had_it(client: httpx.AsyncClient) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes copy")
    team: JsonObject = await _team(client, "Desk", "", [str(scout["id"]), str(writer["id"])])
    assert (await client.delete(f"/api/agents/{scout['id']}")).status_code == 204
    detail: JsonObject = (await client.get(f"/api/teams/{team['id']}")).json()
    assert detail["record"]["agents"] == [writer["id"]] and [m["name"] for m in detail["members"]] == ["Writer"]
    await _clear(client)


async def test_use_team_makes_the_record_and_a_second_use_keeps_it_and_brings_back_the_member_who_was_missing(
    client: httpx.AsyncClient,
) -> None:
    team: AgentTeam = next(each for each in TEAMS if each.id == "engineering")
    names: list[str] = [next(each.name for each in TEMPLATES if each.id == member) for member in team.members]
    used: JsonObject = (await client.post(f"/api/agent-teams/{team.id}/use")).json()
    assert [record["name"] for record in used["made"]] == names
    record: JsonObject = used["team"]
    assert (used["created"], used["added"]) == (True, [])
    assert (record["name"], record["purpose"], record["ready_team"]) == (team.name, team.purpose, team.id)
    assert record["agents"] == [made["id"] for made in used["made"]]
    assert len((await client.get("/api/teams")).json()) == 1
    # Used again: the one record, nothing made, nothing added.
    again: JsonObject = (await client.post(f"/api/agent-teams/{team.id}/use")).json()
    assert (again["created"], again["added"], again["made"], again["team"]) == (False, [], [], record)
    assert len((await client.get("/api/teams")).json()) == 1
    # The owner renamed it and took the Reviewer out: the next Use brings the Reviewer back, at the end, and keeps
    # the name the owner gave.
    reviewer: JsonObject = used["made"][1]
    rest: list[str] = [made["id"] for made in used["made"] if made["id"] != reviewer["id"]]
    renamed = await client.put(
        f"/api/teams/{record['id']}", json={"name": "My engineers", "purpose": "Mine", "agents": rest}
    )
    assert renamed.status_code == 200, renamed.text
    back: JsonObject = (await client.post(f"/api/agent-teams/{team.id}/use")).json()
    assert (back["created"], back["added"], back["made"]) == (False, ["Reviewer"], [])
    assert (back["team"]["id"], back["team"]["name"], back["team"]["purpose"]) == (record["id"], "My engineers", "Mine")
    assert back["team"]["agents"] == [*rest, reviewer["id"]]
    assert len((await client.get("/api/teams")).json()) == 1
    # An agent the owner deleted is made again and put in the team.
    assert (await client.delete(f"/api/agents/{reviewer['id']}")).status_code == 204
    assert (await client.get(f"/api/teams/{record['id']}")).json()["record"]["agents"] == rest
    remade: JsonObject = (await client.post(f"/api/agent-teams/{team.id}/use")).json()
    assert [made["name"] for made in remade["made"]] == ["Reviewer"] and remade["added"] == ["Reviewer"]
    assert remade["team"]["agents"] == [*rest, remade["made"][0]["id"]]
    # A team the owner removed is made again from the agents they have.
    assert (await client.delete(f"/api/teams/{record['id']}")).status_code == 204
    last: JsonObject = (await client.post(f"/api/agent-teams/{team.id}/use")).json()
    assert (last["created"], last["made"], last["team"]["name"]) == (True, [], team.name)
    assert last["team"]["id"] != record["id"] and len(last["team"]["agents"]) == len(names)
    await _clear(client)


async def test_a_chat_with_a_team_leads_the_team_in_every_run_of_its_thread_as_the_team_is_when_the_run_starts(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds and checks sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes the copy")
    team: JsonObject = await _team(
        client, "Desk", "Writes a report with sources", [str(scout["id"]), str(writer["id"])]
    )
    # A plain chat of Dot's, for what the team's runs are measured against.
    plain: JsonObject = await _message(client, manager, {"goal": "Say hello"})
    plain_request: ChatRequest = MODEL.requests[-1]
    assert "You lead the team" not in _system(plain_request)

    first: JsonObject = await _message(client, manager, {"goal": "Write the report", "team_id": team["id"]})
    # A run of Dot's, in a thread that names the team; it has Dot's tools and nothing more or less.
    assert first["agent_id"] is None and first["thread_id"] != plain["thread_id"]
    opening: ChatRequest = MODEL.requests[-1]
    head: str = _system(opening)
    assert head.startswith('You lead the team "Desk", the owner\'s agents working together')
    assert "which you follow within the rules below: Writes a report with sources" in head
    assert "  - Scout: Finds and checks sources\n  - Writer: Writes the copy" in head
    assert "with message_agent" in head and "Answer yourself only what no agent's role covers." in head
    assert "Secret instructions" not in head
    assert {spec.name for spec in opening.tools} == {spec.name for spec in plain_request.tools}
    detail: JsonObject = (await client.get(f"/api/runs/{first['id']}")).json()
    assert detail["agent"] is None
    assert detail["team"] == {"id": team["id"], "name": "Desk", "agents": [scout["id"], writer["id"]]}
    chats: dict[str, JsonObject] = {chat["id"]: chat for chat in (await client.get("/api/threads")).json()}
    assert (
        chats[str(first["thread_id"])]["actor_id"] == team["id"]
        and chats[str(first["thread_id"])]["team"] == detail["team"]
    )
    assert chats[str(plain["thread_id"])]["actor_id"] is None and chats[str(plain["thread_id"])]["team"] is None
    listed: JsonObject = (await client.get("/api/teams")).json()[0]
    assert listed["latest"]["id"] == first["thread_id"]
    assert [chat["id"] for chat in (await client.get(f"/api/teams/{team['id']}")).json()["chats"]] == [
        first["thread_id"]
    ]

    # A follow-up, which says nothing of the team, is told it as well.
    await _message(client, manager, {"goal": "Now shorter", "thread_id": first["thread_id"]})
    assert _system(MODEL.requests[-1]).startswith('You lead the team "Desk"')
    assert "  - Scout: Finds and checks sources\n  - Writer: Writes the copy" in _system(MODEL.requests[-1])

    # An agent taken out of the team is no longer named in the next run of the chat.
    changed = await client.put(
        f"/api/teams/{team['id']}",
        json={"name": "Desk", "purpose": "Writes a report with sources", "agents": [scout["id"]]},
    )
    assert changed.status_code == 200, changed.text
    await _message(client, manager, {"goal": "And again", "thread_id": first["thread_id"]})
    after_change: str = _system(MODEL.requests[-1])
    assert "  - Scout: Finds and checks sources" in after_change and "Writer" not in after_change.split("\n\n", 1)[0]
    # An agent deleted is gone from it too, and with none left the team says so and the run does the work itself.
    assert (await client.delete(f"/api/agents/{scout['id']}")).status_code == 204
    await _message(client, manager, {"goal": "Once more", "thread_id": first["thread_id"]})
    emptied: str = _system(MODEL.requests[-1]).split("\n\n", 1)[0]
    assert "The team has no agent now, so there is nobody to hand a part to: do the work yourself." in emptied
    assert "Scout" not in emptied and "with message_agent" not in emptied
    # ... and a new chat with it is refused.
    refused = await client.post("/api/runs", json={"goal": "Anyone there?", "mode": "chat", "team_id": team["id"]})
    assert refused.status_code == 422 and "no agent now" in refused.text, refused.text

    # The team removed: its chat stays, as Dot's, and a message in it is told no team.
    assert (await client.delete(f"/api/teams/{team['id']}")).status_code == 204
    await _message(client, manager, {"goal": "Still here?", "thread_id": first["thread_id"]})
    assert "You lead the team" not in _system(MODEL.requests[-1])
    gone: dict[str, JsonObject] = {chat["id"]: chat for chat in (await client.get("/api/threads")).json()}
    assert gone[str(first["thread_id"])]["actor_id"] is None and gone[str(first["thread_id"])]["team"] is None
    assert (await client.get(f"/api/runs/{first['id']}")).json()["team"] is None
    await _clear(client)


async def test_a_message_names_the_team_alone_and_an_agent_put_in_a_teams_chat_does_not_lead_it(
    manager: RunManager, client: httpx.AsyncClient
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds and checks sources")
    team: JsonObject = await _team(client, "Desk", "", [str(scout["id"])])
    started: JsonObject = await _message(client, manager, {"goal": "Open the chat", "team_id": team["id"]})
    base: JsonObject = {"goal": "x", "team_id": team["id"]}
    for what, extra in (
        ("a thread", {"thread_id": started["thread_id"]}),
        ("an agent", {"agent_id": scout["id"]}),
        ("a campaign", {"standing": True}),
    ):
        answer = await client.post("/api/runs", json={**base, **extra})
        assert answer.status_code == 422, (what, answer.text)
    assert (await client.post("/api/runs", json={"goal": "x", "team_id": "team_gone"})).status_code == 404
    # An agent's own message in the team's chat is that agent's run: it is not told it leads the team.
    await _message(
        client, manager, {"goal": "Scout, a word", "thread_id": started["thread_id"], "agent_id": scout["id"]}
    )
    assert _system(MODEL.requests[-1]).startswith("You are Scout, an agent the owner made")
    assert "You lead the team" not in _system(MODEL.requests[-1])
    await _clear(client)


async def test_the_list_of_agents_names_the_chat_each_agents_newest_run_is_in(
    manager: RunManager, client: httpx.AsyncClient
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    rows: list[JsonObject] = (await client.get("/api/agents")).json()
    assert [(row["record"]["name"], row["latest"], row["latest_title"]) for row in rows] == [
        ("Dot", None, None),
        ("Scout", None, None),
    ]
    run: JsonObject = await _message(
        client, manager, {"goal": "Find two sources on the matter", "agent_id": scout["id"]}
    )
    renamed = await client.patch(f"/api/threads/{run['thread_id']}", json={"title": "Sources for the matter"})
    assert renamed.status_code == 204, renamed.text
    rows = (await client.get("/api/agents")).json()
    assert rows[1]["latest"]["id"] == run["id"] and rows[1]["latest_title"] == "Sources for the matter"
    await _clear(client)
