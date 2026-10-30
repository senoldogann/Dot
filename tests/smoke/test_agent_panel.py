"""Who a chat is with, in its side panel (item 846): the Agents tab opens with the chat's agent, its face, name and
role, how it is set and its instructions, with Edit, which changes the agent in the panel; a chat with Dot shows Dot's
own settings, each a link to where it is set. A real server on a scripted model, in Chrome."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "agent-panel-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Two sources.", "success": True}),)
)
_AGENT: dict[str, object] = {
    "name": "Scout",
    "role": "Finds and checks sources",
    "instructions": "Prefer primary sources. Say what you could not confirm.",
    "model": "",
    "effort": None,
    "approvals": "yolo",
    "tools": None,
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH, _FINISH]))
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


async def _finished(manager: RunManager, client: httpx.AsyncClient, body: dict[str, object]) -> str:
    started = await client.post("/api/runs", json=body)
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    return run_id


async def _agents_tab(browser: Browser, client: httpx.AsyncClient, run_id: str) -> tuple[Page, Locator]:
    """The run's page with the side panel on its Agents tab: the page and the tab's panel."""
    port: int | None = client.base_url.port
    assert port is not None
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
    await page.goto(f"http://127.0.0.1:{port}/#/runs/{run_id}")
    panel: Locator = page.locator(".inspector")
    await panel.get_by_role("tab", name="Agents").click()
    return page, panel.locator(".inspector-panel:not([hidden])")


async def test_the_agents_tab_opens_with_the_chats_agent_and_edits_it_in_the_panel(
    served: tuple[RunManager, httpx.AsyncClient],
) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    run_id: str = await _finished(manager, client, {"goal": "Find two sources on the matter", "agent_id": agent_id})
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page, shown = await _agents_tab(browser, client, run_id)
            await expect(shown.locator(".panel-section-title").first).to_have_text("Agent")
            card: Locator = shown.locator(".agent-panel")
            await expect(card.locator(".agent-panel-name")).to_have_text("Scout")
            await expect(card.locator(".agent-panel-role")).to_have_text("Finds and checks sources")
            await expect(card.locator(".avatar")).to_have_count(1)
            facts: Locator = card.locator(".agent-panel-facts")
            await expect(facts.locator("dt")).to_have_text(["Model", "Approvals", "Actions"])
            await expect(facts.locator("dd")).to_have_text(["Default (scripted)", "YOLO", "All"])
            await expect(card.get_by_role("region", name="Instructions")).to_contain_text("Prefer primary sources.")
            await expect(card.get_by_role("link", name="Open its page")).to_have_attribute(
                "href", f"#/agents/{agent_id}"
            )
            # Edit opens the agent's editor in the panel; saved, the panel shows the agent as it is now.
            await card.get_by_role("button", name="Edit").click()
            editor: Locator = card.get_by_role("region", name="Edit agent")
            await expect(editor).to_be_visible()
            role: Locator = editor.get_by_role("textbox", name="Role")
            await expect(role).to_have_value("Finds and checks sources")
            await role.fill("Finds, checks and quotes sources")
            save: Locator = editor.get_by_role("button", name="Save changes")
            await expect(save).to_be_enabled()
            await save.click()
            await expect(card.locator(".agent-panel-role")).to_have_text("Finds, checks and quotes sources")
            await expect(editor).to_have_count(0)
            # The editor fits the panel: nothing of it runs past the panel's edge.
            over: object = await shown.evaluate("(node) => node.scrollWidth - node.clientWidth")
            assert isinstance(over, int) and over <= 1, over
            await page.close()
        finally:
            await browser.close()
    kept = (await client.get(f"/api/agents/{agent_id}")).json()["record"]
    assert kept["role"] == "Finds, checks and quotes sources"
    assert kept["instructions"] == _AGENT["instructions"]


async def test_a_chat_with_dot_shows_dots_settings_each_a_link_to_where_it_is_set(
    served: tuple[RunManager, httpx.AsyncClient],
) -> None:
    manager, client = served
    run_id: str = await _finished(manager, client, {"goal": "Say hello"})
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page, shown = await _agents_tab(browser, client, run_id)
            card: Locator = shown.locator(".agent-panel")
            await expect(card.locator(".agent-panel-name")).to_have_text("Dot")
            facts: Locator = card.locator(".agent-panel-facts")
            await expect(facts.locator("dt")).to_have_text(["Model", "Name and look", "Rules", "Memory"])
            await expect(facts.get_by_role("link")).to_have_count(4)
            hrefs: list[str] = await facts.get_by_role("link").evaluate_all(
                "(links) => links.map((a) => a.getAttribute('href'))"
            )
            assert hrefs == ["#/settings/models", "#/settings/general", "#/settings/rules", "#/settings/memory"], hrefs
            await expect(facts.get_by_role("link").last).to_have_text("0 notes")
            await expect(card.get_by_role("button", name="Edit")).to_have_count(0)
            await page.close()
        finally:
            await browser.close()

