"""The team (item 87) through the real loop and the API: a run of Dot's sends an agent a message with message_agent,
the agent answers in a run of its own (its voice, its tools, the team source), the reply comes back as the tool's
result, the Team page and the agent's page show the exchange, and the delegated row in Dot's chat wears the agent's
face. No network and no model service: the scripted model plays both runs.
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
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.team import MESSAGE_AGENT_TOOL, TEAM_SOURCE

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
