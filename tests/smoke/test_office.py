"""The office of the Agents page (the owner's ask of 2026-10-31): Dot and the agents in a 3D room, each with a tag that
says what it does; a click opens a panel with a box to message it; Hide keeps it hidden in this browser; a browser
without WebGL 2 is told so and keeps the lists. A real daemon in Chrome, wide and on a phone."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
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
    mine["latest"] = {
        "id": "run_x",
        "status": "running",
        "goal": "Read the week's sources",
        "thread_id": "t",
        "parent_run_id": None,
        "source": "ui",
    }
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


async def test_a_free_agent_offers_help_and_the_owner_lets_it_help(login_env: dict[str, str]) -> None:
    """Item 896: a free agent the fast model matched with a run at work walks over to offer, both panels say so, and
    "Let it help" asks the daemon to start the helper's run (item 902; test_help_for_real.py plays it whole), sending
    the helper and the run alone, never the model's part."""
    steered: list[dict[str, object]] = []

    async def offers(route: Route) -> None:
        agents = await (await route.fetch(url=route.request.url.replace("/offers", ""))).json()
        mine = [summary["record"] for summary in agents if summary["record"]["id"] != "dot"]
        working, helper = mine[0], next(record for record in mine if record["name"] == "Copy Editor")
        offer = {
            "helper_id": helper["id"],
            "helper_name": helper["name"],
            "helper_role": helper["role"],
            "to_id": working["id"],
            "to_name": working["name"],
            "run_id": "run_x",
            "part": "tighten the brief's wording",
        }
        await route.fulfill(status=200, body=json.dumps([offer]), headers={"content-type": "application/json"})

    async def let_help(route: Route) -> None:
        steered.append(json.loads(route.request.post_data or "{}"))
        # The helper's run, as the daemon starts it (item 902).
        helper_run = {"id": "run_h", "status": "queued", "goal": "Help", "thread_id": "th", "parent_run_id": None}
        await route.fulfill(status=201, body=json.dumps(helper_run), headers={"content-type": "application/json"})

    async with _daemon(login_env) as address, async_playwright() as playwright:
        # The daemon's own answer: with no run at work, nobody is asked and nothing is offered (no model is called).
        async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            answer = await http.get("/api/agents/offers")
            assert answer.status_code == 200 and answer.json() == [], answer.text
            agents = (await http.get("/api/agents")).json()
            helper_id: str = next(summary["record"]["id"] for summary in agents if summary["record"]["id"] != "dot")
            # Let it help: an agent that is not is 404, a run that is not at work 409.
            missing = await http.post("/api/agents/offers/help", json={"helper_id": "agt_none", "run_id": "run_x"})
            assert missing.status_code == 404, missing.text
            idle = await http.post("/api/agents/offers/help", json={"helper_id": helper_id, "run_id": "run_x"})
            assert idle.status_code in (404, 409), idle.text
        browser = await playwright.chromium.launch(args=_ARGS)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.route("**/api/agents", _working)
            await page.route("**/api/agents/offers", offers)
            await page.route("**/api/runs/run_x/events**", _history)
            await page.route("**/api/runs/streams**", _stream)
            await page.route("**/api/agents/offers/help", let_help)
            await _agents_page(page, address)
            office = page.get_by_role("region", name="The office: your agents at work")
            await expect(office.locator("canvas.office-canvas")).to_have_count(1, timeout=20_000)
            # The helper's own panel says whom it offers to help.
            await office.locator(".office-tag", has_text="Copy Editor").click()
            helper_panel = office.get_by_role("complementary", name="Copy Editor in the office")
            await expect(helper_panel.locator(".office-offer")).to_contain_text("Copy Editor → ")
            await helper_panel.get_by_role("button", name="Close").click()
            # The working one's panel lists the offer, and the owner lets it help.
            await office.locator('.office-tag[data-mode="working"]').click()
            offer = office.locator(".office-panel .office-offer")
            await expect(offer).to_contain_text("Copy Editor")
            await expect(offer).to_contain_text("tighten the brief's wording")
            await offer.get_by_role("button", name="Let it help").click()
            await expect(offer.get_by_role("button", name="Helping")).to_be_disabled()
            assert len(steered) == 1 and steered[0]["run_id"] == "run_x" and set(steered[0]) == {"helper_id", "run_id"}
            assert errors == [], errors
        finally:
            await browser.close()


async def _until(done: Callable[[], bool]) -> None:
    """Wait, five seconds at most, until `done` holds."""
    for _ in range(100):
        if done():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("it did not happen in time")


