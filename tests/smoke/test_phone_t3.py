"""A phone's run page and list of chats after T3 Code's phone app (items 294 to 298), on a served daemon, in WebKit as
the owner's iPhone, light and dark. The bar names the chat over the agent and the model, and holds its tools in one
pill: Dot's computer once the run has had a page, the helpers with their count, and the details. A line over the
composer says how long the run has worked, how its helpers stand and how many messages are queued (item 352), and its
parts open where they tell of. The details rise from the foot as a sheet with a grabber, and a drag of its head closes
it; the Agents tab's rows say each helper's state, model and what it does. Dot's computer opens over the whole page with
the page's address and the pill of who has control, and goes back to the panel when it closes. The sidebar is the list
of chats over the whole screen, Dot's name at its head and Settings, the search and a new chat at its foot. A wide
window keeps its bar as it was: the face, the name and the panel's switch alone; the line over the composer is there
too.

The model is a scripted one; the run's page is a local one in Dot's own headless Chrome.
"""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

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
from dotagent.store.models import NO_PICKS, RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-t3-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
_GOAL: str = "Find the cheapest flight to Berlin and check the baggage rules"
_HELPERS: tuple[str, ...] = ("Compare the fares of the airlines", "Check the baggage rules of Pegasus")
_QUEUED: str = "Also look at Sunday evening flights"
# The helpers wait in a command while the test looks; each wait that ends at the shell's limit is followed by another.
_WAITS: int = 40
_SETTLE_S: float = 30.0
# How far down a drag of the sheet's head takes it before it is let go: past the point where it closes.
_DRAG_PX: int = 160
# The page's address comes with the first frame of Dot's computer, which a still page can be slow to send on a busy
# machine (test_takeover.py waits as long).
_FRAME_WAIT_MS: int = 15_000
_PAGE: bytes = (
    b"<!doctype html><html><head><meta charset='utf-8'><title>Flights to Berlin</title></head>"
    b"<body style='font:16px system-ui;margin:24px'><h1>Flights to Berlin</h1><p>Pegasus, Friday 07:15, 96 EUR</p>"
    b"</body></html>"
)


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _site() -> Iterator[str]:
    """A local page for the run's browser to open: its address."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/flights"
    finally:
        server.shutdown()


def _turns(site: str) -> list[ScriptedTurn]:
    """The run opens the page, hands two goals to helpers in one step, and the helpers wait in a command."""
    wait: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 600"}),))
    return [
        ScriptedTurn(text="", calls=(ScriptedCall(tool="browser_open", arguments={"url": site}),)),
        ScriptedTurn(
            text="", calls=tuple(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": goal}) for goal in _HELPERS)
        ),
        *([wait] * _WAITS),
    ]


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path, site: str) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """A daemon on the Dot home ``home`` whose main model is the scripted one and whose browser is headless: its
    runtime, its run manager and its address."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns(site)))
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _box(locator: Locator) -> FloatRect:
    found: FloatRect | None = await locator.bounding_box()
    assert found is not None
    return found


async def _at_work(runtime: Runtime, run: RunRecord) -> None:
    """Wait until both helpers of `run` work and the run waits for them."""
    async with asyncio.timeout(_SETTLE_S):
        while True:
            helpers: list[RunRecord] = await runtime.store.list_thread_helpers(run.thread_id, 10)
            if len([helper for helper in helpers if helper.status == "running"]) == len(_HELPERS):
                return
            await asyncio.sleep(0.2)


