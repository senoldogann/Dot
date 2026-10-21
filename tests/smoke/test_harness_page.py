"""Settings › Self-improvement (the harness) in a real browser (item 393, its words item 503): with no version the page
says Dot's built-in instructions alone are in use; a version written on the page and put in use shows in use and in
the list; setting it aside leaves the built-in instructions again; on a phone the settings list has the row."""

import asyncio
import re
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

TOKEN: str = "harness-page-token"
HABIT: str = "- Read a file before you change it."


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


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


def _address(client: httpx.AsyncClient) -> tuple[str, int]:
    return "127.0.0.1", int(str(client.base_url).rpartition(":")[2].rstrip("/"))


async def _open(browser: Browser, client: httpx.AsyncClient, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900})
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    host, port = _address(client)
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/harness")
    return page


async def test_a_version_written_on_the_page_is_in_use_until_it_is_set_aside(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await _open(browser, client, 1280)
    try:
        # The page says what it is in plain words (item 503).
        await expect(
            page.get_by_text("Dot learns from its own runs how to work better on this Mac.", exact=False)
        ).to_be_visible()
        await expect(page.get_by_text("Dot's built-in instructions alone: no version is in use.")).to_be_visible()
        await expect(page.get_by_text("No version yet")).to_be_visible()
        await expect(page.get_by_text("Nothing learned yet")).to_be_visible()
        # The form says what goes in it (item 412): that it is optional, what each field takes, with examples.
        write = page.locator("section.memory-panel", has_text="Write your own (optional)")
        await expect(write.locator(".harness-intro")).to_contain_text("Optional: you need not write anything here.")
        await expect(write.locator(".harness-intro")).to_contain_text(
            "Once a time of day is set for the nightly learning"
        )
        assert "For example:" in (await page.get_by_role("textbox", name="Habits").get_attribute("placeholder") or "")
        await write.get_by_role("button", name="Start from an example").click()
        await expect(page.get_by_role("textbox", name="Habits")).to_have_value(re.compile(r"^- Read a file whole"))
        await expect(page.get_by_role("textbox", name="Summary")).not_to_have_value("")
        await page.get_by_role("textbox", name="Habits").fill(HABIT)
        await page.get_by_role("textbox", name="Summary").fill("Read first")
        await page.get_by_role("button", name="Save and use").click()
        in_use = page.locator("section.memory-panel", has_text="What Dot follows now")
        await expect(in_use.get_by_text(HABIT)).to_be_visible()
        await expect(in_use.locator(".badge", has_text="in use")).to_be_visible()
        row = page.locator(".list-row[data-version]")
        await expect(row).to_have_count(1)
        await expect(row.locator(".badge")).to_have_text("in use")
        await expect(row.get_by_text("Read first")).to_be_visible()
        # The API says the same.
        view: dict[str, object] = (await client.get("/api/harness")).json()
        assert view["adopted"] is not None
        # Setting the version in use aside leaves the built-in instructions, after the question is answered.
        await row.get_by_role("button", name="Set aside").click()
        await page.get_by_role("dialog").get_by_role("button", name="Set aside").click()
        await expect(page.get_by_text("Dot's built-in instructions alone: no version is in use.")).to_be_visible()
        await expect(row.locator(".badge")).to_have_text("set aside")
        await row.get_by_role("button", name="Use", exact=True).click()
        await expect(in_use.get_by_text(HABIT)).to_be_visible()
        await expect(page.get_by_text("every night at", exact=False)).to_have_count(0)
        await expect(page.get_by_text("off: set a time of day in the config file", exact=False)).to_be_visible()
    finally:
        await page.close()


async def test_the_phone_lists_self_improvement_among_how_dot_works(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await browser.new_page(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2
    )
    try:
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        host, port = _address(client)
        await page.goto(ui_url(host, port, TOKEN))
        await page.goto(f"http://{host}:{port}/#/settings")
        row = page.get_by_role("link", name="Self-improvement", exact=False)
        await expect(row).to_be_visible()
        await row.click()
        await expect(page.get_by_role("heading", name="Self-improvement", exact=True)).to_be_visible()
        await expect(page.locator("section.memory-panel", has_text="What Dot follows now")).to_be_visible()
    finally:
        await page.close()
