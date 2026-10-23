"""The team (item 87) through the real loop and the API: a run of Dot's sends an agent a message with message_agent,
the agent answers in a run of its own (its voice, its tools, the team source), the reply comes back as the tool's
result, the Team page and the agent's page show the exchange, and the delegated row in Dot's chat wears the agent's
face. An agent answers in the stricter of its own approvals and the sender's (item 135). Dot builds a team of its own
within bounds, and a run in another chat uses it (item 307). No network and no model service: the scripted model plays
every run.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from dotagent.agent.loop import AGENT_CORE_TOOLS
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.campaign import CAMPAIGN_NOTE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.team import CREATE_AGENT_TOOL, MESSAGE_AGENT_TOOL, TEAM_SOURCE

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_SETTLE_MS: int = 700
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"


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
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
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


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _system(request: ChatRequest) -> str:
    return next(message.content for message in request.messages if message.role == "system")


@pytest.mark.browser
async def test_a_run_messages_an_agent_and_reads_its_reply_and_the_team_page_shows_the_exchange(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    made = await client.post(
        "/api/agents",
        json={
            "name": "Scout",
            "role": "Finds and checks sources",
            "instructions": "Prefer primary sources.",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": ["shell"],
        },
    )
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    # Dot's run sends the message, Scout's run answers it, Dot's run ends with the reply: the script plays them in turn.
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(MESSAGE_AGENT_TOOL, {"agent": "scout", "message": "Find two sources on the matter and name them."}),
            _turn(FINISH_TOOL, {"answer": "Two sources: A and B.", "success": True}),
            _turn(FINISH_TOOL, {"answer": "Scout found A and B.", "success": True}),
        ],
    )
    started = await client.post("/api/runs", json={"goal": "Ask the team for two sources", "approvals": "yolo"})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    detail = await client.get(f"/api/runs/{run_id}")
    assert detail.json()["run"]["status"] == "done", detail.text
    children: list[RunRecord] = await runtime.store.list_child_runs(run_id)
    assert len(children) == 1
    child: RunRecord = children[0]
    assert (child.source, child.agent_id, child.status, child.result) == (
        TEAM_SOURCE,
        agent_id,
        "done",
        "Two sources: A and B.",
    )
    # Dot's prompt lists the team and offers the tool; Scout's prompt opens in its voice and offers no messaging.
    first: ChatRequest = MODEL.requests[0]
    assert "# Team" in _system(first) and "  - Scout: Finds and checks sources" in _system(first)
    assert MESSAGE_AGENT_TOOL in {spec.name for spec in first.tools}
    scouts: ChatRequest = MODEL.requests[1]
    assert _system(scouts).startswith("You are Scout, an agent the owner made"), _system(scouts)[:120]
    assert MESSAGE_AGENT_TOOL not in {spec.name for spec in scouts.tools}
    # The reply reached Dot's run as the tool's result.
    last: ChatRequest = MODEL.requests[2]
    tool_results: list[str] = [message.content for message in last.messages if message.role == "tool"]
    assert any(
        text.startswith(f"Scout (run {child.id}) ended as done after 1 steps.\nTwo sources: A and B.")
        for text in tool_results
    ), tool_results
    # The thread of the team, and the agent's page.
    team = await client.get("/api/team")
    assert [
        (m["from_id"], m["to_id"], m["message"], m["reply"], m["run_id"], m["parent_run_id"])
        for m in team.json()["messages"]
    ] == [("dot", agent_id, "Find two sources on the matter and name them.", "Two sources: A and B.", child.id, run_id)]
    page = await client.get(f"/api/agents/{agent_id}")
    assert [m["run_id"] for m in page.json()["messages"]] == [child.id]

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    out: Path = tmp_path / "team"
    await asyncio.to_thread(out.mkdir)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, device_scale_factor=1
            )
            page_ui: Page = await context.new_page()
            await page_ui.goto(ui_url(host, port, TOKEN))
            await page_ui.wait_for_timeout(_SETTLE_MS)
            await page_ui.goto(f"http://{host}:{port}/#/team")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            rows: list[str] = await page_ui.locator(".team-row").all_inner_texts()
            assert len(rows) == 1 and "Dot" in rows[0] and "Scout" in rows[0] and "Two sources: A and B." in rows[0], (
                rows
            )
            # The arrow between the names is the word "to" in text a screen reader reads: a label on a plain span it
            # does not read (item 559).
            assert await page_ui.locator(".team-row .team-arrow").text_content() == "to"
            await page_ui.screenshot(path=str(out / "team.png"))
            await page_ui.goto(f"http://{host}:{port}/#/agents/{agent_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".team-row").count() == 1
            await page_ui.screenshot(path=str(out / "agent.png"))
            # Dot's chat shows the message as a delegated row wearing Scout's face.
            await page_ui.goto(f"http://{host}:{port}/#/runs/{run_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            hues: list[str | None] = [
                await face.get_attribute("data-hue")
                for face in await page_ui.locator(".run-main .avatar[data-hue]").all()
            ]
            assert hues and all(hue is not None for hue in hues), hues
            await page_ui.screenshot(path=str(out / "run.png"))
            await context.close()
        finally:
            await browser.close()


async def test_an_agent_that_runs_yolo_answers_a_run_that_asks_by_asking(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    made = await client.post(
        "/api/agents",
        json={
            "name": "Courier",
            "role": "Runs errands",
            "instructions": "Be brief.",
            "model": "",
            "effort": None,
            "approvals": "yolo",
            "tools": ["shell"],
        },
    )
    assert made.status_code == 201, made.text
    for asked, answered in (("ask", "ask"), ("auto", "auto"), ("yolo", "yolo")):
        MODEL.script = ScriptedProvider(
            _PROVIDER,
            [
                _turn(MESSAGE_AGENT_TOOL, {"agent": "courier", "message": "Say done."}),
                _turn(FINISH_TOOL, {"answer": "done", "success": True}),
                _turn(FINISH_TOOL, {"answer": "Courier said done.", "success": True}),
            ],
        )
        started = await client.post("/api/runs", json={"goal": "Ask Courier to say done", "approvals": asked})
        assert started.status_code == 201, started.text
        run_id: str = started.json()["id"]
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run_id)
        (child,) = await runtime.store.list_child_runs(run_id)
        assert (child.status, child.approvals) == ("done", answered), (asked, child)


def _make(name: str, role: str, tools: list[str]) -> ScriptedCall:
    return ScriptedCall(
        tool=CREATE_AGENT_TOOL,
        arguments={"name": name, "role": role, "instructions": f"You are the team's {role.lower()}.", "tools": tools},
    )


async def _chat(client: httpx.AsyncClient, manager: RunManager, goal: str) -> str:
    """A chat of its own, so that no lesson or skill is drawn at its end (only a task's are) and the script holds; it
    ends with a reply in words, as a chat does, while the agents' runs it starts are tasks that end with finish."""
    started = await client.post("/api/runs", json={"goal": goal, "approvals": "yolo", "mode": "chat"})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    return run_id


async def test_dot_builds_a_team_within_its_bounds_and_a_run_of_another_chat_sends_it_work_by_name(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    """Item 307: a run makes three agents, each with tools of its own (a tool the run lacks and a fourth agent are
    refused), and sends one of them work at once; the Agents list shows the team as made by that run; a run in another
    chat is told of the team and sends another member work by its name."""
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            ScriptedTurn(
                text="",
                calls=(
                    _make("Reader", "Reads the sources", ["read_file"]),
                    _make("Writer", "Writes the summary", ["write_file", "read_file"]),
                    _make("Planner", "Plans the campaign", [CAMPAIGN_NOTE_TOOL]),
                    _make("Checker", "Checks the summary", ["read_file"]),
                    _make("Extra", "Does one thing too many", ["read_file"]),
                ),
            ),
            _turn(MESSAGE_AGENT_TOOL, {"agent": "Reader", "message": "Read notes.txt and say what it holds."}),
            _turn(FINISH_TOOL, {"answer": "It holds the plan.", "success": True}),
            ScriptedTurn(text="The team is set up and Reader read the notes.", calls=()),
        ],
    )
    asked: int = len(MODEL.requests)
    builder: str = await _chat(client, manager, "Summarize the sources with a small team")
    assert (await runtime.store.get_run(builder)).status == "done"
    rows = (await client.get("/api/agents")).json()
    made: dict[str, dict[str, object]] = {
        row["record"]["name"]: row["record"] for row in rows if row["record"]["made_by"] == builder
    }
    assert {name: record["tools"] for name, record in made.items()} == {
        "Reader": ["read_file"],
        "Writer": ["write_file", "read_file"],
        "Checker": ["read_file"],
    }
    # The two refusals came back as the results of their calls.
    results: list[str] = [message.content for message in MODEL.requests[asked + 1].messages if message.role == "tool"]
    assert f"an agent gets only tools this run has, not {CAMPAIGN_NOTE_TOOL}" in results
    assert "a run makes at most 3 agents; send the work to the ones it made: Reader, Writer, Checker" in results
    # Reader answered in a run of its own, in its voice, with Dot's instructions and no tool beyond its own.
    readers: ChatRequest = MODEL.requests[asked + 2]
    assert _system(readers).startswith("You are Reader, an agent Dot made: Reads the sources.")
    assert "Dot's instructions to you" in _system(readers)
    assert {spec.name for spec in readers.tools} <= {"read_file", *AGENT_CORE_TOOLS}
    (child,) = await runtime.store.list_child_runs(builder)
    assert (child.agent_id, child.status, child.result) == (made["Reader"]["id"], "done", "It holds the plan.")

    # A run in another chat is told of the team and sends Writer work by its name.
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(MESSAGE_AGENT_TOOL, {"agent": "writer", "message": "Write a two-line summary of the plan."}),
            _turn(FINISH_TOOL, {"answer": "Line one. Line two.", "success": True}),
            ScriptedTurn(text="Writer wrote the summary.", calls=()),
        ],
    )
    asked = len(MODEL.requests)
    user: str = await _chat(client, manager, "Get the summary written")
    assert (await runtime.store.get_run(user)).thread_id != (await runtime.store.get_run(builder)).thread_id
    system: str = _system(MODEL.requests[asked])
    assert "  - Reader: Reads the sources" in system and "  - Writer: Writes the summary" in system
    (written,) = await runtime.store.list_child_runs(user)
    assert (written.agent_id, written.status, written.result) == (made["Writer"]["id"], "done", "Line one. Line two.")
    for record in made.values():
        assert (await client.delete(f"/api/agents/{record['id']}")).status_code == 204


