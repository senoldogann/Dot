"""The visual system, end to end (item 50): the controls page draws every control on the tokens, in both looks
(neutral, paper), light and dark, wide and on a phone, comfortable and compact; the screenshots are the record of a
look. The density chosen in Settings › Appearance lands on the page element."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

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

TOKEN: str = "controls-test-token"
_SECTIONS: int = 11


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
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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


async def test_every_control_draws_in_both_looks_wide_and_on_a_phone_at_both_densities(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    shots: tuple[tuple[str, int, Literal["light", "dark"]], ...] = (
        ("neutral", 1280, "light"),
        ("neutral", 1280, "dark"),
        ("neutral", 390, "light"),
        ("paper", 1280, "light"),
        ("paper", 1280, "dark"),
        ("paper", 390, "dark"),
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for theme, width, scheme in shots:
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                await page.goto(ui_url(host, port, TOKEN))
                await page.evaluate(f"localStorage.setItem('dot.theme', '{theme}')")
                await page.goto(f"{base}/#/controls")
                await page.reload()
                await expect(page.locator("html")).to_have_attribute("data-theme", theme)
                await expect(page.locator(".control-section")).to_have_count(_SECTIONS)
                await expect(page.locator("[data-control=faces] .avatar")).to_have_count(5)
                await page.screenshot(path=str(tmp_path / f"controls-{theme}-{width}-{scheme}.png"), full_page=True)
                await page.close()

            page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{base}/#/settings/general")
            await expect(page.locator("html")).to_have_attribute("data-density", "comfortable")
            await page.get_by_role("group", name="Density").get_by_role("button", name="Compact").click()
            await expect(page.locator("html")).to_have_attribute("data-density", "compact")
            await page.goto(f"{base}/#/controls")
            await page.reload()
            await expect(page.locator("html")).to_have_attribute("data-density", "compact")
            await expect(page.locator(".control-section")).to_have_count(_SECTIONS)
            await page.screenshot(path=str(tmp_path / "controls-compact-1280-light.png"), full_page=True)
            await page.close()
        finally:
            await browser.close()
