"""The office of the Agents page (the owner's ask of 2026-10-31): Dot and the agents in a 3D room, each with a tag that
says what it does; a click opens a panel with a box to message it; Hide keeps it hidden in this browser; a browser
without WebGL 2 is told so and keeps the lists. A real daemon in Chrome, wide and on a phone."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "office-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TEMPLATES: tuple[str, ...] = ("research-desk", "copy-editor")
# WebGL in a headless Chrome without a GPU.
_ARGS: list[str] = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[str]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
            manager: RunManager = RunManager(runtime)
            stopping: asyncio.Event = asyncio.Event()
            listener = bind_listener("127.0.0.1", 0)
            port: int = listener.getsockname()[1]
            context = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=TOKEN,
                host="127.0.0.1",
                port=port,
                started_at=time.time(),
                stopping=stopping,
            )
            server = DotServer(serving_config(create_app(context)), stopping)
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
            try:
                address: str = f"http://127.0.0.1:{port}"
                async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
                    for template in _TEMPLATES:
                        made = await http.post(f"/api/agent-templates/{template}/use")
                        assert made.status_code == 201, made.text
                yield address
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _working(route: Route) -> None:
    """The first of the owner's agents works (Dot comes first in the list): its newest run is running."""
    response = await route.fetch()
    agents = await response.json()
    mine = next(summary for summary in agents if summary["record"]["id"] != "dot")
    mine["latest"] = {"id": "run_x", "status": "running", "goal": "Read the week's sources", "thread_id": "t"}
    await route.fulfill(status=200, body=json.dumps(agents), headers={"content-type": "application/json"})


async def _history(route: Route) -> None:
    """The working run's stored events: a search it made, which went through (item 889)."""
    events = [
        {
            "id": 1,
            "run_id": "run_x",
            "kind": "tool_call",
            "ts": 1.0,
            "data": {"tool": "web_search", "args": {"query": "news"}},
        },
        {"id": 2, "run_id": "run_x", "kind": "tool_result", "ts": 2.0, "data": {"tool": "web_search", "ok": True}},
    ]
    await route.fulfill(status=200, body=json.dumps(events), headers={"content-type": "application/json"})


async def _stream(route: Route) -> None:
    """The one stream of the followed runs: nothing new while the test looks."""
    # Never from the start: the history was read first (and no closed office opens one).
    assert "ids=run_x" in route.request.url and "after=2" in route.request.url, route.request.url
    await route.fulfill(status=200, body=":\n\n", headers={"content-type": "text/event-stream"})


async def _agents_page(page: Page, address: str) -> None:
    port: int = int(address.rpartition(":")[2])
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/agents")


async def test_the_office_shows_every_agent_and_dot_and_a_click_opens_the_box_to_message_one(
    login_env: dict[str, str],
) -> None:
    async with _daemon(login_env) as address, async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=_ARGS)
        try:
            for width, phone in ((1280, False), (390, True)):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 860}, has_touch=phone, is_mobile=phone, reduced_motion="reduce"
                )
                errors: list[str] = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                await page.route("**/api/agents", _working)
                await page.route("**/api/runs/run_x/events**", _history)
                await page.route("**/api/runs/streams**", _stream)
                await _agents_page(page, address)
                office = page.get_by_role("region", name="The office: your agents at work")
                await expect(office.locator("canvas.office-canvas")).to_have_count(1, timeout=20_000)
                tags = office.locator(".office-tag")
                await expect(tags).to_have_count(1 + len(_TEMPLATES), timeout=20_000)
                await expect(tags.filter(has_text="Dot")).to_have_count(1)
                await expect(office.locator('.office-tag[data-mode="working"]')).to_have_count(1)
                await expect(office.locator('.office-tag[data-mode="working"]')).to_contain_text("Working")
                # What it does this moment, over its head (item 889).
                await expect(office.locator(".office-bubble:visible")).to_have_text("Web search · news")
                # The room is baked: a few hundred draw calls, not one per piece of furniture.
                await expect(office).to_have_attribute("data-draw-calls", re.compile(r"^\d+$"))
                calls: int = int(await office.get_attribute("data-draw-calls") or "0")
                assert 0 < calls < 300, calls
                free = tags.filter(has_text="Copy Editor")
                if phone:
                    # A phone's room is square and its free agents' tags are their names alone (item 894: seven full
                    # tags hid the room on the owner's phone); a finger's height still.
                    frame = await office.bounding_box()
                    assert frame is not None and abs(frame["width"] - frame["height"]) <= 2, frame
                    await expect(free.locator(".office-tag-dot")).to_be_hidden()
                    box = await free.bounding_box()
                    assert box is not None and box["height"] >= 32, box
                await free.click()
                panel = office.get_by_role("complementary", name="Copy Editor in the office")
                await expect(panel).to_be_visible()
                await expect(panel.get_by_role("textbox", name="Message")).to_be_visible()
                reset = office.get_by_role("button", name=re.compile("whole room", re.I))
                if phone:
                    # The panel takes the frame's foot: the button that brings the room back is not over its box.
                    await expect(reset).to_be_hidden()
                    await expect(free.locator(".office-tag-dot")).to_be_visible()
                await panel.get_by_role("button", name="Close").click()
                await expect(reset).to_be_visible()
                # The working one's panel shows its steps as they come.
                await office.locator('.office-tag[data-mode="working"]').click()
                await expect(office.locator(".office-panel .office-step")).to_have_text(["Web search · news"])
                await expect(office.locator('.office-panel .office-step[data-state="ok"]')).to_have_count(1)
                await office.get_by_role("button", name="Close").click()
                await tags.filter(has_text="Copy Editor").click()
                await expect(panel).to_be_visible()
                await panel.get_by_role("button", name="Close").click()
                await expect(panel).to_have_count(0)
                # The lists stay under the office.
                await expect(page.get_by_role("region", name="Your agents", exact=True)).to_be_visible()
                # Away and back: the office is drawn again, its old room and context given back.
                for _ in range(3):
                    await page.goto(f"{address}/#/settings")
                    await page.goto(f"{address}/#/agents")
                    await expect(page.locator(".office-tag")).to_have_count(1 + len(_TEMPLATES), timeout=20_000)
                await expect(page.locator("canvas.office-canvas")).to_have_count(1)
                assert errors == [], errors
                await page.close()
        finally:
            await browser.close()


