"""On a phone the round button at the top left is the one way back (item 338), in WebKit as the owner's iPhone on a
served daemon with one finished chat of the scripted model:

- a tab opened from the settings' list says Back, and Back returns to the list, where the button opens the chats;
- a service's page opened by its address says Back and leads to Plugins; one opened from a tile returns there;
- a run opened from the home's Today strip says Back and returns to the home;
- a chat opened from the sidebar has the button that opens the list of chats;
- no page draws a link back of its own on a phone; in a Mac's window no Back shows, and a page's own link back shows
  while the sidebar is hidden;
- a follow-up sent in a run opened from the home goes on in the same chat, whose page keeps Back to the home (item 345).

Before, the button opened the list of chats on every page, so Back from a tab, a service or a run left the page it came
from, and a tab had its own "All settings" besides.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "phone-back-token"
# Links the test puts on the home: one to an old address, one inside a part that stops its clicks, in the page.
_LINKS: str = """() => {
  const old = document.createElement("a");
  old.id = "old-address";
  old.href = "#/memory";
  old.textContent = "Memory, at its old address";
  const stopper = document.createElement("span");
  stopper.addEventListener("click", (event) => event.stopPropagation());
  const stopped = document.createElement("a");
  stopped.id = "stopped-link";
  stopped.href = "#/customize/plugins/notion";
  stopped.textContent = "Notion, through a part that stops its clicks";
  stopper.append(stopped);
  document.querySelector("main").prepend(old, stopper);
}"""
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A throw-away home where every model role is the scripted one."""
    home: Path = tmp_path_factory.mktemp("home")
    roles: str = "".join(f'{role} = "{SCRIPTED_MODEL}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[models]\n{roles}\n[memory]\nembed_enabled = false\n\n"
        '[skills]\nelsewhere = false\n\n[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _phone_walk(page: Page, address: str, run: RunRecord) -> None:
    button: Locator = page.locator(".stagebar-left button")
    # Settings, opened from the sidebar: the button opens the chats.
    await page.get_by_role("button", name="Show sidebar").click()
    await page.get_by_role("complementary", name="Sidebar").get_by_role("link", name="Settings").click()
    await expect(page).to_have_url(f"{address}/#/settings")
    await expect(button).to_have_attribute("aria-label", "Show sidebar")
    # A tab from the list: Back, and no link back of its own; Back returns to the list.
    await page.get_by_role("navigation", name="Settings").get_by_role("link", name="Appearance").click()
    await expect(page).to_have_url(f"{address}/#/settings/appearance")
    await expect(button).to_have_attribute("aria-label", "Back")
    await expect(page.locator("main .back")).to_have_count(0)
    await button.click()
    await expect(page).to_have_url(f"{address}/#/settings")
    await expect(button).to_have_attribute("aria-label", "Show sidebar")
    # A service's page opened by its address leads to Plugins; its own link back does not show.
    await page.goto(f"{address}/#/customize/plugins/notion")
    await expect(page.locator(".plugin-hero")).to_be_visible()
    await expect(page.locator("main .back")).to_be_hidden()
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/customize/plugins")
    await expect(button).to_have_attribute("aria-label", "Show sidebar")
    # A service opened from its tile returns to Plugins.
    await page.locator(".plugin-tile[data-plugin='linear'] .plugin-name").click()
    await expect(page).to_have_url(f"{address}/#/customize/plugins/linear")
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/customize/plugins")
    # A run opened from the home's Today strip returns to the home (item 364: the Activity page is gone).
    await page.goto(f"{address}/#/runs")
    await page.locator(f"main a.activity-row[href='#/runs/{run.id}']").first.click()
    await expect(page).to_have_url(f"{address}/#/runs/{run.id}")
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/runs")
    # The browser's Forward to the run, and a reload of it, keep its Back (review B5); as Back it opens no panel and
    # says it controls none (review B8).
    await page.go_forward()
    await expect(page).to_have_url(f"{address}/#/runs/{run.id}")
    await expect(button).to_have_attribute("aria-label", "Back")
    await page.reload()
    await expect(page.locator(".answer").last).to_contain_text("Done.")
    await expect(button).to_have_attribute("aria-label", "Back")
    assert await button.get_attribute("aria-expanded") is None and await button.get_attribute("aria-controls") is None
    await button.click()
    await expect(page).to_have_url(f"{address}/#/runs")
    # A link to an old address counts for the page it leads to (review B3), and so does a link whose clicks a part of
    # the page stops, or one in a dialog outside the page (review B4).
    await page.evaluate(_LINKS)
    await page.locator("#old-address").click()
    await expect(page).to_have_url(f"{address}/#/settings/memory")
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/runs")
    await page.evaluate(_LINKS)
    await page.locator("#stopped-link").click()
    await expect(page).to_have_url(f"{address}/#/customize/plugins/notion")
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/runs")
    # The page above takes the place of the page Back leaves (review B5): the browser's Back after it does not come
    # back to the tab.
    await page.goto(f"{address}/#/settings/appearance")
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/settings")
    await page.go_back()
    await expect(page).to_have_url(f"{address}/#/runs")
    # Customize has one address for its open tab.
    await page.goto(f"{address}/#/customize")
    await expect(page).to_have_url(f"{address}/#/customize/plugins")
    # A chat opened from the sidebar: the button is the way to the list of chats.
    await page.get_by_role("button", name="Show sidebar").click()
    await page.locator(f"#sidebar a[href='#/runs/{run.id}']").first.click()
    await expect(page).to_have_url(f"{address}/#/runs/{run.id}")
    await expect(button).to_have_attribute("aria-label", "Show sidebar")
    await button.click()
    await expect(page.locator(".sidebar")).to_be_in_viewport()


async def _follow_up_walk(page: Page, address: str, run: RunRecord) -> None:
    """A follow-up goes on in the same chat (item 345): before, its run's page was a new entry of the history that no
    link had opened, so the button opened the chats and the page it came from was two steps of the browser's Back
    away."""
    button: Locator = page.locator(".stagebar-left button")
    await page.goto(f"{address}/#/runs")
    await page.locator(f"main a.activity-row[href='#/runs/{run.id}']").first.click()
    await expect(page).to_have_url(f"{address}/#/runs/{run.id}")
    await expect(button).to_have_attribute("aria-label", "Back")
    await page.locator(".dock textarea").fill("And the totals, please")
    await page.locator(".dock .btn.send").click()
    await expect(page).to_have_url(re.compile(rf"#/runs/(?!{run.id}$)[^/]+$"))
    await expect(button).to_have_attribute("aria-label", "Back")
    await button.click()
    await expect(page).to_have_url(f"{address}/#/runs")


async def test_a_phone_has_one_way_back_at_the_top_left(runtime: Runtime, tmp_path: Path) -> None:
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [ScriptedTurn(text="Done.", calls=())] * 4))
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Write the weekly report",
            "chat",
            SCRIPTED_MODEL,
            None,
            "ui",
            None,
            None,
            "standard",
            None,
            "yolo",
            None,
            (),
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        assert ended.status == "done", ended.result
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
        address: str = f"http://127.0.0.1:{port}"
        try:
            async with async_playwright() as playwright:
                webkit: Browser = await playwright.webkit.launch(headless=True)
                chrome: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                try:
                    phone_context: BrowserContext = await webkit.new_context(
                        **playwright.devices["iPhone 17 Pro Max"], reduced_motion="reduce"
                    )
                    await phone_context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    phone: Page = await phone_context.new_page()
                    await phone.goto(ui_url("127.0.0.1", port, TOKEN))
                    await _phone_walk(phone, address, ended)
                    await phone.screenshot(path=str(tmp_path / "back-phone.png"))
                    follow_up: Page = await phone_context.new_page()
                    await _follow_up_walk(follow_up, address, ended)
                    await follow_up.close()
                    # A settings tab opened with the phone on its side, wider than a phone's layout, has its page
                    # above it once the phone stands up (review B5).
                    standing: dict[str, int] = dict(phone.viewport_size or {})
                    await phone.set_viewport_size({"width": standing["height"], "height": standing["width"]})
                    await phone.goto(f"{address}/#/settings/appearance")
                    await expect(phone.locator(".settings-side")).to_be_visible()
                    await expect(phone.get_by_role("button", name="Back")).to_have_count(0)
                    await phone.set_viewport_size({"width": standing["width"], "height": standing["height"]})
                    await expect(phone.locator(".stagebar-left button")).to_have_attribute("aria-label", "Back")
                    await phone.locator(".stagebar-left button").click()
                    await expect(phone).to_have_url(f"{address}/#/settings")
                    # In a Mac's window a page keeps its own link back, and no Back shows.
                    wide: Page = await chrome.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
                    await wide.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await wide.goto(ui_url("127.0.0.1", port, TOKEN))
                    await wide.goto(f"{address}/#/customize/plugins/notion")
                    await expect(wide.locator(".plugin-hero")).to_be_visible()
                    await expect(wide.get_by_role("button", name="Back")).to_have_count(0)
                    # The page's link back shows with the sidebar beside it (item 380), and with the sidebar hidden.
                    await expect(wide.locator("main .back")).to_be_visible()
                    await wide.goto(f"{address}/#/diagnostics")
                    await expect(wide.locator("main .back")).to_have_text("System")
                    await expect(wide.locator("main .back")).to_be_visible()
                    await wide.goto(f"{address}/#/customize/plugins/notion")
                    await wide.get_by_role("button", name="Hide sidebar").click()
                    await expect(wide.locator("main .back")).to_be_visible()
                    await expect(wide.locator(".stagebar-left button")).to_have_attribute("aria-label", "Show sidebar")
                finally:
                    await webkit.close()
                    await chrome.close()
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