async def test_a_phones_run_page_and_list_of_chats_follow_t3_codes_phone_app(own_home: Path, tmp_path: Path) -> None:
    with _site() as site:
        async with _served(own_home, site) as (runtime, manager, address), async_playwright() as playwright:
            run: RunRecord = await manager.start(
                _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
            )
            await _at_work(runtime, run)
            await runtime.store.add_queued(
                run.thread_id, _QUEUED, (), "task", None, None, "standard", None, NO_PICKS, None, None, None, "test"
            )
            title: str = (await runtime.store.get_thread(run.thread_id)).title
            port: int = int(address.rpartition(":")[2])
            browser: Browser = await playwright.webkit.launch(headless=True)
            try:
                schemes: tuple[Literal["light", "dark"], ...] = ("light", "dark")
                for scheme in schemes:
                    context = await browser.new_context(
                        **playwright.devices[_OWNERS_PHONE], color_scheme=scheme, reduced_motion="reduce"
                    )
                    page: Page = await context.new_page()
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{run.id}")
                    bar: Locator = page.locator("header.stagebar")
                    # The bar: the chat's title over the agent and the model, and the tools in one pill.
                    await expect(bar.locator(".stage-heading-title")).to_have_text(title)
                    await expect(bar.locator(".stage-heading-sub")).to_have_text("Dot · scripted")
                    await expect(bar.locator(".stage-name")).to_be_hidden()
                    helpers_button: Locator = bar.get_by_role("button", name="Show the helpers")
                    await expect(helpers_button).to_have_text(str(len(_HELPERS)))
                    screen_button: Locator = bar.get_by_role("button", name="Show Dot's computer")
                    await expect(screen_button).to_be_visible()
                    await expect(bar.get_by_role("button", name="Run details")).to_be_visible()
                    # The line over the composer: the run's time, its helpers and what is queued (item 352).
                    capsule: Locator = page.locator(".capsule")
                    await expect(capsule).to_contain_text("Working")
                    await expect(capsule).to_contain_text(f"{len(_HELPERS)} working")
                    await expect(page.locator(".queue")).to_contain_text(_QUEUED)
                    await expect(capsule.get_by_role("button", name="1 queued")).to_be_visible()
                    await page.screenshot(path=str(tmp_path / f"run-{scheme}.png"))

                    # The helpers' part opens the details as a sheet from the foot, on the Agents tab.
                    await capsule.get_by_role("button", name=f"{len(_HELPERS)} working").click()
                    sheet: Locator = page.locator(".inspector[data-mode='drawer']")
                    await expect(sheet).to_have_attribute("data-open", "true")
                    await expect(sheet.get_by_role("tab", name="Agents")).to_have_attribute("aria-selected", "true")
                    await expect(sheet.locator(".inspector-grabber")).to_be_visible()
                    height: int = await page.evaluate("window.innerHeight")
                    box: FloatRect = await _box(sheet)
                    assert round(box["y"] + box["height"]) == height, (box, height)
                    assert box["y"] > height * 0.05, (box, height)
                    rows: Locator = sheet.locator(".inspector-panel:not([hidden]) .agent-row")
                    await expect(rows).to_have_count(len(_HELPERS))
                    await expect(rows.locator(".agent-state")).to_have_text(["Running"] * len(_HELPERS))
                    await expect(rows.locator(".agent-model")).to_have_text(["scripted"] * len(_HELPERS))
                    await expect(rows.locator(".agent-doing")).to_have_text(["Now: shell · sleep 600"] * len(_HELPERS))
                    await page.screenshot(path=str(tmp_path / f"sheet-{scheme}.png"))
                    # A drag of its head down closes it.
                    head: FloatRect = await _box(sheet.locator(".inspector-head"))
                    x: float = head["x"] + head["width"] / 2
                    y: float = head["y"] + 8
                    await page.mouse.move(x, y)
                    await page.mouse.down()
                    await page.mouse.move(x, y + _DRAG_PX, steps=8)
                    await page.mouse.up()
                    await expect(sheet).to_have_attribute("data-open", "false")

                    # Dot's computer over the whole page: the page's address, and who has control.
                    await screen_button.click()
                    full: Locator = page.locator("dialog.computer-full")
                    await expect(full).to_be_visible()
                    await expect(full.locator(".computer-url")).to_have_text(site, timeout=_FRAME_WAIT_MS)
                    await expect(full.get_by_text("Dot has control")).to_be_visible()
                    await expect(full.get_by_role("button", name="Take over")).to_be_visible()
                    view: FloatRect = await _box(full)
                    assert (round(view["width"]), round(view["height"])) == (
                        await page.evaluate("window.innerWidth"),
                        height,
                    ), view
                    await page.screenshot(path=str(tmp_path / f"computer-{scheme}.png"))
                    await full.get_by_role("button", name="Close Dot's computer").click()
                    await expect(full).to_have_count(0)
                    await expect(page.locator("#inspector-panel-screen .computer")).to_have_count(1)

                    # The sidebar is the list of chats over the whole screen, with the search and a new chat at its
                    # foot.
                    await bar.get_by_role("button", name="Show sidebar").click()
                    sidebar: Locator = page.get_by_role("complementary", name="Sidebar")
                    await expect(sidebar.locator(".side-title")).to_have_text("Dot")
                    width: int = await page.evaluate("window.innerWidth")
                    assert round((await _box(sidebar))["width"]) == width
                    foot: Locator = sidebar.locator(".side-foot")
                    await expect(foot.get_by_role("searchbox", name="Search chats")).to_be_visible()
                    await expect(foot.get_by_role("link", name="New chat")).to_be_visible()
                    await expect(foot.get_by_role("link", name="Settings")).to_be_visible()
                    await page.screenshot(path=str(tmp_path / f"chats-{scheme}.png"))
                    await context.close()

                # A wide window keeps its bar: the face, the name and the panel's switch alone; the line over the
                # composer is there too while the run works (item 352).
                wide: Page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await wide.goto(ui_url("127.0.0.1", port, TOKEN))
                await wide.goto(f"{address}/#/runs/{run.id}")
                await expect(wide.locator(".stage-title .stage-name")).to_be_visible()
                await expect(wide.locator(".stage-heading")).to_be_hidden()
                await expect(wide.locator(".stage-tools button:visible")).to_have_count(1)
                await expect(wide.locator(".capsule")).to_contain_text("Working")
                # What the run does now: it waits on the call that started its helpers.
                await expect(wide.locator(".capsule-activity")).to_contain_text("delegate")
                await expect(wide.locator(".capsule").get_by_role("button", name="1 queued")).to_be_visible()
                # The curtain of a page opened on a chat (item 328) lifts once the chat shows.
                await expect(wide.locator(".waiting")).to_have_count(0)
                await wide.screenshot(path=str(tmp_path / "wide.png"), animations="disabled")
                await wide.close()
            finally:
                await browser.close()
