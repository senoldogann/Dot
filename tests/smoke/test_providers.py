"""Settings › Providers (item 373): the providers down the left side with their own marks and the state of their keys,
the one chosen beside them with its key, its models and the judge's switch; on a phone the list, then the page of the
one tapped with the way back. A real server, wide and dark, and on a phone light."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "providers-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    manager: RunManager = RunManager(runtime)
    try:
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/settings/keys")
    await page.wait_for_selector(".providers-layout", timeout=15000)
    await page.wait_for_timeout(400)
    return page, errors


async def test_the_providers_are_a_column_of_marks_and_a_page_of_the_one_chosen(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            wide, wide_errors = await _page(browser, served, 1280, "dark")
            rows = wide.locator(".provider-item")
            assert await rows.count() >= 6
            names = [
                await rows.nth(index).locator(".provider-name").text_content() for index in range(await rows.count())
            ]
            assert names[:3] == ["OpenAI", "Anthropic", "OpenCode Go"]
            marks = await wide.evaluate(
                """() => [...document.querySelectorAll('.provider-item .provider-mark')]
                     .map((mark) => getComputedStyle(mark).maskImage)
                     .filter((image) => image === 'none').length"""
            )
            assert marks == 0, "every provider keeps its own mark"
            assert await wide.locator(".provider-panel").count() == 1
            assert (await wide.locator(".provider-title").text_content()) == "OpenAI"
            assert await wide.locator(".provider-section-title", has_text="Key").count() == 1
            assert await wide.locator(".provider-section-title", has_text="Models").count() == 1
            sideways = await wide.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert sideways <= 0
            await wide.screenshot(path=str(tmp_path / "wide-dark.png"))

            # The list is a group of buttons, not a listbox whose arrow keys would have to move through it, and the
            # provider open is marked as the current one (item 559).
            assert await wide.locator(".providers-list").get_attribute("role") == "group"
            assert await rows.locator("xpath=self::*[@role]").count() == 0
            assert await rows.first.get_attribute("aria-current") == "true"
            await rows.filter(has_text="Anthropic").click()
            assert (await wide.locator(".provider-title").text_content()) == "Anthropic"
            assert await rows.filter(has_text="Anthropic").get_attribute("aria-current") == "true"
            assert await wide.locator(".provider-item[aria-current]").count() == 1
            await wide.screenshot(path=str(tmp_path / "wide-dark-anthropic.png"))
            assert wide_errors == []
            await wide.context.close()

            light, light_errors = await _page(browser, served, 1280, "light")
            await light.screenshot(path=str(tmp_path / "wide-light.png"))
            assert light_errors == []
            await light.context.close()

            phone, phone_errors = await _page(browser, served, 390, "light")
            layout = phone.locator(".providers-layout")
            assert await layout.get_attribute("data-open") == "false"
            assert not await phone.locator(".provider-panel").is_visible()
            await phone.screenshot(path=str(tmp_path / "phone-list.png"))
            await phone.locator(".provider-item").filter(has_text="TypeSafe").click()
            await phone.wait_for_timeout(300)
            assert await layout.get_attribute("data-open") == "true"
            assert (await phone.locator(".provider-title").text_content()) == "TypeSafe"
            sideways = await phone.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert sideways <= 0
            await phone.screenshot(path=str(tmp_path / "phone-detail.png"))
            await phone.locator(".provider-back").click()
            await phone.wait_for_timeout(300)
            assert await layout.get_attribute("data-open") == "false"
            assert await phone.locator(".provider-panel").is_visible() is False
            # Back gives the focus to the provider that was open, not to the first of the list (item 559).
            opened = await phone.evaluate("document.activeElement.querySelector('.provider-name')?.textContent")
            assert opened == "TypeSafe", opened
            assert await phone.locator(".provider-item[aria-current]").count() == 0
            assert phone_errors == []
            await phone.context.close()
        finally:
            await browser.close()
