"""Pairing a phone (item 267), on a served daemon: Settings › Phone gives a code, and an app on a phone's home screen,
whose storage is its own, trades it for the access token. Through the API a code needs the token to be given and none
to be traded, is traded once, in the case and the dashes a person types it, and five wrong tries or a newer code use it
up; the guard still turns away another site's page, and the phone's address comes from ``[server] reach``. In Chrome,
a page signed in on the Mac gets a code from Settings › Phone, and a phone with no token types it on the sign-in screen
and is let in; a wrong code is told, and a token typed where the code goes is not sent. Wide and on a phone, light
and dark. Before, the sign-in screen took the token only.

No network and no model service is used.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-pairing-test-token"
TAILNET_NAME: str = "dot-mac.tailnet-test.ts.net"
CODE: re.Pattern[str] = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home`` that a reverse proxy presents as ``TAILNET_NAME``, at the address it yields."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[server]\nreach = ["{TAILNET_NAME}"]\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
        await close_runtime(runtime)


async def test_a_code_is_traded_once_for_the_token(own_home: Path) -> None:
    async with _served(own_home) as address, httpx.AsyncClient(base_url=f"{address}/api", timeout=30.0) as client:
        signed: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        # As the phone's app asks: through the tailnet name, from a page of it.
        phone: dict[str, str] = {"Host": TAILNET_NAME, "Origin": f"https://{TAILNET_NAME}"}
        assert (await client.post("/phone/pairing")).status_code == 401
        assert (await client.get("/phone", headers=signed)).json()["addresses"] == [f"https://{TAILNET_NAME}/"]

        issued: httpx.Response = await client.post("/phone/pairing", headers=signed)
        code: str = issued.json()["code"]
        assert CODE.match(code) and issued.json()["expires_at"] > time.time() + 590
        typed: str = code.lower().replace("-", " ")
        paired: httpx.Response = await client.post("/phone/pair", json={"code": typed}, headers=phone)
        assert (paired.status_code, paired.json()) == (200, {"token": TOKEN})
        again: httpx.Response = await client.post("/phone/pair", json={"code": code}, headers=phone)
        assert again.status_code == 403 and "ask for a new one in Settings › Phone" in again.json()["message"]

        # Five wrong tries use a code up; the right one after them is refused.
        code = (await client.post("/phone/pairing", headers=signed)).json()["code"]
        for left in range(4, -1, -1):
            wrong: httpx.Response = await client.post("/phone/pair", json={"code": "0000-0000"}, headers=phone)
            assert (wrong.status_code, wrong.json()["context"]["tries_left"]) == (403, left)
        assert (await client.post("/phone/pair", json={"code": code}, headers=phone)).status_code == 403

        # A newer code takes the place of the one before; another site's page is turned away by the guard.
        older: str = (await client.post("/phone/pairing", headers=signed)).json()["code"]
        newer: str = (await client.post("/phone/pairing", headers=signed)).json()["code"]
        assert (await client.post("/phone/pair", json={"code": older}, headers=phone)).status_code == 403
        elsewhere: dict[str, str] = {"Host": TAILNET_NAME, "Origin": "https://pages.example"}
        assert (await client.post("/phone/pair", json={"code": newer}, headers=elsewhere)).status_code == 403
        newest: str = (await client.post("/phone/pairing", headers=signed)).json()["code"]
        assert (await client.post("/phone/pair", json={"code": newest}, headers=phone)).json() == {"token": TOKEN}


async def _one_size(browser: Browser, address: str, size: tuple[int, Literal["light", "dark"]], shots: Path) -> None:
    width, scheme = size
    port: int = int(address.rpartition(":")[2])
    mac: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await mac.goto(ui_url("127.0.0.1", port, TOKEN))
    await mac.goto(f"{address}/#/settings/phone")
    # The address is the QR code's own once Dot answers to a name (item 565); the code is hidden until it is shown.
    await expect(mac.locator(".phone-address-text")).to_have_text(f"https://{TAILNET_NAME}/")
    await expect(mac.locator("output.pair-code")).to_be_hidden()
    await mac.get_by_role("button", name="Show code").click()
    shown: Locator = mac.locator("output.pair-code")
    await expect(shown).to_have_text(CODE)
    code: str = await shown.inner_text()
    # The guide of Settings › Phone has hints of its own (item 421): the code's is the one that says when it ends.
    await expect(mac.locator(".phone-code-lead", has_text="Type it in Dot")).to_contain_text("It works once")
    # Shown, the code is the last step's (the steps before it are ticked), and the button asks for another.
    await expect(mac.locator(".phone-step.current")).to_contain_text("Sign in with a code")
    await expect(mac.get_by_role("button", name="New code")).to_be_visible()
    await mac.screenshot(path=str(shots / f"settings-{width}-{scheme}.png"))

    # A phone with no token: a wrong code is told, the right one lets it in.
    phone: Page = await browser.new_page(
        viewport={"width": 390, "height": 844}, color_scheme=scheme, reduced_motion="reduce"
    )
    await phone.goto(f"{address}/")
    # It names the button Settings › Phone has (item 565), which shows the code.
    await expect(phone.locator(".signin")).to_contain_text("go to Settings › Phone and choose Show code")
    field: Locator = phone.get_by_role("textbox", name="Sign-in code")
    # A token typed where the code goes is never sent: the daemon is not proven before a code goes.
    tried: list[str] = []
    phone.on("request", lambda request: tried.append(request.url) if request.url.endswith("/phone/pair") else None)
    await field.fill(TOKEN)
    await phone.get_by_role("button", name="Sign in", exact=True).click()
    await expect(phone.locator(".signin .note.bad")).to_contain_text("That does not look like a sign-in code")
    assert tried == []
    await field.fill("0000-0000")
    await phone.get_by_role("button", name="Sign in", exact=True).click()
    await expect(phone.locator(".signin .note.bad")).to_contain_text("That sign-in code is wrong, used or out of date")
    await phone.screenshot(path=str(shots / f"signin-{width}-{scheme}.png"))
    await phone.get_by_role("textbox", name="Sign-in code").fill(code.lower())
    await phone.get_by_role("textbox", name="Sign-in code").press("Enter")
    await expect(phone.get_by_role("textbox", name="Goal")).to_be_visible()
    assert await phone.evaluate("localStorage.getItem('dot.token')") == TOKEN
    await phone.close()
    await mac.close()


async def test_a_phone_signs_in_with_the_code_settings_gives(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as address, async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for size in _SHOTS:
                await _one_size(browser, address, size, tmp_path)
        finally:
            await browser.close()
