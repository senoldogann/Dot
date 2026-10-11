"""Agents (item 86) through the API and the real loop: an agent is made, a run is started for it on the scripted
model, the run wears the agent (its id, its prompt, its bounds), the Agents page and the agent's page show it, and the
agent's end leaves its chats to Dot. No network and no model service: the scripted model answers.
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
from dotagent.tools.meta import FINISH_TOOL

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


def _agent(tools: list[str]) -> dict[str, object]:
    return {
        "name": "Scout",
        "role": "Finds and checks sources",
        "instructions": "Prefer primary sources. Say what you could not confirm.",
        "model": "",
        "effort": None,
        "approvals": "yolo",
        "tools": tools,
    }


async def test_an_agent_is_made_runs_in_its_own_voice_and_bounds_and_leaves_its_chats_to_dot(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    refused = await client.post("/api/agents", json=_agent(["no_such_tool"]))
    assert refused.status_code == 422, refused.text
    made = await client.post("/api/agents", json=_agent(["shell"]))
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    listed = await client.get("/api/agents")
    assert [row["record"]["id"] for row in listed.json()] == ["dot", agent_id]
    assert listed.json()[1]["latest"] is None
    for forbidden in (client.put("/api/agents/dot", json=_agent([])), client.delete("/api/agents/dot")):
        answer = await forbidden
        assert answer.status_code == 422, answer.text

    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Two sources.", "success": True}),)
            )
        ],
    )
    started = await client.post("/api/runs", json={"goal": "Find two sources on the matter", "agent_id": agent_id})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    assert started.json()["agent_id"] == agent_id
    # The agent's approvals bound the run; its model is the configured one, since the agent names none.
    assert started.json()["approvals"] == "yolo"
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    detail = await client.get(f"/api/runs/{run_id}")
    assert detail.json()["run"]["status"] == "done", detail.text
    assert detail.json()["agent"]["name"] == "Scout"
    # The prompt opens in the agent's voice; the tools offered are the one it names and the core ones.
    request: ChatRequest = MODEL.requests[-1]
    system: str = next(message.content for message in request.messages if message.role == "system")
    assert system.startswith("You are Scout, an agent the owner made: Finds and checks sources."), system[:200]
    assert "Prefer primary sources." in system
    offered: set[str] = {spec.name for spec in request.tools}
    assert "shell" in offered and FINISH_TOOL in offered
    assert "fetch" not in offered, offered
    page = await client.get(f"/api/agents/{agent_id}")
    assert [run["id"] for run in page.json()["runs"]] == [run_id]
    # The run's row on the Activity page wears the agent's face.
    activity = await client.get("/api/activity")
    recent = [row for row in activity.json()["recent"] if row["id"] == run_id]
    assert [row["actor_id"] for row in recent] == [agent_id], activity.text

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    out: Path = tmp_path / "agents"
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
            # The composer offers the agent, and the Agents page lists Dot and the agent with its newest chat.
            await page_ui.goto(f"http://{host}:{port}/#/runs")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            options: list[str] = await page_ui.locator("select[aria-label='Agent'] option").all_inner_texts()
            assert options == ["Dot", "Scout"], options
            await page_ui.goto(f"http://{host}:{port}/#/agents")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            rows_text: list[str] = await page_ui.locator(".agent-row").all_inner_texts()
            assert len(rows_text) == 2 and "Scout" in rows_text[1] and "Find two sources" in rows_text[1], rows_text
            await page_ui.screenshot(path=str(out / "agents.png"))
            await page_ui.goto(f"http://{host}:{port}/#/agents/{agent_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".agent-who .home-name").inner_text() == "Scout"
            assert (
                await page_ui.locator("textarea[aria-label='Message']").get_attribute("placeholder") == "Message Scout"
            )
            assert await page_ui.locator(".list-row[data-run]").count() == 1
            await page_ui.screenshot(path=str(out / "agent.png"))
            # The run's page wears the agent's face and name in the bar.
            await page_ui.goto(f"http://{host}:{port}/#/runs/{run_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".stage-title .stage-name").inner_text() == "Scout"
            assert await page_ui.locator(".stage-title .avatar[data-hue]").count() == 1
            await page_ui.screenshot(path=str(out / "run.png"))
            await context.close()
        finally:
            await browser.close()

    gone = await client.delete(f"/api/agents/{agent_id}")
    assert gone.status_code == 204, gone.text
    assert (await client.get(f"/api/runs/{run_id}")).json()["run"]["agent_id"] is None
    assert (await client.get(f"/api/agents/{agent_id}")).status_code == 404