async def test_the_panel_is_a_chat_that_goes_on_in_the_agents_chat(login_env: dict[str, str]) -> None:
    """Item 898, the owner's word of 2026-11-01: every message from the office made a new chat. The panel shows the
    agent's chat; a message goes to the run at work, else on in that chat; "New chat" starts the next one afresh."""
    steered: list[dict[str, object]] = []
    started: list[dict[str, object]] = []
    queued: list[dict[str, object]] = []
    waiting: bool = False

    async def agents(route: Route) -> None:
        response = await route.fetch()
        listed = await response.json()
        mine = [summary for summary in listed if summary["record"]["id"] != "dot"]
        mine[0]["latest"] = {
            "id": "run_x",
            "status": "running",
            "goal": "Read the week's sources",
            "thread_id": "t",
            "parent_run_id": None,
            "source": "ui",
        }
        mine[1]["latest"] = {
            "id": "run_y",
            "status": "blocked" if waiting else "done",
            "goal": "Tighten the brief",
            "thread_id": "t2",
            "parent_run_id": None,
            "source": "ui",
            "result": "Done: two lines shorter.",
        }
        await route.fulfill(status=200, body=json.dumps(listed), headers={"content-type": "application/json"})

    async def turns(route: Route) -> None:
        url: str = route.request.url
        runs = (
            [
                {
                    "id": "run_x",
                    "goal": "Read the week's sources",
                    "result": "",
                    "status": "running",
                    "parent_run_id": None,
                },
                {"id": "run_w", "goal": "Hello", "result": "Hi there", "status": "done", "parent_run_id": None},
            ]
            if "thread_id=t&" in url or url.endswith("thread_id=t")
            else [
                {
                    "id": "run_y",
                    "goal": "Tighten the brief",
                    "result": "Done: two lines shorter.",
                    "status": "done",
                    "parent_run_id": None,
                }
            ]
        )
        await route.fulfill(status=200, body=json.dumps(runs), headers={"content-type": "application/json"})

    async def quiet(route: Route) -> None:
        """No stored events of the waiting run, and nothing new on the stream while the test looks."""
        events: bool = "/events" in route.request.url
        await route.fulfill(
            status=200,
            body="[]" if events else ":\n\n",
            headers={"content-type": "application/json" if events else "text/event-stream"},
        )

    async def queue(route: Route) -> None:
        if route.request.method != "POST":
            await route.fulfill(status=200, body="[]", headers={"content-type": "application/json"})
            return
        queued.append({**json.loads(route.request.post_data or "{}"), "thread": route.request.url.split("/")[-2]})
        await route.fulfill(status=201, body="[]", headers={"content-type": "application/json"})

    async def steer(route: Route) -> None:
        steered.append({**json.loads(route.request.post_data or "{}"), "run": route.request.url.split("/")[-2]})
        await route.fulfill(status=202, body=json.dumps({"id": "run_x"}), headers={"content-type": "application/json"})

    async def start(route: Route) -> None:
        if route.request.method != "POST":
            await route.continue_()
            return
        started.append(json.loads(route.request.post_data or "{}"))
        run = {"id": f"run_n{len(started)}", "status": "queued", "goal": "", "thread_id": "t2", "agent_id": None}
        await route.fulfill(status=201, body=json.dumps(run), headers={"content-type": "application/json"})

    async with _daemon(login_env) as address, async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=_ARGS)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.route("**/api/agents", agents)
            await page.route("**/api/runs?thread_id=*", turns)
            await page.route("**/api/runs/run_x/events**", _history)
            await page.route("**/api/runs/run_y/events**", quiet)
            await page.route("**/api/runs/streams**", quiet)
            await page.route("**/api/runs/*/steer", steer)
            await page.route("**/api/threads/*/queue", queue)
            await page.route("**/api/runs", start)
            await _agents_page(page, address)
            office = page.get_by_role("region", name="The office: your agents at work")
            await expect(office.locator("canvas.office-canvas")).to_have_count(1, timeout=20_000)
            # The working one: its chat, and a message to it waits in that chat's queue, as Settings › General says by
            # default (item 230), not a new chat.
            await office.locator('.office-tag[data-mode="working"]').click()
            panel = office.locator(".office-panel")
            await expect(panel.locator(".office-turn-you")).to_have_text(["Hello", "Read the week's sources"])
            await expect(panel.locator(".office-turn-agent")).to_have_text(["Hi there"])
            await panel.get_by_role("textbox", name="Message").fill("After this, the news.")
            await panel.get_by_role("button", name="Start").click()
            await _until(lambda: len(queued) == 1)
            assert queued[0]["thread"] == "t" and queued[0]["text"] == "After this, the news." and steered == []
            # With Steer chosen, the message reaches the run at work.
            await page.evaluate("localStorage.setItem('dot.busySend', 'steer')")
            await panel.get_by_role("textbox", name="Message").fill("And the news?")
            await panel.get_by_role("button", name="Start").click()
            await _until(lambda: len(steered) == 1)
            assert steered[0]["text"] == "And the news?" and started == []
            # What was sent stands in the chat at once, as the owner's words (the review of item 898).
            await expect(panel.locator(".office-turn-you")).to_have_text(
                ["Hello", "Read the week's sources", "After this, the news.", "And the news?"]
            )
            await panel.get_by_role("button", name="Close").click()
            # A free one whose last chat was the owner's: the message goes on in that chat.
            await office.locator(".office-tag", has_text="Copy Editor").click()
            panel = office.locator(".office-panel")
            await expect(panel.locator(".office-turn-agent")).to_have_text(["Done: two lines shorter."])
            await panel.get_by_role("textbox", name="Message").fill("Now the summary.")
            await panel.get_by_role("button", name="Start").click()
            await _until(lambda: len(started) == 1)
            assert started[0]["thread_id"] == "t2" and started[0]["goal"] == "Now the summary."
            # "New chat": the next message starts afresh.
            await panel.get_by_role("button", name="New chat").click()
            await expect(panel.locator(".office-chat-note")).to_have_text("Your next message starts a new chat.")
            await panel.get_by_role("textbox", name="Message").fill("Something else.")
            await panel.get_by_role("button", name="Start").click()
            await _until(lambda: len(started) == 2)
            assert started[1]["thread_id"] is None
            await panel.get_by_role("button", name="Close").click()
            # One that waits for the owner (blocked) is at work too: the message reaches that run (the review).
            waiting = True
            await page.goto(f"{address}/#/settings")
            await page.goto(f"{address}/#/agents")
            office = page.get_by_role("region", name="The office: your agents at work")
            await expect(office.locator(".office-tag", has_text="Copy Editor")).to_have_count(1, timeout=20_000)
            await office.locator(".office-tag", has_text="Copy Editor").click()
            panel = office.locator(".office-panel")
            await panel.get_by_role("textbox", name="Message").fill("Yes, go on.")
            await panel.get_by_role("button", name="Start").click()
            await _until(lambda: len(steered) == 2)
            assert steered[1]["run"] == "run_y" and len(started) == 2
            assert errors == [], errors
        finally:
            await browser.close()