async def test_hide_keeps_the_office_hidden_and_a_browser_without_webgl_is_told_so(login_env: dict[str, str]) -> None:
    async with _daemon(login_env) as address, async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=_ARGS)
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
            page: Page = await context.new_page()
            await _agents_page(page, address)
            part = page.get_by_role("region", name="The office")
            await expect(part.locator(".office-tag")).to_have_count(1 + len(_TEMPLATES), timeout=20_000)
            await part.get_by_role("button", name="Hide").click()
            await expect(part.locator(".office-holder")).to_be_hidden()
            await page.reload()
            await expect(
                page.get_by_role("region", name="The office").get_by_role("button", name="Show")
            ).to_be_visible()
            await expect(page.locator("canvas.office-canvas")).to_have_count(0)
            await context.close()
            blind = await browser.new_context(viewport={"width": 1280, "height": 860})
            await blind.add_init_script(
                "const own = HTMLCanvasElement.prototype.getContext;"
                "HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {"
                " return kind === 'webgl2' ? null : own.call(this, kind, ...rest); };"
            )
            page = await blind.new_page()
            await _agents_page(page, address)
            await expect(
                page.get_by_text("This browser draws no 3D, so the office cannot be shown here.")
            ).to_be_visible()
            await expect(page.get_by_role("region", name="Your agents", exact=True)).to_be_visible()
            await blind.close()
        finally:
            await browser.close()


async def test_one_stream_carries_several_runs_events_in_their_order(login_env: dict[str, str]) -> None:
    """Item 889: the office follows every working agent on one stream (a browser keeps six connections to a server).
    Runs that are over give their stored events, in the store's order, and the stream ends; an unknown run is 404 and
    more than the stream follows is 422."""
    async with (
        _daemon(login_env) as address,
        httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
    ):
        agents = (await http.get("/api/agents")).json()
        assert len(agents) >= 2
        made: list[str] = []
        for goal in ("First", "Second"):
            started = await http.post("/api/runs", json={"goal": goal, "mode": "chat"})
            assert started.status_code == 201, started.text
            made.append(started.json()["id"])
        for run_id in made:
            for _ in range(100):
                if (await http.get(f"/api/runs/{run_id}")).json()["run"]["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.1)
        answer = await http.get(f"/api/runs/streams?ids={','.join(made)}&after=0", timeout=20)
        assert answer.status_code == 200, answer.text
        frames = [json.loads(line[6:]) for line in answer.text.splitlines() if line.startswith("data: ")]
        assert {frame["run_id"] for frame in frames} == set(made)
        assert [frame["id"] for frame in frames] == sorted(frame["id"] for frame in frames)
        assert [frame["kind"] for frame in frames if frame["kind"] == "run_finished"] == ["run_finished"] * 2
        missing = await http.get("/api/runs/streams?ids=run_nobody")
        assert missing.status_code == 404, missing.text
        many = await http.get(f"/api/runs/streams?ids={','.join(f'run_{n}' for n in range(13))}")
        assert many.status_code == 422, many.text
