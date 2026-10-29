"""Settings › Trading in a real browser (docs/daemon.md): trading is switched from off to paper, a limit is changed and
kept in the config file, a limit the settings refuse says why, Live asks first, a key the Keychain stand-in never
receives is refused with its reason, and an order of the record is listed, its long reason wrapped inside the table's
card; wide and light, and on a phone and dark, where the page keeps inside the screen. The Keychain is a stand-in, and
so is Coinbase's market (an empty list of products, for the watch's count once "*" is allowed): the paper account
holds only euros, which need no price."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from vault_stand_ins import MemorySecretStore

from dotagent.agent.manager import RunManager
from dotagent.config import load_config, load_default_config
from dotagent.core.paths import config_path
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.engine_state import STATE_KEY, EngineResult, EngineShare, EngineState, pause, resume
from dotagent.trading.market import CoinbaseMarket

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "trading-settings-page-token"
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
# The orders table keeps inside its card, and its reason inside its cell (item 779: a reason kept to one line pushed
# the table past the card, which cut off its last columns).
_FITS: str = """(card) => {
  const reason = card.querySelector('.trading-reason td');
  return card.querySelector('table').offsetWidth <= card.clientWidth && reason.scrollWidth <= reason.clientWidth + 1;
}"""
_REASON: str = (
    "The price moved past the stop the run had set: the 15-minute RSI fell from 56 to 41, the daily SMA20 sits under"
    " the bid, the book thinned on the sell side and the order would have filled far below its limit, so it was not"
    " sent."
)
_SIZES: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _no_market(request: httpx.Request) -> httpx.Response:
    """Coinbase's market as a stand-in: an empty list of products, and no product of its own."""
    if request.url.path.endswith("/market/products"):
        return httpx.Response(200, json={"products": []})
    return httpx.Response(404, json={"error": "NOT_FOUND"})


