"""The composer (items 64, 104 and 115): typing ``/`` on the new chat page opens the menu of commands and skills,
letters filter it, the arrows move, Enter picks without sending, Escape closes; Shift+Enter breaks a line. The bar's
pills open the Model and the Options menus; a draft survives a navigation; the bar keeps one line from the desk to the
phone. Wide and on a phone."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, ConsoleMessage, Page, Route, async_playwright, expect

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
# A model that offers reasoning levels, added in the page to the daemon's list (the scripted model offers none), so the
# Reasoning pill has levels to show.
_THINKER: dict[str, object] = {
    "ref": "ollama:thinker",
    "vision": False,
    "tools": True,
    "thinking": True,
    "context_length": 32768,
    "efforts": ["low", "medium", "high"],
    "default_effort": "medium",
}


def _collect_refusals(refused: list[str]) -> Callable[[ConsoleMessage], None]:
    """A console listener that keeps what the page's Content-Security-Policy refused."""

    def listen(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text:
            refused.append(message.text)

    return listen


async def _with_a_thinking_model(route: Route) -> None:
    response = await route.fetch()
    providers: list[dict[str, list[dict[str, object]]]] = await response.json()
    for provider in providers:
        if provider["provider"] == "ollama":
            provider["models"].append(_THINKER)
    await route.fulfill(response=response, json=providers)


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
                await expect(page.locator(".composer-pill[data-pill='mode']")).to_contain_text("Research")
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
                refused: list[str] = []
                page.on("console", _collect_refusals(refused))
                await page.route("**/api/models", _with_a_thinking_model)
                await page.goto(ui_url(host, port, TOKEN))
                goal = page.locator(GOAL)
                popover = page.locator(".composer .composer-popover[aria-label='Model']")
                await expect(popover).to_be_hidden()
                # The Model pill (item 108) wears the mark of its provider, an SVG of the page's own, and the model's name.
                model_pill = page.locator(".composer-pill[data-pill='model']")
                await expect(model_pill).to_contain_text("scripted")
                mark = model_pill.locator(".provider-mark")
                await expect(mark).to_have_attribute("data-provider", "ollama")
                masked: str = await mark.evaluate("(node) => getComputedStyle(node).maskImage")
                assert "icons/providers/ollama.svg" in masked, masked
                served: httpx.Response = await client.get("/icons/providers/ollama.svg")
                assert served.status_code == 200, served.status_code
                assert served.headers["content-type"].startswith("image/svg+xml"), served.headers["content-type"]
                # The scripted model offers no reasoning levels, so the Reasoning pill is away.
                reasoning_pill = page.locator(".composer-pill[data-pill='reasoning']")
                await expect(reasoning_pill).to_be_hidden()
                # The menu: the daemon's default ticked, every model with its mark; a pick closes it and names the model.
                await model_pill.click()
                await expect(popover).to_be_visible()
                await expect(popover.locator("[role=option][aria-selected=true]")).to_contain_text("Default (scripted)")
                await expect(popover.locator("[role=option] .provider-mark").first).to_have_attribute(
                    "data-provider", "ollama"
                )
                await page.screenshot(path=str(tmp_path / f"model-menu-{width}.png"), animations="disabled")
                await popover.get_by_role("option", name="thinker").click()
                await expect(popover).to_be_hidden()
                await expect(model_pill).to_contain_text("thinker")
                # A model with levels brings the Reasoning pill: the daemon's level until one is picked, then the one picked.
                await expect(reasoning_pill).to_contain_text("Medium")
                await expect(reasoning_pill.locator("[data-icon='brain']")).to_have_count(1)
                await reasoning_pill.click()
                levels = page.locator(".composer .composer-popover[aria-label='Reasoning']")
                await expect(levels.locator("[role=option]")).to_have_count(4)
                await expect(levels.locator("[role=option][aria-selected=true]")).to_contain_text("Default (Medium)")
                await page.screenshot(path=str(tmp_path / f"reasoning-menu-{width}.png"), animations="disabled")
                await levels.get_by_role("option", name="High", exact=True).click()
                await expect(levels).to_be_hidden()
                await expect(reasoning_pill).to_contain_text("High")
                # Back to the daemon's default: the Reasoning pill goes again.
                await model_pill.click()
                await popover.get_by_role("option", name="Default (scripted)").click()
                await expect(reasoning_pill).to_be_hidden()
                assert await model_pill.get_attribute("aria-expanded") == "false"
                # The Mode pill: the mode's icon and name; its menu the three modes with what each does.
                mode_pill = page.locator(".composer-pill[data-pill='mode']")
                await expect(mode_pill).to_contain_text("Task")
                await mode_pill.click()
                modes = page.locator(".composer .composer-popover[aria-label='Mode']")
                await expect(modes.locator("[role=option]")).to_have_count(3)
                await modes.get_by_role("option", name="Chat").click()
                await expect(modes).to_be_hidden()
                await expect(mode_pill).to_contain_text("Chat")
                await expect(mode_pill.locator("[data-icon='message']")).to_have_count(1)
                await expect(goal).to_be_focused()
                await mode_pill.click()
                await modes.get_by_role("option", name="Task").click()
                await expect(mode_pill).to_contain_text("Task")
                # The Options pill carries the approvals as its value and opens the approvals, goal mode and keep going.
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await expect(options_pill).to_contain_text("Ask")
                await options_pill.click()
                popover = page.locator(".composer .composer-popover[aria-label='Options']")
                await expect(popover.get_by_role("group", name="Approvals")).to_be_visible()
                await expect(popover.get_by_role("switch", name="Goal mode")).to_be_visible()
                await expect(popover.get_by_role("switch", name="Keep going until done")).to_be_visible()
                await page.screenshot(path=str(tmp_path / f"options-menu-{width}.png"), animations="disabled")
                await popover.get_by_role("button", name="YOLO").click()
                await expect(options_pill).to_contain_text("YOLO")
                await expect(options_pill).to_have_attribute("data-tone", "warn")
                await expect(options_pill.locator("[data-icon='lockOpen']")).to_have_count(1)
                await popover.get_by_role("button", name="Ask").click()
                await expect(options_pill).to_contain_text("Ask")
                await expect(options_pill.locator("[data-icon='lock']")).to_have_count(1)
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
                assert refused == [], refused
                await page.close()
        finally:
            await browser.close()


# The bar's own box, and the middle and the right edge of every control on it that shows (item 115).
_BAR: str = """(() => {
  const bar = document.querySelector('.composer-bar').getBoundingClientRect();
  const parts = [...document.querySelectorAll('.composer-bar > button, .composer-options > :not([hidden])')]
    .filter((node) => node.getClientRects().length > 0)
    .map((node) => {
      const box = node.getBoundingClientRect();
      return [node.dataset.pill ?? node.className, box.top + box.height / 2, box.right];
    });
  return { top: bar.top, bottom: bar.bottom, right: bar.right, parts };
})()"""


async def test_the_bar_keeps_one_line_from_the_desk_to_the_phone(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 115: with every pill out (an agent, a model with reasoning levels, YOLO), the bar is one line 32 px high at
    1280, 800, 560 and 390 px, nothing past its right edge; the mode's name folds to its icon on a short bar, and YOLO
    wears no fill."""
    made: httpx.Response = await client.post(
        "/api/agents",
        json={
            "name": "Scout",
            "role": "Finds and checks sources",
            "instructions": "Prefer primary sources.",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": [],
        },
    )
    assert made.status_code == 201, made.text
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 800, 560, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900})
                await page.route("**/api/models", _with_a_thinking_model)
                await page.goto(ui_url(host, port, TOKEN))
                await expect(page.locator(".composer-pill[data-pill='agent']")).to_be_visible()
                await page.locator(".composer-pill[data-pill='model']").click()
                await (
                    page.locator(".composer-popover[aria-label='Model']").get_by_role("option", name="thinker").click()
                )
                await expect(page.locator(".composer-pill[data-pill='reasoning']")).to_be_visible()
                options_pill = page.locator(".composer-pill[data-pill='options']")
                await options_pill.click()
                await page.locator(".composer-popover[aria-label='Options']").get_by_role("button", name="YOLO").click()
                await page.mouse.click(width - 20, 880)
                await expect(page.locator(".composer-popover[aria-label='Options']")).to_be_hidden()
                await page.mouse.move(0, 0)
                bar: dict[str, float | list[list[str | float]]] = await page.evaluate(_BAR)
                top, bottom, right, parts = bar["top"], bar["bottom"], bar["right"], bar["parts"]
                assert (
                    isinstance(top, float | int) and isinstance(bottom, float | int) and isinstance(right, float | int)
                )
                assert isinstance(parts, list) and len(parts) == 7, (width, bar)
                assert round(bottom - top) == 32, (width, bar)
                middles: list[float] = [float(part[1]) for part in parts]
                assert max(middles) - min(middles) <= 1, (width, bar)
                assert all(float(part[2]) <= right + 0.5 for part in parts), (width, bar)
                mode_name = page.locator(".composer-pill[data-pill='mode'] .composer-pill-value")
                if width <= 560:
                    await expect(mode_name).to_be_hidden()
                else:
                    await expect(mode_name).to_be_visible()
                await expect(options_pill).to_have_attribute("data-tone", "warn")
                fill: str = await options_pill.evaluate("(node) => getComputedStyle(node).backgroundColor")
                assert fill == "rgba(0, 0, 0, 0)", fill
                await page.locator(".composer-stack").screenshot(path=str(tmp_path / f"bar-{width}.png"))
                await page.close()
        finally:
            await browser.close()