async def test_an_agent_of_a_ready_team_hands_its_teammate_a_part_from_its_own_chat(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    """Item 512: Competitor Watch's chat delegated the Growth team's work to helpers, as its tools named delegate and
    not message_agent. A team's agents can message one another now, and a member's run is told its team, its
    teammates first: Competitor Watch sends Paid Media a part from its own chat."""
    used = await client.post("/api/agent-teams/growth/use")
    assert used.status_code == 201, used.text
    members: dict[str, str] = {record["name"]: record["id"] for record in used.json()["made"]}
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(MESSAGE_AGENT_TOOL, {"agent": "paid media", "message": "Say how last week's ads did."}),
            _turn(FINISH_TOOL, {"answer": "The ads did well.", "success": True}),
            ScriptedTurn(text="Paid Media says the ads did well.", calls=()),
        ],
    )
    asked: int = len(MODEL.requests)
    started = await client.post(
        "/api/runs",
        json={
            "goal": "How did the ads do?",
            "approvals": "yolo",
            "mode": "chat",
            "agent_id": members["Competitor Watch"],
        },
    )
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    first: ChatRequest = MODEL.requests[asked]
    assert MESSAGE_AGENT_TOOL in {spec.name for spec in first.tools}
    system: str = _system(first)
    assert "- You are in the Growth team: your teammates come first" in system
    listed: list[str] = [line for line in system.splitlines() if line.startswith("  - ")]
    assert listed[:3] == [
        f"  - {name} (Growth team): {role}"
        for name, role in (
            ("Paid Media", "Reads the ad results and says what to change"),
            ("Product Performance", "Watches the product's numbers and reports what moved"),
            ("Sales Outbound", "Finds prospects that fit and drafts a first email to each"),
        )
    ], listed
    (child,) = await runtime.store.list_child_runs(run_id)
    assert (child.source, child.agent_id, child.status, child.result) == (
        TEAM_SOURCE,
        members["Paid Media"],
        "done",
        "The ads did well.",
    )
    for agent_id in members.values():
        assert (await client.delete(f"/api/agents/{agent_id}")).status_code == 204
