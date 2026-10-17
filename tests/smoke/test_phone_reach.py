"""Settings › Phone sets up Tailscale and keeps the Mac awake (item 271), against a stand-in ``tailscale`` and a
stand-in ``caffeinate`` that ``DOT_TAILSCALE`` and ``DOT_CAFFEINATE`` name: neither the real Tailscale nor the Mac's
sleep is touched.

The daemon is served on this Mac in a throw-away home; the stand-in Tailscale answers as its command line does, and
writes down what it is asked to serve.
"""

import asyncio
import stat
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from playwright.async_api import Browser, BrowserContext, FloatRect, Locator, Page, async_playwright, expect

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
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
_SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A folder with a ``tailscale`` that runs signed in and serves nothing until it is asked, and a ``caffeinate`` that
    only waits, which ``DOT_TAILSCALE`` and ``DOT_CAFFEINATE`` name. Yields the folder, where the stand-in writes what
    it was asked to serve."""
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
    monkeypatch.setenv("DOT_TAILSCALE", str(folder / "tailscale"))
    monkeypatch.setenv("DOT_CAFFEINATE", str(folder / "caffeinate"))
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


def _client(address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)


async def test_set_up_serves_dot_and_writes_its_name_and_the_mac_is_kept_awake_on_demand(stand_ins: Path) -> None:
    async with _daemon() as (runtime, address), _client(address) as client:
        port: int = int(address.rpartition(":")[2])
        before: dict[str, object] = (await client.get("/phone")).json()
        assert before["tailnet"] == {
            "installed": True,
            "running": True,
            "name": _NAME,
            "served": False,
            "reached": False,
            "written": False,
            "problem": None,
        }
        assert before["keep_awake"] is False
        # Pressed twice at once, Set up asks Tailscale once and writes the name once.
        both = await asyncio.gather(client.post("/phone/tailnet"), client.post("/phone/tailnet"))
        assert [answer.status_code for answer in both] == [200, 200], [answer.text for answer in both]
        after = both[1].json()["tailnet"]
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


async def test_a_config_file_that_cannot_be_changed_leaves_the_mac_as_it_was(stand_ins: Path) -> None:
    async with _daemon() as (runtime, address), _client(address) as client:
        config_path().write_text("[server\n", encoding="utf-8")
        refused = await client.post("/phone/awake", json={"on": True})
        assert refused.status_code == 422, refused.text
        assert not runtime.awake.on


async def test_a_mac_without_tailscale_is_told_how_to_get_it() -> None:
    # The Tailscale of every smoke test is one that does not exist (tests/smoke/conftest.py).
    async with _daemon() as (_runtime, address), _client(address) as client:
        assert (await client.get("/phone")).json()["tailnet"]["installed"] is False
        refused = await client.post("/phone/tailnet")
        assert refused.status_code == 422 and "install it from tailscale.com" in refused.text


async def test_a_tailscale_whose_app_is_closed_is_not_running_and_the_phone_screen_still_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tailscale's command line, as it answers while the app on the Mac is not open.
    said: str = "failed to connect to local Tailscale service; is Tailscale running?"
    closed: Path = tmp_path / "tailscale"
    _executable(closed, f"#!/bin/sh\necho '{said}' >&2\nexit 1\n")
    monkeypatch.setenv("DOT_TAILSCALE", str(closed))
    async with _daemon() as (_runtime, address), _client(address) as client:
        shown = await client.get("/phone")
        assert shown.status_code == 200, shown.text
        tailnet = shown.json()["tailnet"]
        assert (tailnet["installed"], tailnet["running"], tailnet["problem"]) == (
            True,
            False,
            f"Tailscale refused: {said}",
        )
        refused = await client.post("/phone/tailnet")
        assert refused.status_code == 422 and "open it on this Mac and sign in" in refused.text


async def test_what_tailscale_serves_unread_is_said_and_the_switch_still_answers(stand_ins: Path) -> None:
    (stand_ins / "serve.json").write_text("not json", encoding="utf-8")
    async with _daemon() as (runtime, address), _client(address) as client:
        shown = await client.get("/phone")
        assert shown.status_code == 200, shown.text
        tailnet = shown.json()["tailnet"]
        assert (tailnet["running"], tailnet["served"], tailnet["problem"]) == (
            True,
            False,
            "what Tailscale serves could not be read",
        )
        on = await client.post("/phone/awake", json={"on": True})
        assert on.status_code == 200 and on.json()["keep_awake"] is True and runtime.awake.on


async def _card_gap(page: Page) -> float:
    """The room between the card of Tailscale and the card of the phone below it."""
    reach: FloatRect | None = await page.locator("section.card", has_text="Reach Dot from anywhere").bounding_box()
    phone: FloatRect | None = await page.locator("section.card", has_text="Dot on your phone").bounding_box()
    assert reach is not None and phone is not None
    return phone["y"] - (reach["y"] + reach["height"])


@pytest.mark.browser
async def test_settings_phone_shows_what_a_phone_needs_and_sets_it_up(stand_ins: Path, tmp_path: Path) -> None:
    async with _daemon() as (_runtime, address), async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            for scheme in _SCHEMES:
                for name, browser, phone in (("wide", chromium, False), ("phone", webkit, True)):
                    context: BrowserContext = await browser.new_context(
                        **(
                            {**playwright.devices[_OWNERS_PHONE], "color_scheme": scheme, "reduced_motion": "reduce"}
                            if phone
                            else {
                                "viewport": {"width": 1280, "height": 900},
                                "color_scheme": scheme,
                                "reduced_motion": "reduce",
                            }
                        )
                    )
                    page: Page = await context.new_page()
                    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                    await page.goto(f"{address}/#/settings/phone")
                    lines: Locator = page.get_by_role("list", name="Tailscale")
                    await expect(lines).to_contain_text(f"this Mac is {_NAME} on your tailnet")
                    set_up: Locator = page.get_by_role("button", name="Set up")
                    if await set_up.is_visible():
                        await set_up.click()
                    await expect(lines).to_contain_text("Tailscale serves Dot to your tailnet over HTTPS.")
                    await expect(lines).to_contain_text("Dot answers to it from the daemon's next start")
                    assert await _card_gap(page) >= 8
                    awake: Locator = page.get_by_role("switch", name="Keep this Mac awake while it charges")
                    await awake.click()
                    await expect(awake).to_have_attribute("aria-checked", "true")
                    await page.screenshot(path=tmp_path / f"phone-reach-{name}-{scheme}.png", full_page=True)
                    await awake.click()
                    await expect(awake).to_have_attribute("aria-checked", "false")
                    await context.close()
        finally:
            await chromium.close()
            await webkit.close()