async def test_a_free_agents_panel_is_its_chat_and_the_office_goes_full_screen(login_env: dict[str, str]) -> None:
    """Item 901, the owner's word of 2026-11-01: a free agent's panel showed its last run's steps and long answer, and
    the office stood in a small square with the whole window free. A free agent with no chat of the owner's shows an
    empty chat; the office goes full screen and back, Escape too."""
    async with _daemon(login_env) as address, async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=_ARGS)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await _agents_page(page, address)
            office = page.get_by_role("region", name="The office: your agents at work")
            await expect(office.locator("canvas.office-canvas")).to_have_count(1, timeout=20_000)
            await office.locator(".office-tag", has_text="Copy Editor").click()
            panel = office.locator(".office-panel")
            await expect(panel.locator(".office-chat-note")).to_have_text("No chat yet. Write to start one.")
            await expect(panel.locator(".office-step")).to_have_count(0)
            # Full screen: the window's size, and back with the button or Escape.
            full = office.get_by_role("button", name="Full screen")
            await full.click()
            box = await office.bounding_box()
            assert box is not None and box["width"] == 1280 and box["height"] == 860, box
            # What its panel opens stands over it: the composer's menu (the pre-push review).
            await panel.locator(".plus-button").first.click()
            await expect(page.locator(".menu").first).to_be_visible()
            menu_z: str = await page.locator(".menu").first.evaluate("(node) => getComputedStyle(node).zIndex")
            office_z: str = await office.evaluate("(node) => getComputedStyle(node).zIndex")
            assert int(menu_z) > int(office_z), (menu_z, office_z)
            await page.keyboard.press("Escape")
            await office.get_by_role("button", name="Leave full screen").click()
            box = await office.bounding_box()
            assert box is not None and box["width"] < 1280, box
            await full.click()
            # An Escape that closes a dialog (a file, a question) closes the dialog alone (the pre-push review).
            opening: str = (
                "() => { const d = document.createElement('dialog'); d.id = 't-dialog';"
                " document.body.append(d); d.showModal(); }"
            )
            await page.evaluate(opening)
            await page.keyboard.press("Escape")
            await expect(page.locator("#t-dialog")).not_to_have_attribute("open", "")
            await expect(office.get_by_role("button", name="Leave full screen")).to_have_attribute(
                "aria-pressed", "true"
            )
            await page.keyboard.press("Escape")
            await expect(office.get_by_role("button", name="Full screen")).to_have_attribute("aria-pressed", "false")
            assert errors == [], errors
        finally:
            await browser.close()
