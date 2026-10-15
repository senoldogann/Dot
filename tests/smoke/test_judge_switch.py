"""The switch of the typed judge (item 255), on a daemon served by a real server whose TypeSafe key is not set: the API
says the judge is off and its key unset, refuses to switch it on without the service, writes "none" into the config
file when asked, and the doctor's row follows the switch; the Keys page in Chrome shows the switch on the row of
TypeSafe's key, off and disabled, wide and on a phone, light and dark. Before, the judge was chosen by the config file
alone, read at start.

No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "judge-switch-test-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, str]]:
    """The daemon in the home of the environment, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
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
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


@pytest.mark.browser
async def test_the_judge_is_switched_through_the_api_and_shown_on_the_keys_page(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, address):
            async with httpx.AsyncClient(
                base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
            ) as client:
                off = (await client.get("/judge")).json()
                assert (off["provider"], off["key_set"]) == ("none", False)
                assert off["key_name"] == runtime.config.judge.api_key_env
                # Without the service (no key when the daemon started) the judge cannot be switched on.
                refused: httpx.Response = await client.put("/judge", json={"provider": "typesafe"})
                assert refused.status_code >= 400 and runtime.judge.provider == "none"
                assert "TYPESAFE_API_KEY" in refused.text
                # Switching it off is written into the config file, and the doctor reads the switch.
                still = (await client.put("/judge", json={"provider": "none"})).json()
                assert still["provider"] == "none"
                assert '[judge]\nprovider = "none"' in (home / "config.toml").read_text(encoding="utf-8")
                rows = [row for row in (await client.get("/doctor")).json() if row["name"] == "judge"]
                assert len(rows) == 1 and rows[0]["detail"].startswith("off (")
            async with async_playwright() as playwright:
                host, port = "127.0.0.1", int(address.rpartition(":")[2])
                browser: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                try:
                    for width, scheme in _SHOTS:
                        page: Page = await browser.new_page(
                            viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                        )
                        await page.goto(ui_url(host, port, TOKEN))
                        await page.goto(f"{address}/#/settings/keys")
                        switch: Locator = page.get_by_role("switch", name="Typed judge (Jev)")
                        await switch.wait_for()
                        assert await switch.get_attribute("aria-checked") == "false"
                        assert await switch.is_disabled()
                        row: Locator = page.locator(".key-row").filter(has=switch)
                        assert "TypeSafe" in (await row.locator(".row-title").text_content() or "")
                        assert await row.evaluate("(row) => row.getBoundingClientRect().right <= window.innerWidth")
                        await switch.scroll_into_view_if_needed()
                        await page.screenshot(path=tmp_path / f"judge-switch-{width}-{scheme}.png")
                        await page.close()
                finally:
                    await browser.close()
