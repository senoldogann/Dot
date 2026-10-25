"""Whether runs go on after a restart (item 231), which they do by default (item 248): the switch of Settings › System
writes ``[agent] resume`` into the config file, which the daemon's next start reads, and leaves the comments and the
other settings as they were; the daemon's status says it at once. Served by a real server in a throw-away home, wide and
on a phone, light and dark."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "resume-switch-token"
# The owner's own words and settings in the file, which the switch must leave as they are.
_AGENT_TABLE: str = "[agent]\n# mine: keep this\ngoal_mode = false\n"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, whose config file has an ``[agent]`` table of its own."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n{_AGENT_TABLE}', encoding="utf-8"
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
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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


async def _file(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _resume(http: httpx.AsyncClient) -> object:
    """What the daemon's status says of resuming."""
    response: httpx.Response = await http.get("/api/status")
    assert response.status_code == 200, response.text
    return response.json()["resume"]


async def test_the_switch_writes_whether_the_next_start_resumes_the_runs_it_cut_off(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    # The throw-away home's config file, never the owner's.
    path: Path = config_path()
    assert path == Path(os.environ["DOT_HOME"]) / "config.toml"
    assert (await _file(path)).endswith(_AGENT_TABLE)
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with (
        httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
        async_playwright() as playwright,
    ):
        # On by default (item 248): the next start resumes the runs it cut off.
        assert await _resume(http) == {"resume": "safe", "resume_within_s": 1800}
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/settings/system")
                # The switch stands in Details, shut until it is opened (item 625).
                details: Locator = page.locator(
                    "section.card.fold", has=page.locator(".fold-title", has_text="Details")
                )
                await details.locator(".fold-toggle").click()
                switch: Locator = page.get_by_role("switch", name="Go on after a restart")
                setting: Locator = page.locator(".setting").filter(has=switch)
                await expect(switch).to_have_attribute("aria-checked", "true")
                await expect(setting.locator(".hint")).to_contain_text("in the last 30 min goes on by itself")
                assert not (await setting.evaluate("(row) => row.scrollWidth > row.clientWidth"))
                await setting.scroll_into_view_if_needed()
                await page.mouse.move(0, 0)
                await page.screenshot(path=str(tmp_path / f"resume-{width}-{scheme}.png"), animations="disabled")

                # Off: the runs it cut off wait for the owner; the file says so, the rest of it as it was.
                await switch.click()
                await expect(switch).to_have_attribute("aria-checked", "false")
                assert await _resume(http) == {"resume": "off", "resume_within_s": 1800}
                text: str = await _file(path)
                assert text.endswith('[agent]\nresume = "off"\n# mine: keep this\ngoal_mode = false\n'), text
                assert load_config(path).agent.resume == "off"

                # On again.
                await switch.click()
                await expect(switch).to_have_attribute("aria-checked", "true")
                assert await _resume(http) == {"resume": "safe", "resume_within_s": 1800}
                assert load_config(path).agent.resume == "safe"
                await page.close()
        finally:
            await browser.close()
