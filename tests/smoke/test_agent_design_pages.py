"""An agent's page in a real Chrome, over a real daemon whose model is the scripted one, for the design an agent library
asks of an agent (items 874, 875 and 876): what its runs used and the actions none of them called, taken away
on the owner's yes."""

import asyncio
import re
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
# Whether the page scrolls sideways.
_SIDEWAYS: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


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


async def _open(browser: Browser, client: httpx.AsyncClient, route: str, width: int, phone: bool = False) -> Page:
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce",
        is_mobile=phone,
        has_touch=phone,
    )
    page: Page = await context.new_page()
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#{route}")
    return page


async def _launch(runtime: Runtime) -> tuple[Any, Browser]:
    playwright = await async_playwright().start()
    return playwright, await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)


async def _agent(client: httpx.AsyncClient, name: str, tools: list[str] | None) -> JsonObject:
    made = await client.post(
        "/api/agents",
        json={
            "name": name,
            "role": "Reads the news",
            "instructions": "",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": tools,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


async def _ended_run(runtime: Runtime, agent_id: str, tools: list[str]) -> None:
    """A run of the agent that called ``tools`` and ended, as the store keeps one."""
    store = runtime.store
    thread = await store.create_thread("A run", "ui")
    run: RunRecord = await store.create_run(
        thread.id,
        "goal",
        "task",
        SCRIPTED_MODEL,
        None,
        "ui",
        None,
        "standard",
        None,
        "ask",
        False,
        None,
        agent_id,
        None,
    )
    for tool in tools:
        await store.add_event(run.id, "tool_call", {"call_id": "c", "tool": tool, "args": {}})
    await store.add_run_tokens(run.id, 120_000, 3_000)
    await store.finish_run(run.id, "done", "ok")


async def test_the_page_names_what_the_agents_runs_used_and_takes_away_what_none_called_on_the_owners_yes(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 874: an agent with actions its role never needs is a way to go astray; its page now says which."""
    MODEL.script = ScriptedProvider(_PROVIDER, [])
    scout: JsonObject = await _agent(client, "Scout", ["web_search", "read_file", "shell", "message_agent"])
    playwright, browser = await _launch(runtime)
    try:
        await _ended_run(runtime, scout["id"], ["web_search", "web_search"])
        page: Page = await _open(browser, client, f"/agents/{scout['id']}", 1280)
        used: Locator = page.locator("[data-section='usage']")
        await expect(used).to_contain_text("Its last 1 run: 123k tokens, 1 action called")
        await expect(used).to_contain_text("web_search ×2")
        # One run says little of what a role needs: no action is named as unused yet.
        await expect(used.locator("[data-usage='wait']")).to_be_visible()
        await expect(used.locator("[data-usage='unused']")).to_have_count(0)
        await _ended_run(runtime, scout["id"], ["web_search"])
        await _ended_run(runtime, scout["id"], ["web_search"])
        await page.reload()
        unused: Locator = page.locator("[data-section='usage'] [data-usage='unused']")
        await expect(unused).to_contain_text("Not called in its last 3 runs: 2 actions")
        await expect(unused).to_contain_text("read_file, shell")
        await unused.get_by_role("button", name="Remove them").click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("Remove 2 actions from Scout?")
        await dialog.get_by_role("button", name="Remove").click()
        await expect(page.locator(".toast")).to_contain_text("Scout keeps 2 actions.")
        record = (await client.get(f"/api/agents/{scout['id']}")).json()["record"]
        assert record["tools"] == ["web_search", "message_agent"]
        await expect(page.locator("[data-section='usage'] [data-usage='unused']")).to_have_count(0)
        # On a phone the section fits the screen.
        phone: Page = await _open(browser, client, f"/agents/{scout['id']}", 390, phone=True)
        await expect(phone.locator("[data-section='usage']")).to_be_visible()
        assert await phone.evaluate(_SIDEWAYS) <= 0
    finally:
        await browser.close()
        await playwright.stop()


async def test_an_agent_with_every_action_is_offered_to_keep_only_the_ones_its_runs_called(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    every: JsonObject = await _agent(client, "Generalist", None)
    for _ in range(3):
        await _ended_run(runtime, every["id"], ["read_file", "finish"])
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/agents/{every['id']}", 1280)
        row: Locator = page.locator("[data-section='usage'] [data-usage='every']")
        await expect(row).to_contain_text("It has every action")
        await row.get_by_role("button", name="Keep only these").click()
        await page.get_by_role("dialog").get_by_role("button", name="Keep only these").click()
        await expect(page.locator(".toast")).to_contain_text("Generalist keeps 2 actions.")
        record = (await client.get(f"/api/agents/{every['id']}")).json()["record"]
        assert record["tools"] == ["read_file", "message_agent"]
    finally:
        await browser.close()
        await playwright.stop()


async def test_the_editor_keeps_what_the_agent_never_does_and_the_page_saves_it(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 875: the edges of an agent's job are a field of their own, which its runs hold to whoever asks."""
    agent: JsonObject = await _agent(client, "Prospector", ["web_search"])
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/agents/{agent['id']}", 1280)
        await page.get_by_role("button", name="Edit").click()
        never: Locator = page.get_by_label("Never does")
        await expect(never).to_have_value("")
        await never.fill("Work inbound leads\nSend a message itself")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator(".toast")).to_contain_text("Agent saved.")
        record = (await client.get(f"/api/agents/{agent['id']}")).json()["record"]
        assert record["refuses"] == "Work inbound leads\nSend a message itself"
        # Opened again, the editor shows it as saved.
        await page.reload()
        await page.get_by_role("button", name="Edit").click()
        await expect(page.get_by_label("Never does")).to_have_value("Work inbound leads\nSend a message itself")
        phone: Page = await _open(browser, client, f"/agents/{agent['id']}", 390, phone=True)
        await phone.get_by_role("button", name="Edit").click()
        await expect(phone.get_by_label("Never does")).to_be_visible()
        assert await phone.evaluate(_SIDEWAYS) <= 0
    finally:
        await browser.close()
        await playwright.stop()


async def test_a_template_asks_its_questions_at_use_and_its_agent_starts_with_the_answers(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    """Item 876: a template's first run asked what it needed; now Use asks, three questions at most."""
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, "/agents", 1280)
        await page.get_by_role("group", name="Ready-made agents").get_by_role("button", name="Single agents").click()
        await page.get_by_role("button", name="Use the template Sales Outbound").click()
        dialog: Locator = page.get_by_role("dialog")
        await expect(dialog).to_contain_text("Set up Sales Outbound")
        await expect(dialog.locator("input")).to_have_count(3)
        await dialog.get_by_label("Whom do you sell to (the kind of company, its size, the person)?").fill("Cafés")
        await dialog.get_by_label("Where (countries or cities)?").fill("Oulu")
        await dialog.get_by_role("button", name="Use template").click()
        await expect(page).to_have_url(re_agent())
        agent_id: str = page.url.rpartition("/")[2]
        record = (await client.get(f"/api/agents/{agent_id}")).json()["record"]
        assert record["instructions"].endswith(
            "What the owner told you when making you:\n"
            "- Whom do you sell to (the kind of company, its size, the person)? Cafés\n"
            "- Where (countries or cities)? Oulu"
        )
        # Closing the dialog makes nothing; on a phone it fits the screen.
        phone: Page = await _open(browser, client, "/agents", 390, phone=True)
        await phone.get_by_role("group", name="Ready-made agents").get_by_role("button", name="Single agents").click()
        await phone.get_by_role("button", name="Use the template Talent Scout").click()
        await expect(phone.get_by_role("dialog")).to_contain_text("Set up Talent Scout")
        assert await phone.evaluate(_SIDEWAYS) <= 0
        await phone.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        await expect(phone.get_by_role("dialog")).to_have_count(0)
        names: list[str] = [row["record"]["name"] for row in (await client.get("/api/agents")).json()]
        assert "Talent Scout" not in names
    finally:
        await browser.close()
        await playwright.stop()


def re_agent() -> re.Pattern[str]:
    return re.compile(r"#/agents/agt_[a-z0-9_]+$")
