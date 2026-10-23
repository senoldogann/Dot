"""Settings › Phone sets up Tailscale and keeps the Mac awake (item 271), against a stand-in ``tailscale`` and a
stand-in ``caffeinate`` that ``DOT_TAILSCALE`` and ``DOT_CAFFEINATE`` name: neither the real Tailscale nor the Mac's
sleep is touched.

The daemon is served on this Mac in a throw-away home; the stand-in Tailscale answers as its command line does, and
writes down what it is asked to serve.
"""

import asyncio
import os
import re
import stat
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import Quartz
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
async def _daemon(reach: list[str]) -> AsyncIterator[tuple[Runtime, str]]:
    """A daemon in a throw-away home that answers to the names ``reach`` too: its runtime, and the address it serves
    on."""
    plain: AppConfig = eval_config(load_default_config())
    config: AppConfig = plain.model_copy(update={"server": plain.server.model_copy(update={"reach": reach})})
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


@pytest.fixture
def opener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``open`` that only writes down what it was asked to open, which ``DOT_OPEN`` names; returns the file it
    writes."""
    asked: Path = tmp_path / "opened"
    _executable(tmp_path / "open", f'#!/bin/sh\necho "$*" >> "{asked}"\n')
    monkeypatch.setenv("DOT_OPEN", str(tmp_path / "open"))
    return asked


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _read_qr(png: bytes) -> list[str]:
    """What the QR codes in ``png`` open, as macOS's detector reads them."""
    detector = Quartz.CIDetector.detectorOfType_context_options_(
        Quartz.CIDetectorTypeQRCode, None, {Quartz.CIDetectorAccuracy: Quartz.CIDetectorAccuracyHigh}
    )
    return [str(feature.messageString()) for feature in detector.featuresInImage_(Quartz.CIImage.imageWithData_(png))]


def _client(address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)


async def test_set_up_serves_dot_and_writes_its_name_and_the_mac_is_kept_awake_on_demand(stand_ins: Path) -> None:
    async with _daemon([]) as (runtime, address), _client(address) as client:
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
    async with _daemon([]) as (runtime, address), _client(address) as client:
        config_path().write_text("[server\n", encoding="utf-8")
        refused = await client.post("/phone/awake", json={"on": True})
        assert refused.status_code == 422, refused.text
        assert not runtime.awake.on


async def test_a_mac_without_tailscale_is_told_how_to_get_it() -> None:
    # The Tailscale of every smoke test is one that does not exist (tests/smoke/conftest.py).
    async with _daemon([]) as (_runtime, address), _client(address) as client:
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
    async with _daemon([]) as (_runtime, address), _client(address) as client:
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
    async with _daemon([]) as (runtime, address), _client(address) as client:
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


async def test_a_serve_that_waits_is_ended_in_time_and_says_what_it_waits_for(
    stand_ins: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tailscale's serve as it waits for HTTPS to be turned on for the tailnet: it says where, then waits.
    _executable(
        stand_ins / "tailscale",
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  "status --json") cat "{stand_ins}/status.json" ;;\n'
        '  "serve status --json") echo "{}" ;;\n'
        '  "serve --bg "*)\n'
        f'    echo $$ > "{stand_ins}/waiting"\n'
        "    printf 'Serve is not enabled on your tailnet.\\nTo enable, visit:\\n\\n' >&2\n"
        "    printf '    https://login.example/f/serve\\n' >&2\n"
        "    exec sleep 30 ;;\n"
        "esac\n",
    )
    monkeypatch.setattr("dotagent.tailnet._SERVE_TIMEOUT_S", 1.0)
    async with _daemon([]) as (_runtime, address), _client(address) as client:
        refused = await client.post("/phone/tailnet")
        assert refused.status_code == 422, refused.text
        assert (
            "Tailscale did not answer in 1 s: Serve is not enabled on your tailnet. To enable, visit: "
            "https://login.example/f/serve"
        ) in refused.text
        # The page that turns HTTPS on is offered as a link (item 421).
        assert refused.json()["context"]["link"] == "https://login.example/f/serve"
        # The serve that waited is ended.
        with pytest.raises(ProcessLookupError):
            os.kill(int((stand_ins / "waiting").read_text(encoding="utf-8")), 0)


async def _card_gap(page: Page) -> float:
    """The room between the card that says whether the phone reaches Dot and the card of the phone's steps below it."""
    reach: FloatRect | None = await page.locator("section.phone-reach").bounding_box()
    phone: FloatRect | None = await page.locator(".set-card", has=page.locator(".phone-setup-grid")).bounding_box()
    assert reach is not None and phone is not None
    return phone["y"] - (reach["y"] + reach["height"])


