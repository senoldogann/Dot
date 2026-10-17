"""Settings › Phone sets up Tailscale and keeps the Mac awake (item 271), against stand-ins of ``tailscale`` and
``caffeinate`` put first on the PATH: neither the real Tailscale nor the Mac's sleep is touched.

The daemon is served on this Mac in a throw-away home; the stand-in Tailscale answers as its command line does, and
writes down what it is asked to serve.
"""

import asyncio
import os
import stat
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import config_path
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "phone-reach-test-token"
_NAME: str = "mac.tail1234.ts.net"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A folder first on the PATH with a ``tailscale`` that runs signed in and serves nothing until it is asked, and a
    ``caffeinate`` that only waits. Yields the folder, where the stand-in writes what it was asked to serve."""
    folder: Path = tmp_path / "bin"
    folder.mkdir()
    (folder / "status.json").write_text(
        f'{{"BackendState": "Running", "Self": {{"DNSName": "{_NAME}."}}}}', encoding="utf-8"
    )
    (folder / "serve.json").write_text("{}", encoding="utf-8")
    _executable(
        folder / "tailscale",
        f"""#!/bin/sh
case "$*" in
  "status --json") cat "{folder}/status.json" ;;
  "serve status --json") cat "{folder}/serve.json" ;;
  "serve --bg "*)
    echo "$*" >> "{folder}/asked"
    printf '{{"Web": {{"{_NAME}:443": {{"Handlers": {{"/": {{"Proxy": "http://127.0.0.1:%s"}}}}}}}}}}' "$3" \\
      > "{folder}/serve.json" ;;
  *) exit 2 ;;
esac
""",
    )
    _executable(folder / "caffeinate", "#!/bin/sh\nexec sleep 60\n")
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    yield folder


@asynccontextmanager
async def _daemon() -> AsyncIterator[tuple[Runtime, str]]:
    """A daemon in a throw-away home: its runtime, and the address it serves on."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
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
                yield runtime, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


async def test_set_up_serves_dot_and_writes_its_name_and_the_mac_is_kept_awake_on_demand(stand_ins: Path) -> None:
    async with (
        _daemon() as (runtime, address),
        httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30
        ) as client,
    ):
        port: int = int(address.rpartition(":")[2])
        before: dict[str, object] = (await client.get("/phone")).json()
        assert before["tailnet"] == {
            "installed": True,
            "running": True,
            "name": _NAME,
            "served": False,
            "reached": False,
            "written": False,
        }
        assert before["keep_awake"] is False
        set_up = await client.post("/phone/tailnet")
        assert set_up.status_code == 200, set_up.text
        after = set_up.json()["tailnet"]
        assert (after["served"], after["written"], after["reached"]) == (True, True, False)
        assert (stand_ins / "asked").read_text(encoding="utf-8") == f"serve --bg {port}\n"
        assert f'reach = ["{_NAME}"]' in config_path().read_text(encoding="utf-8")
        # Set up again, nothing more is asked of Tailscale and the name is written once.
        again = await client.post("/phone/tailnet")
        assert again.status_code == 200 and (stand_ins / "asked").read_text(encoding="utf-8").count("serve") == 1
        assert config_path().read_text(encoding="utf-8").count(_NAME) == 1
        on = await client.post("/phone/awake", json={"on": True})
        assert on.status_code == 200 and on.json()["keep_awake"] is True and runtime.awake.on
        assert "keep_awake = true" in config_path().read_text(encoding="utf-8")
        off = await client.post("/phone/awake", json={"on": False})
        assert off.json()["keep_awake"] is False and not runtime.awake.on
        assert "keep_awake = false" in config_path().read_text(encoding="utf-8")


async def test_a_mac_without_tailscale_is_told_how_to_get_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("dotagent.tailnet._APP_CLI", tmp_path / "no-tailscale")
    async with (
        _daemon() as (_runtime, address),
        httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30
        ) as client,
    ):
        assert (await client.get("/phone")).json()["tailnet"]["installed"] is False
        refused = await client.post("/phone/tailnet")
        assert refused.status_code == 422 and "install it from tailscale.com" in refused.text


@pytest.mark.browser
async def test_settings_phone_shows_what_a_phone_needs_and_sets_it_up(stand_ins: Path, tmp_path: Path) -> None:
    async with _daemon() as (_runtime, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/settings/phone")
                lines: Locator = page.get_by_role("list", name="Tailscale")
                await expect(lines).to_contain_text(f"this Mac is {_NAME} on your tailnet")
                set_up: Locator = page.get_by_role("button", name="Set up")
                if await set_up.is_visible():
                    await set_up.click()
                await expect(lines).to_contain_text("Tailscale serves Dot to your tailnet over HTTPS.")
                await expect(lines).to_contain_text("Dot answers to it from its next start")
                awake: Locator = page.get_by_role("switch", name="Keep this Mac awake while it charges")
                await awake.click()
                await expect(awake).to_have_attribute("aria-checked", "true")
                await page.screenshot(path=tmp_path / f"phone-reach-{width}-{scheme}.png", full_page=True)
                await awake.click()
                await expect(awake).to_have_attribute("aria-checked", "false")
                await page.close()
        finally:
            await browser.close()
