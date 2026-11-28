"""The chat's settings as the phone draws them (the owner's report of 2026-11-27), in the web: on a phone's width the
model's pill opens "Chat settings", whose Options are one card in the phone's order and words: Reasoning (the chosen
model's own levels in full words), Fast mode only for a model that has it, Approvals and Plan first; a desk's model
pill opens the same sheet (item 2015), with the same levels in full words and Fast mode as a switch, only for a model
that has it. At 1440x900 and 390x844, light and dark; the shots go to ``DOT_SHOTS`` when it is set (for the pull
request), else to the test's own folder. No network and no model service is used.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-settings-test-token"
GOAL: str = "textarea[aria-label='Goal']"
# Two models added in the page to the daemon's list: one with every level and fast mode, one with three levels and none.
_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_WORDS: tuple[str, ...] = ("Low", "Medium", "High", "Extra high", "Max")
_THINKER: dict[str, object] = {
    "ref": "ollama:thinker",
    "label": "Thinker",
    "vision": False,
    "tools": True,
    "thinking": True,
    "context_length": 32768,
    "efforts": list(_LEVELS),
    "default_effort": "medium",
    "fast_mode": True,
}
_PLAIN: dict[str, object] = {
    "ref": "ollama:plain",
    "label": "Plain",
    "vision": False,
    "tools": True,
    "thinking": True,
    "context_length": 32768,
    "efforts": ["low", "medium", "high"],
    "default_effort": "medium",
    "fast_mode": False,
}


async def _with_two_models(route: Route) -> None:
    # The daemon's list is read here rather than with `route.fetch`: on GitHub's runners the WebKit sheet found no
    # Thinker to pick, as if the list never came, and `route.fetch` cut another test's request there (ECONNRESET).
    headers: dict[str, str] = await route.request.all_headers()
    async with httpx.AsyncClient(headers={"Authorization": headers["authorization"]}, timeout=30) as client:
        response: httpx.Response = await client.get(route.request.url)
    response.raise_for_status()
    providers: list[dict[str, list[dict[str, object]]]] = response.json()
    for provider in providers:
        if provider["provider"] == "ollama":
            provider["models"].extend([_THINKER, _PLAIN])
    await route.fulfill(json=providers)


def _shots(tmp_path: Path) -> Path:
    folder: Path = Path(os.environ["DOT_SHOTS"]) if "DOT_SHOTS" in os.environ else tmp_path
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("ollama", ScriptedProvider("ollama", []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    manager: RunManager = RunManager(runtime)
    stopping = asyncio.Event()
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
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}):
            yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _open(browser: Browser, address: str, width: int, height: int, scheme: Literal["light", "dark"]) -> Page:
    phone: bool = width <= 720
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height}, color_scheme=scheme, has_touch=phone, is_mobile=phone
    )
    await page.route("**/api/models", _with_two_models)
    port: int = int(address.rpartition(":")[2])
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await expect(page.locator(GOAL)).to_be_visible()
    return page


async def test_the_phone_sheet_has_the_phones_options_in_its_order_and_words(address: str, tmp_path: Path) -> None:
    shots: Path = _shots(tmp_path)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for scheme in ("light", "dark"):
                page: Page = await _open(browser, address, 390, 844, scheme)
                model_pill: Locator = page.locator(".composer-pill[data-pill='model']")
                await model_pill.click()
                sheet: Locator = page.get_by_role("dialog", name="Chat settings")
                await expect(sheet).to_be_visible()
                await sheet.get_by_role("option", name="Thinker").click()
                await page.screenshot(path=str(shots / f"web-chat-settings-390-{scheme}.png"))
                options: Locator = sheet.locator(".sheet-options")
                await expect(
                    options.locator("summary > .sheet-row-text, .sheet-switch .sheet-row-text > span:first-child")
                ).to_have_text(["Reasoning", "Fast mode", "Approvals", "Plan first"])
                # The levels in full words, none cut.
                await options.locator("summary", has_text="Reasoning").click()
                levels: Locator = options.get_by_role("listbox", name="Reasoning").get_by_role("option")
                await expect(levels.locator(".pop-option-text > span:first-child")).to_have_text(
                    ["Default (Medium)", *_WORDS]
                )
                for index in range(await levels.count()):
                    words: Locator = levels.nth(index).locator(".pop-option-text > span").first
                    cut: bool = await words.evaluate("(e) => e.scrollWidth > e.clientWidth + 1")
                    assert not cut, await words.text_content()
                await page.screenshot(path=str(shots / f"web-chat-settings-levels-390-{scheme}.png"))
                await levels.filter(has_text="Extra high").click()
                await expect(options.locator("summary", has_text="Reasoning")).to_contain_text("Extra high")
                # Fast mode is a switch; a model without it has no row for it.
                fast: Locator = options.get_by_role("switch", name="Fast mode")
                await expect(fast).to_have_attribute("aria-checked", "false")
                await fast.click()
                await expect(fast).to_have_attribute("aria-checked", "true")
                await sheet.get_by_role("option", name="Plain").click()
                await expect(
                    options.locator("summary > .sheet-row-text, .sheet-switch .sheet-row-text > span:first-child")
                ).to_have_text(["Reasoning", "Approvals", "Plan first"])
                await expect(options.get_by_role("switch", name="Fast mode")).to_have_count(0)
                await expect(page.get_by_text("This model has no fast mode.")).to_have_count(0)
                await page.close()
        finally:
            await browser.close()


async def test_the_desk_sheet_has_the_same_options_in_full_words_and_fast_mode_only_where_it_is(
    address: str, tmp_path: Path
) -> None:
    shots: Path = _shots(tmp_path)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=True)
        try:
            for scheme in ("light", "dark"):
                page: Page = await _open(browser, address, 1440, 900, scheme)
                # No reasoning pill on a desk either: the model's pill opens the chat's settings (item 2015).
                await expect(page.locator(".composer-pill[data-pill='reasoning']")).to_be_hidden()
                model_pill: Locator = page.locator(".composer-pill[data-pill='model']")
                await model_pill.click()
                sheet: Locator = page.get_by_role("dialog", name="Chat settings")
                await expect(sheet).to_be_visible()
                await sheet.get_by_role("option", name="Thinker").click()
                options: Locator = sheet.locator(".sheet-options")
                rows: Locator = options.locator(
                    "summary > .sheet-row-text, .sheet-switch .sheet-row-text > span:first-child"
                )
                await expect(rows).to_have_text(["Reasoning", "Fast mode", "Approvals", "Plan first"])
                await options.locator("summary", has_text="Reasoning").click()
                levels: Locator = options.get_by_role("listbox", name="Reasoning").get_by_role("option")
                await expect(levels.locator(".pop-option-text > span:first-child")).to_have_text(
                    ["Default (Medium)", *_WORDS]
                )
                await page.screenshot(path=str(shots / f"web-reasoning-1440-{scheme}.png"))
                fast: Locator = options.get_by_role("switch", name="Fast mode")
                await expect(fast).to_have_attribute("aria-checked", "false")
                await fast.click()
                await expect(fast).to_have_attribute("aria-checked", "true")
                await sheet.get_by_role("option", name="Plain").click()
                await expect(rows).to_have_text(["Reasoning", "Approvals", "Plan first"])
                await expect(options.get_by_role("switch", name="Fast mode")).to_have_count(0)
                await page.close()
        finally:
            await browser.close()