@pytest_asyncio.fixture(loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home with trading off and a Keychain that is a dictionary."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[builtin_mcp]\nweb_research = false\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    built.trading.key_store = MemorySecretStore()
    # Coinbase is a stand-in that lists no product (item 777: with "*" the page reads the list for the watch's count).
    built.trading.market = CoinbaseMarket(5.0, httpx.MockTransport(_no_market))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
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


async def _fail_an_order(runtime: Runtime) -> None:
    """An order of the paper account that did nothing, after the account's start: it is listed and moves no balance."""
    await runtime.store.add_trading_order(
        TradingOrderRecord(
            id=f"ord_page_{time.time_ns()}",
            mode="paper",
            product="ETH-EUR",
            side="sell",
            notional=10.0,
            size=0.0,
            price=0.0,
            fee=0.0,
            status="failed",
            ts=time.time(),
            run_id="",
            exchange_id="",
            note=_REASON,
        )
    )


async def _reason_fits(page: Page) -> None:
    """The orders table, with an order's long reason under it, keeps inside its card."""
    card: Locator = page.locator(".trading-table-card").filter(has=page.locator(".trading-orders"))
    assert await card.evaluate(_FITS), "the orders table or its reason is wider than the card"


async def _held(page: Page, served: str) -> dict[str, Any]:
    """What the daemon holds of trading, asked as the page asks it."""
    answer = await page.request.get(f"{served}/api/trading", headers={"Authorization": f"Bearer {TOKEN}"})
    assert answer.ok, await answer.text()
    return dict(await answer.json())


def _mode(page: Page, name: str) -> Locator:
    return page.get_by_role("group", name="Trading").get_by_role("button", name=name, exact=True)


async def test_the_page_switches_trading_keeps_a_limit_and_asks_before_live(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SIZES:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
                await page.goto(f"{served}/#/settings/trading")
                await expect(page.locator(".trading")).to_be_visible()
                if width == 1280:
                    await _wide(page, runtime, served)
                else:
                    # Paper, the key's form, the limits, the account and an order (the account was started again
                    # after the first): the page keeps inside the screen.
                    await _fail_an_order(runtime)
                    await page.reload()
                    await expect(_mode(page, "Paper")).to_have_attribute("aria-pressed", "true")
                    await expect(page.locator(".trading [data-order]")).to_contain_text("Sell ETH-EUR")
                    await _reason_fits(page)
                    assert await page.evaluate(_OVERFLOW) <= 1
                await page.screenshot(path=str(tmp_path / f"trading-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()


async def _wide(page: Page, runtime: Runtime, served: str) -> None:
    held: dict[str, Any] = await _held(page, served)
    assert held["settings"]["mode"] == "off" and held["tools_on"] is False
    # Off: the limits and what each mode means; no key, no account, no orders.
    await expect(_mode(page, "Off")).to_have_attribute("aria-pressed", "true")
    await expect(page.locator(".trading-modes [data-mode]")).to_have_count(3)
    await expect(page.get_by_role("heading", name="Coinbase key")).to_have_count(0)
    await expect(page.get_by_role("heading", name="Orders")).to_have_count(0)
    # Paper: the account opens with its euros, the paper start joins the limits, the key's form appears, and runs are
    # told they get the tools after a restart.
    await _mode(page, "Paper").click()
    await expect(_mode(page, "Paper")).to_have_attribute("aria-pressed", "true")
    await expect(page.locator('[data-figure="equity"]')).to_have_text("1,000.00 EUR")
    await expect(page.locator('[data-figure="day"]')).to_have_text("0.00 EUR")
    await expect(page.locator('[data-figure="traded"]')).to_have_text("0.00 EUR of 100.00 EUR")
    # The desk (item 777): the paper account since its start, no open position yet, and the watch saying that no
    # schedule is started by a move of the market.
    await expect(page.locator('[data-figure="start"]')).to_have_text("0.00 EUR")
    await expect(page.get_by_role("heading", name="Open positions")).to_be_visible()
    await expect(page.get_by_text("No open position.", exact=False)).to_be_visible()
    await expect(page.locator('.trading [data-watch="wake"]')).to_contain_text("No schedule is started by a move")
    await expect(page.locator('[data-limit="paper_start"]')).to_be_visible()
    await expect(page.get_by_role("heading", name="Coinbase key")).to_be_visible()
    await expect(page.get_by_text("Runs get the trading tools after Dot restarts.")).to_be_visible()
    await expect(page.get_by_text("No orders yet")).to_be_visible()
    assert (await _held(page, served))["settings"]["mode"] == "paper"
    assert runtime.trading.config().mode == "paper"
    await _fail_an_order(runtime)
    await page.reload()
    await expect(page.locator(".trading [data-order]")).to_contain_text("The price moved")
    await _reason_fits(page)
    # A limit is changed and saved: only it is sent, and the config file keeps it.
    save: Locator = page.get_by_role("button", name="Save limits")
    await expect(save).to_be_disabled()
    await page.locator('[data-limit="max_order"]').fill("20")
    await expect(save).to_be_enabled()
    await page.locator('[data-limit="max_order"]').fill("12x")
    await expect(save).to_be_disabled()
    await page.locator('[data-limit="max_order"]').fill("20")
    await page.get_by_label("Product to allow").fill("xrp-eur")
    await page.get_by_role("button", name="Allow").click()
    await expect(page.locator('[data-product="XRP-EUR"]')).to_be_visible()
    # "*" is the whole market, said in words (item 771).
    await page.get_by_label("Product to allow").fill("*")
    await page.get_by_role("button", name="Allow").click()
    await expect(page.locator('[data-product="*"]')).to_have_text("Every product trading 1,000,000 EUR a day")
    await save.click()
    await expect(save).to_be_disabled()
    held = await _held(page, served)
    assert held["settings"]["max_order"] == 20.0 and held["settings"]["products"][-2:] == ["XRP-EUR", "*"]
    kept: dict[str, object] = tomllib.loads(config_path().read_text(encoding="utf-8"))["trading"]
    assert kept["max_order"] == 20.0 and kept["mode"] == "paper"
    # A limit the settings refuse says why, and changes nothing.
    await page.locator('[data-limit="max_order"]').fill("150")
    await save.click()
    await expect(page.get_by_role("alert").filter(has_text="max_order is more than max_day")).to_be_visible()
    assert runtime.trading.config().max_order == 20.0
    # Live asks first; Cancel leaves Paper.
    await _mode(page, "Live").click()
    dialog: Locator = page.get_by_role("dialog")
    await expect(dialog).to_contain_text("yolo")
    await dialog.get_by_role("button", name="Cancel").click()
    await expect(_mode(page, "Paper")).to_have_attribute("aria-pressed", "true")
    assert runtime.trading.config().mode == "paper"
    # A key that is no key is refused with its reason, and the Keychain stand-in never takes one.
    await page.get_by_label("Key name").fill("organizations/o/apiKeys/k")
    await page.get_by_label("Private key").fill("not a key " * 12)
    await page.get_by_role("button", name="Save key").click()
    await expect(page.locator(".trading-keyform .trading-problem")).to_be_visible()
    store = runtime.trading.key_store
    assert isinstance(store, MemorySecretStore) and store.kept == {}
    # The paper account starts again, after a question.
    await page.get_by_role("button", name="Start again").click()
    await page.get_by_role("dialog").get_by_role("button", name="Start again").click()
    await expect(page.locator('[data-figure="equity"]')).to_have_text("1,000.00 EUR")


async def test_the_watch_chooses_the_schedule_a_move_wakes_and_says_when_it_is_gone(
    runtime: Runtime, served: str
) -> None:
    """Item 813: Wake on a move lists the schedules and None, a schedule that is gone shows as such until another is
    chosen, and a choice is kept as the watch says it."""
    async with httpx.AsyncClient(base_url=f"{served}/api", headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        made: httpx.Response = await client.post(
            "/schedules", json={"name": "Paper shift", "goal": "Trade.", "cron": "15 * * * *"}
        )
    assert made.status_code == 201, made.text
    shift: str = made.json()["record"]["id"]
    runtime.trading.settings.value = runtime.trading.config().model_copy(
        update={"mode": "paper", "products": ["BTC-EUR"], "wake_schedule": "sch_gone"}
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
            await page.goto(f"{served}/#/settings/trading")
            wake: Locator = page.locator("[data-watch='wake']")
            await expect(wake).to_contain_text("is gone (sch_gone)")
            picker: Locator = page.get_by_role("combobox", name="Wake on a move")
            await expect(picker.locator("option:checked")).to_have_text("A schedule that is gone")
            await picker.select_option(shift)
            await expect(wake).to_contain_text("A move of the market starts Paper shift")
            assert runtime.trading.config().wake_schedule == shift
            await picker.select_option("")
            await expect(wake).to_contain_text("choose one under Wake on a move")
            assert runtime.trading.config().wake_schedule == ""
            # A schedule deleted while the page shows it is refused, and the picker shows what is kept beside why.
            async with httpx.AsyncClient(
                base_url=f"{served}/api", headers={"Authorization": f"Bearer {TOKEN}"}
            ) as client:
                assert (await client.delete(f"/schedules/{shift}")).status_code in (200, 204)
            await picker.select_option(shift)
            await expect(page.locator(".trading-problem:not([hidden])")).to_contain_text("No schedule has that id")
            await expect(picker).to_have_value("")
            assert runtime.trading.config().wake_schedule == ""
            await page.close()
        finally:
            await browser.close()
            runtime.trading.settings.value = runtime.trading.config().model_copy(update={"mode": "off"})


async def test_the_trend_engine_switches_on_and_shows_its_last_look_on_a_wide_page_and_a_phone(
    runtime: Runtime, served: str
) -> None:
    """Item 818: the engine's switch is kept; its last look shows the shares beside those held, what held an order
    back, and why it is paused; the table keeps inside its card on a phone too."""
    runtime.trading.settings.value = runtime.trading.config().model_copy(
        update={"mode": "paper", "products": ["BTC-EUR", "ETH-EUR", "SOL-EUR"], "engine": "off"}
    )
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(
                channel=load_default_config().browser.channel, headless=True
            )
            try:
                for width, scheme in _SIZES:
                    await runtime.store.kv_delete(STATE_KEY)
                    await resume(runtime.store)
                    runtime.trading.settings.value = runtime.trading.config().model_copy(update={"engine": "off"})
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
                    await page.goto(f"{served}/#/settings/trading")
                    engine: Locator = page.get_by_role("group", name="Trend engine")
                    await engine.get_by_role("button", name="On", exact=True).click()
                    await expect(page.get_by_text("It looks within the hour.")).to_be_visible()
                    assert runtime.trading.config().engine == "trend"
                    await runtime.store.kv_set(
                        STATE_KEY,
                        EngineState(
                            looked_at=time.time(),
                            shares=(
                                EngineShare(product="BTC-EUR", weight=0.1, target=0.33),
                                EngineShare(product="ETH-EUR", weight=0.0, target=0.0),
                            ),
                            placed=("ord_1",),
                            notes=("buy BTC-EUR not placed: the order, 25.00 EUR, is over what today's orders may "
                                   "still add up to, 0.00 EUR of 100.00 EUR",),
                            result=EngineResult(started_at=time.time() - 86_400, product="BTC-EUR", account=0.0123,
                                                held=-0.045),
                        ).model_dump_json(),
                    )  # fmt: skip
                    await pause(runtime.store, "Coinbase halted withdrawals", "run_s", time.time())
                    await page.reload()
                    await expect(page.locator("[data-share='BTC-EUR']")).to_contain_text("10%")
                    await expect(page.locator("[data-share='BTC-EUR']")).to_contain_text("33%")
                    await expect(page.locator("[data-engine='look']")).to_contain_text("1 order")
                    await expect(page.locator("[data-engine='note']")).to_contain_text("today's orders")
                    # Item 819: what the account made beside holding Bitcoin instead.
                    await expect(page.locator("[data-engine='result']")).to_contain_text("the account +1.23 %")
                    await expect(page.locator("[data-engine='result']")).to_contain_text("BTC-EUR instead \u22124.50 %")
                    await expect(page.locator("[data-engine='paused']")).to_contain_text("Paused by run_s since")
                    card: Locator = page.locator(".trading-table-card").filter(has=page.locator("[data-share]"))
                    assert await card.evaluate("card => card.scrollWidth <= card.clientWidth + 1"), width
                    await page.close()
            finally:
                await browser.close()
    finally:
        await resume(runtime.store)
        runtime.trading.settings.value = runtime.trading.config().model_copy(update={"mode": "off", "engine": "off"})