@pytest.mark.browser
async def test_settings_phone_shows_what_a_phone_needs_and_sets_it_up(stand_ins: Path, tmp_path: Path) -> None:
    async with _daemon([]) as (_runtime, address), async_playwright() as playwright:
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
                    steps: Locator = page.get_by_role("list", name="Steps on this Mac")
                    await expect(steps).to_contain_text(f"This Mac is {_NAME} on your tailnet.")
                    set_up: Locator = page.get_by_role("button", name="Set up")
                    if await set_up.is_visible():
                        await set_up.click()
                    await expect(steps).to_contain_text("Tailscale serves Dot to your tailnet over HTTPS")
                    await expect(steps).to_contain_text("Dot takes the name up when it starts again.")
                    # Two steps of three are done, and the one left is the one to do now (item 527).
                    progress: Locator = page.get_by_role("progressbar", name="Steps done on this Mac")
                    await expect(progress).to_have_attribute("aria-valuenow", "2")
                    await expect(steps.locator(".phone-guide-step.current")).to_contain_text("Dot answers to the name")
                    # Restart Dot asks first: the runs going on would stop with it.
                    await steps.get_by_role("button", name="Restart Dot").click()
                    asked: Locator = page.locator("dialog.modal")
                    await expect(asked).to_contain_text("Restart Dot?")
                    await asked.get_by_role("button", name="Cancel").click()
                    await expect(asked).to_have_count(0)
                    await expect(steps).to_contain_text("Dot takes the name up when it starts again.")
                    download: Locator = page.get_by_role("img", name="A QR code of Tailscale's download page")
                    await expect(download).to_be_visible()
                    # The picture shows before its address, which comes once its blob is read; it is read once it has
                    # one and is decoded.
                    await expect(download).to_have_attribute("src", re.compile(r"^blob:"))
                    assert await download.evaluate("picture => picture.decode().then(() => picture.naturalWidth)") > 0
                    assert await _card_gap(page) >= 8
                    # The one QR code opens Tailscale's page or Dot's address (item 565); Dot answers to no name yet and
                    # says so, and the step the guide is at is the one whose code is shown.
                    await expect(page.locator(".phone-step.current")).to_contain_text("Install Tailscale on the phone")
                    await page.get_by_role("button", name="Open Dot").click()
                    await expect(page.get_by_text("No address yet")).to_be_visible()
                    await expect(download).to_have_count(0)
                    await expect(page.locator(".phone-step.current")).to_contain_text("Open Dot on the phone")
                    await page.get_by_role("button", name="Tailscale app").click()
                    await expect(page.get_by_role("img", name="A QR code of Tailscale's download page")).to_be_visible()
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


async def test_tailscale_is_opened_in_the_app_store_while_it_is_missing(opener: Path) -> None:
    async with _daemon([]) as (_runtime, address), _client(address) as client:
        opened = await client.post("/phone/tailscale")
        assert opened.status_code == 200, opened.text
    assert await asyncio.to_thread(_lines, opener) == ["macappstore://apps.apple.com/app/id1475387142"]


async def test_an_installed_tailscale_is_opened_as_its_app_to_sign_in(stand_ins: Path, opener: Path) -> None:
    async with _daemon([]) as (_runtime, address), _client(address) as client:
        opened = await client.post("/phone/tailscale")
        assert opened.status_code == 200, opened.text
    assert await asyncio.to_thread(_lines, opener) == ["-a Tailscale"]


async def test_the_qr_codes_open_tailscales_download_page_and_dots_address_once_it_has_one() -> None:
    async with _daemon([]) as (_runtime, address), _client(address) as client:
        download = await client.get("/phone/qr/tailscale")
        assert download.status_code == 200 and download.headers["content-type"] == "image/png"
        assert _read_qr(download.content) == ["https://tailscale.com/download"]
        assert (await client.get("/phone/qr/dot")).status_code == 422
        assert (await client.get("/phone/qr/elsewhere")).status_code == 422
    async with _daemon([_NAME]) as (_runtime, address), _client(address) as client:
        dot = await client.get("/phone/qr/dot")
        assert dot.status_code == 200, dot.text
        assert _read_qr(dot.content) == [f"https://{_NAME}/"]


@pytest.mark.browser
async def test_what_tailscale_says_while_it_runs_is_shown_in_the_guide(stand_ins: Path) -> None:
    """Tailscale runs signed in, but what it serves cannot be read: the step that serves Dot says what it said."""
    (stand_ins / "serve.json").write_text("not json", encoding="utf-8")
    async with _daemon([]) as (_runtime, address), async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await (await chromium.new_context(reduced_motion="reduce")).new_page()
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/settings/phone")
            steps: Locator = page.get_by_role("list", name="Steps on this Mac")
            await expect(steps).to_contain_text(f"This Mac is {_NAME} on your tailnet.")
            await expect(steps).to_contain_text("Tailscale said: what Tailscale serves could not be read")
        finally:
            await chromium.close()
