"""The composer (items 64 and 104): typing ``/`` on the new chat page opens the menu of commands and skills, letters
filter it, the arrows move, Enter picks without sending, Escape closes; Shift+Enter breaks a line. The bar's pills
open the Model and the Options menus; a draft survives a navigation. Wide and on a phone."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "composer-test-token"
SKILL: str = """---
name: scan-catalogue
description: "Scan a product catalogue page by page and report the items."
---
# Scan the catalogue

Read every page.
"""
GOAL: str = "textarea[aria-label='Goal']"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    (home / "skills" / "scan-catalogue").mkdir(parents=True)
    (home / "skills" / "scan-catalogue" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def test_the_palette_names_the_skills_and_the_servers(client: httpx.AsyncClient) -> None:
    answer: httpx.Response = await client.get("/api/palette")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "skills": [
            {
                "name": "scan-catalogue",
                "description": "Scan a product catalogue page by page and report the items.",
                "source": "home",
            }
        ],
        "servers": [],
    }


async def test_a_slash_opens_the_menu_letters_filter_it_and_enter_picks_without_sending(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                menu = page.locator(".composer .slash-menu")
                await expect(menu).to_be_hidden()
                await goal.click()
                await goal.type("/")
                await expect(menu).to_be_visible()
                await expect(menu.locator(".slash-group")).to_have_text(["Commands", "Skills"])
                await expect(menu.locator("[role=option]")).to_have_count(4)
                await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("/task")
                assert await goal.get_attribute("aria-expanded") == "true"
                await page.screenshot(path=str(tmp_path / f"slash-open-{width}.png"))
                # Letters filter; the first match is active; the arrow keys move; Enter picks instead of sending.
                await goal.type("sc")
                await expect(menu.locator("[role=option]")).to_have_count(1)
                await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("scan-catalogue")
                await goal.press("Enter")
                await expect(menu).to_be_hidden()
                await expect(goal).to_have_value("use the skill scan-catalogue: ")
                await expect(page).to_have_url(f"http://{host}:{port}/#/runs")
                # A slash inside a word opens nothing; one after a space does, and Escape closes it.
                await goal.type("a/b")
                await expect(menu).to_be_hidden()
                await goal.type(" /")
                await expect(menu).to_be_visible()
                await goal.press("Escape")
                await expect(menu).to_be_hidden()
                # A command acts on the composer: /research picks the research mode and leaves no text behind.
                await goal.fill("")
                await goal.type("/res")
                await expect(menu.locator("[role=option]")).to_have_count(1)
                await goal.press("Tab")
                await expect(goal).to_have_value("")
                await expect(page.get_by_role("group").get_by_role("button", name="Research")).to_have_attribute(
                    "aria-pressed", "true"
                )
                # Shift+Enter breaks a line and sends nothing.
                await goal.type("first")
                await goal.press("Shift+Enter")
                await goal.type("second")
                await expect(goal).to_have_value("first\nsecond")
                await expect(page).to_have_url(f"http://{host}:{port}/#/runs")
                await page.close()
        finally:
            await browser.close()


async def test_the_pills_open_their_menus_and_a_draft_survives_a_navigation(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                popover = page.locator(".composer .composer-popover[aria-label='Model']")
                await expect(popover).to_be_hidden()
                # The Model pill: the daemon's default is the row ticked; picking the scripted model names it on the pill.
                model_pill = page.locator(".composer-pill[data-pill='model']")
                await expect(model_pill).to_contain_text("scripted")
                await model_pill.click()
                await expect(popover).to_be_visible()
                await expect(popover.locator("[role=option][aria-selected=true]")).to_contain_text("Default (scripted)")
                await page.screenshot(path=str(tmp_path / f"model-menu-{width}.png"))
                last = popover.locator("[role=option]").last
                picked: str = await last.inner_text()
                await last.click()
                await expect(popover.locator("[role=option][aria-selected=true]")).to_have_text(picked)
                await page.keyboard.press("Escape")
                await expect(popover).to_be_hidden()
                assert await model_pill.get_attribute("aria-expanded") == "false"
                # The Options pill carries the approvals as its value and opens the approvals, goal mode and keep going.
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await expect(options_pill).to_contain_text("Ask")
                await options_pill.click()
                popover = page.locator(".composer .composer-popover[aria-label='Options']")
                await expect(popover.get_by_role("group", name="Approvals")).to_be_visible()
                await expect(popover.get_by_role("switch", name="Goal mode")).to_be_visible()
                await expect(popover.get_by_role("switch", name="Keep going until done")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"options-menu-{width}.png"))
                await popover.get_by_role("button", name="YOLO").click()
                await expect(options_pill).to_contain_text("YOLO")
                await expect(options_pill).to_have_attribute("data-tone", "warn")
                await popover.get_by_role("button", name="Ask").click()
                await expect(options_pill).to_contain_text("Ask")
                # A click outside (on the empty page under the composer) closes the menu.
                await page.mouse.click(width - 20, 880)
                await expect(popover).to_be_hidden()
                # A draft keeps: the text typed and not sent is there again after a walk to another page.
                await goal.fill("keep me for later")
                await page.goto(f"http://{host}:{port}/#/agents")
                await page.goto(f"http://{host}:{port}/#/runs")
                await expect(page.locator(GOAL)).to_have_value("keep me for later")
                await page.locator(GOAL).fill("")
                await page.goto(f"http://{host}:{port}/#/agents")
                await page.goto(f"http://{host}:{port}/#/runs")
                await expect(page.locator(GOAL)).to_have_value("")
                await page.close()
        finally:
            await browser.close()
