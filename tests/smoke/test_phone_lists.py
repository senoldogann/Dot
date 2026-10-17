"""Dot's lists on a phone (item 332), in WebKit as the owner's iPhone and as a small one, on a served daemon with a run at
work and a run it delegated, at work too.

On a phone a card's name stands over the card, out of its surface, on Activity's sections and Connect apps' (as in the
settings' tabs, item 330); a row of Activity is its title over one line of facts, a delegated run's too; Team's empty
line says what brings messages there. In a Mac's window the cards keep their names inside. Before, the names were inside
on a phone too, a delegated run's facts ran to three lines and Team's empty line named a tool. The model is a scripted
one.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-lists-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_PHONES: tuple[str, ...] = ("iPhone 17 Pro Max", "iPhone SE")
# Long enough for the checks: the runs work while the pages are looked at, and the daemon stops them at the end.
_WORK: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 60"}),))
# One line of facts, whatever its size: under one and a half of its own sizes high.
_ONE_LINE: float = 1.5
_CARD: str = """(card) => {
  const head = card.querySelector(':scope > .card-head');
  const ground = (node) => getComputedStyle(node).backgroundColor;
  return { card: ground(card), head: ground(head), body: ground(head.nextElementSibling) };
}"""
_LINES: str = "(node) => node.getBoundingClientRect().height / parseFloat(getComputedStyle(node).fontSize)"
_CLEAR: str = "rgba(0, 0, 0, 0)"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    yield home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon on the Dot home ``home`` whose model is the scripted one, whose runs wait in a command: its run manager
    and the address it is at."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_WORK, _WORK]))
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _working(manager: RunManager) -> None:
    """A task at work and a run it delegated, at work too."""
    parent: RunRecord = await manager.start(
        "Watch tonight's build and tell me what broke", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard",
        None, "yolo", False, (),
    )  # fmt: skip
    await manager.start(
        "Read the build log and list every failure with its test", "task", SCRIPTED_MODEL, None, "ui", parent.id, None,
        "standard", None, "yolo", False, (),
    )  # fmt: skip


async def _phone(browser: Browser, playwright: Playwright, device: str) -> BrowserContext:
    context: BrowserContext = await browser.new_context(
        **playwright.devices[device], color_scheme="light", reduced_motion="reduce"
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    return context


async def _ground(card: Locator) -> dict[str, str]:
    await expect(card).to_be_visible()
    found: object = await card.evaluate(_CARD)
    assert isinstance(found, dict)
    return {str(key): str(value) for key, value in found.items()}


async def test_a_phones_lists_name_their_cards_over_them_and_keep_a_row_to_two_lines(own_home: Path) -> None:
    async with _served(own_home) as (manager, address), async_playwright() as playwright:
        await _working(manager)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for device in _PHONES:
                context: BrowserContext = await _phone(browser, playwright, device)
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))

                await page.goto(f"{address}/#/activity")
                child: Locator = page.locator(".activity-children .activity-row").first
                await expect(child).to_be_visible(timeout=15_000)
                facts: Locator = child.locator(".meta-facts")
                await expect(facts).to_contain_text("delegated")
                lines: object = await facts.evaluate(_LINES)
                assert isinstance(lines, int | float) and lines < _ONE_LINE, (device, lines)
                ground: dict[str, str] = await _ground(page.locator(".activity-section").first)
                assert ground["card"] == _CLEAR and ground["head"] == _CLEAR and ground["body"] != _CLEAR, ground

                await page.goto(f"{address}/#/connect")
                ground = await _ground(page.locator(".connect-section").first)
                assert ground["card"] == _CLEAR and ground["head"] == _CLEAR and ground["body"] != _CLEAR, ground

                await page.goto(f"{address}/#/team")
                empty: Locator = page.locator("main .empty-line")
                await expect(empty).to_contain_text("Ask Dot")
                await expect(empty).not_to_contain_text("message_agent")
                await context.close()

            page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/activity")
            ground = await _ground(page.locator(".activity-section").first)
            assert ground["card"] != _CLEAR, ground
        finally:
            await browser.close()
