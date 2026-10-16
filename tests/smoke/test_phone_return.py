"""A phone that comes back to Dot finds it calm (item 328). A served daemon stands behind a proxy, which acts out the
phone's network: nothing passes while the phone is put away, and once it comes back the connections from before are
gone and new ones wait while the network wakes. The page runs in WebKit, as the owner's iPhone.

- **Back from the background, with the run at work meanwhile.** The chat stays still while the network wakes:
  - nothing on screen moves;
  - nothing says that the page reconnects;
  - the missed steps come once the network is there;
  - the rows that did not change stay the same rows.
- **A network that stays away.** The bar says so once the grace is over, and the chat still does not move.
- **Dot opened again after iOS let the page go.** The chat's bar and composer show at once, without "Connecting to the
  daemon…".
- **The browser's bar** takes the page's colour, light and dark.
- **A page that Dot's update left behind** says so without moving the chat, and loads again once it is put away.

The model is a scripted one.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI
from playwright.async_api import (
    APIResponse,
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    async_playwright,
    expect,
)
from starlette.types import ASGIApp, Receive, Scope, Send

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.delegate import DELEGATE_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-return-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
_GOAL: str = "Watch tonight's build and tell me what failed"
_HELPERS: tuple[str, ...] = ("Read the build log and list the failures", "Check which tests are flaky this week")
_EARLIER: tuple[str, ...] = ("Plan a weekend in Lisbon", "What is the capital of Finland?", "Draft the weekly report")
# The helpers work in short commands, each one other than the last, so that the run is never stopped for repeating
# itself; there are more of them than the test lasts.
_WAITS: int = 400
_SETTLE_S: float = 30.0
# How long the page is away for real (the run makes steps meanwhile), and how far its clock is moved on then: past the
# silence after which a stream is cut on the page's return (`BACK_SILENCE_MS` in api.js).
_AWAY_S: float = 5.0
_AWAY_MS: int = 25_000
# How long the phone's network takes to wake (a VPN such as Tailscale), and how long the page is watched after it is
# back.
_WAKE_S: float = 4.0
_WATCH_S: float = 12.0
# How long the page says nothing about a connection made again after its return (`RETURN_GRACE_MS` in state.js), and
# how long the network stays away in the case where it does not wake.
_GRACE_S: float = 8.0
_LOST_S: float = 15.0
# The missed steps show within this long of the network's return.
_CAUGHT_UP_S: float = 2.0
# How soon the bar and the composer of the chat stand when Dot is opened again.
_IN_PLACE_MS: int = 500
_WORDS: tuple[str, ...] = ("Reconnecting", "Connection lost", "Connecting")
# What the pages of an updated daemon are, as the status a page reads tells it.
_OTHER_PAGES: str = "0" * 16

_SIGHT: str = """(state) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => state === "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event(state === "hidden" ? "blur" : "focus"));
}"""

# Before the page goes away: what stands at three heights of the screen, the rows of the sidebar and of the helpers
# (marked, to tell them from rows made again), and the steps the helpers show. Every frame after `start` notes what
# moved, what says the page reconnects, and when the helpers' steps changed; every element added is noted with the part
# of the page it went into.
_RECORD: str = """(words) => {
  const log = { start: 0, moves: [], said: {}, added: {}, stepsAt: null };
  window.__calm = log;
  const steps = () => (document.querySelector(".helpers-body")?.textContent.match(/step \\d+/g) ?? []).join(",");
  log.steps = steps();
  log.watched = [0.3, 0.5, 0.7].map((f) => document.elementFromPoint(innerWidth / 2, innerHeight * f));
  log.tops = log.watched.map((node) => node.getBoundingClientRect().top);
  for (const row of document.querySelectorAll(".side-chat, .helpers-body > *")) row.__kept = true;
  const parts = [".sidebar", ".inspector", ".helpers", ".run-thread", ".capsule", ".stagebar", ".composer"];
  new MutationObserver((records) => {
    if (log.start === 0) return;
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        const part = parts.find((selector) => node.closest(selector) !== null) ?? "other";
        log.added[part] = (log.added[part] ?? 0) + 1;
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
  const frame = () => {
    if (log.start !== 0) {
      const at = Math.round(performance.now() - log.start);
      log.watched.forEach((node, index) => {
        const moved = Math.round(node.getBoundingClientRect().top - log.tops[index]);
        if (!node.isConnected) log.moves.push(`${at}ms #${index} gone`);
        else if (Math.abs(moved) > 1) log.moves.push(`${at}ms #${index} ${moved}px`);
      });
      const text = document.body.innerText;
      for (const word of words) {
        if (text.includes(word)) log.said[word] = log.said[word] ?? at;
      }
      if (log.stepsAt === null && steps() !== log.steps) log.stepsAt = at;
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}"""

# Where the chat's title and composer stand in `scope`, the curtain or the page: left, top, width and height.
_PLACES: str = """(scope) => {
  const place = (selector) => {
    const box = document.querySelector(`${scope} ${selector}`).getBoundingClientRect();
    return [box.left, box.top, box.width, box.height].map(Math.round);
  };
  return { title: place(".stage-heading-title"), composer: place(".composer") };
}"""

# What the watch found; `chats` are the chats whose rows did not change, each row kept or not.
_READ: str = """(chats) => {
  const log = window.__calm;
  return {
    moves: log.moves.slice(0, 12), said: log.said, added: log.added, steps_at: log.stepsAt,
    sidebar: chats.map((id) => document.querySelector(`.side-chat[data-chat="${id}"]`)?.__kept === true),
    helpers: [...document.querySelectorAll(".helpers-body > *")].map((row) => row.__kept === true),
  };
}"""


class Proxy:
    """A TCP proxy in front of the daemon, standing for the phone's network.

    - `sleep()` stops passing bytes on every connection, without closing any, as a phone put away leaves its network.
    - `wake(seconds)` is the network of a phone that comes back: every connection from before is closed, and new ones
      wait `seconds` before they pass.
    """

    def __init__(self) -> None:
        self.target: int = 0
        self.port: int = 0
        self.asleep: bool = False
        self.hold_until: float = 0.0
        self._open: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._carry, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    def close(self) -> None:
        assert self._server is not None
        self._server.close()

    def sleep(self) -> None:
        self.asleep = True

    def wake(self, seconds: float) -> None:
        for writer in list(self._open):
            writer.close()
        self._open.clear()
        self.asleep = False
        self.hold_until = asyncio.get_running_loop().time() + seconds

    async def _carry(self, client: asyncio.StreamReader, to_client: asyncio.StreamWriter) -> None:
        # A connection is one from before as soon as it is taken, so that `wake` closes one made while the network
        # slept and still waiting for the daemon too: the page's timers run on while it is put away.
        self._open.add(to_client)
        await asyncio.sleep(max(0.0, self.hold_until - asyncio.get_running_loop().time()))
        if to_client.is_closing():
            return
        upstream, to_upstream = await asyncio.open_connection("127.0.0.1", self.target)
        self._open.add(to_upstream)
        await asyncio.gather(self._pass(client, to_upstream), self._pass(upstream, to_client))

    async def _pass(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        """Pass the bytes of ``source`` to ``sink`` until either closes; while the network sleeps, they are dropped."""
        try:
            while chunk := await source.read(65_536):
                if self.asleep:
                    continue
                sink.write(chunk)
                await sink.drain()
        except ConnectionError:
            pass
        finally:
            sink.close()


class NotedPages:
    """Notes in ``asked`` the files of the pages (anything but the API) asked of the daemon."""

    def __init__(self, app: ASGIApp, asked: list[str]) -> None:
        self.app: ASGIApp = app
        self.asked: list[str] = asked

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not str(scope["path"]).startswith("/api"):
            self.asked.append(str(scope["path"]))
        await self.app(scope, receive, send)


def _turns() -> list[ScriptedTurn]:
    """The run hands two goals to helpers in one step, and the helpers work in short commands."""
    return [
        ScriptedTurn(
            text="", calls=tuple(ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": goal}) for goal in _HELPERS)
        ),
        *(
            ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": f"sleep 1; echo {n}"}),))
            for n in range(_WAITS)
        ),
    ]


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    yield home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[Runtime, RunManager, Proxy, list[str]]]:
    """A daemon on the Dot home ``home`` whose model is the scripted one, behind the proxy, which the pages reach it
    through: its runtime, its run manager, the proxy and the files of the pages asked of it."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns()))
    manager: RunManager = RunManager(runtime)
    proxy: Proxy = Proxy()
    await proxy.start()
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        proxy.target = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=proxy.port,
            started_at=time.time(),
            stopping=stopping,
        )
        asked: list[str] = []
        app: FastAPI = create_app(context)
        app.add_middleware(NotedPages, asked=asked)
        server: DotServer = DotServer(serving_config(app), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager, proxy, asked
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        proxy.close()
        await manager.shutdown()
        await close_runtime(runtime)


async def _at_work(runtime: Runtime, manager: RunManager) -> tuple[RunRecord, list[str]]:
    """Earlier chats that are over, then a run whose two helpers work: the run, once they do, and the earlier chats."""
    earlier_chats: list[str] = []
    for title in _EARLIER:
        thread: ThreadRecord = await runtime.store.create_thread(title, "test")
        earlier: RunRecord = await runtime.store.create_run(
            thread.id,
            title,
            "chat",
            SCRIPTED_MODEL,
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await runtime.store.finish_run(earlier.id, "done", "Done.")
        earlier_chats.append(thread.id)
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_SETTLE_S):
        while True:
            helpers: list[RunRecord] = await runtime.store.list_thread_helpers(run.thread_id, 10)
            if len([helper for helper in helpers if helper.status == "running"]) == len(_HELPERS):
                return run, earlier_chats
            await asyncio.sleep(0.2)


async def _phone(
    browser: Browser, playwright: Playwright, scheme: Literal["light", "dark"], workers: Literal["allow", "block"]
) -> BrowserContext:
    """The owner's phone in ``scheme``; ``workers`` blocks the service worker where a test answers for the daemon."""
    context: BrowserContext = await browser.new_context(
        **playwright.devices[_OWNERS_PHONE],
        color_scheme=scheme,
        reduced_motion="no-preference",
        service_workers=workers,
    )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    return context


async def _chat_page(context: BrowserContext, proxy: Proxy, run: RunRecord) -> Page:
    """The chat of ``run`` on the phone, signed in, with its helpers at work, and a clock that can be moved on."""
    page: Page = await context.new_page()
    await page.clock.install()
    await page.goto(ui_url("127.0.0.1", proxy.port, TOKEN))
    await page.goto(f"http://127.0.0.1:{proxy.port}/#/runs/{run.id}")
    await expect(page.locator(".capsule")).to_contain_text("Working", timeout=15_000)
    await expect(page.locator(".helpers-body")).to_contain_text("step", timeout=15_000)
    await page.wait_for_timeout(1_500)
    return page


async def _keep_pages_copy(page: Page) -> None:
    """Let the service worker take the page; loaded again, the page leaves it a copy of the pages, as a phone that opened
    Dot before has one."""
    await page.wait_for_function("() => navigator.serviceWorker.controller !== null")
    await page.reload()
    await page.wait_for_function("async () => (await caches.match('/js/main.js')) !== undefined")
    await expect(page.locator(".helpers-body")).to_contain_text("step", timeout=15_000)


async def _places(page: Page, scope: str) -> dict[str, list[int]]:
    """Where the chat's title and composer stand in ``scope`` (`_PLACES`): each one's left, top, width and height."""
    found: object = await page.evaluate(_PLACES, scope)
    assert isinstance(found, dict), found
    places: dict[str, list[int]] = {}
    for name, box in found.items():
        assert isinstance(box, list), found
        places[str(name)] = [int(side) for side in box]
    return places


async def _from_other_pages(route: Route) -> None:
    """The daemon's status, as an updated daemon that serves other pages tells it."""
    answer: APIResponse = await route.fetch()
    status: JsonObject = await answer.json()
    await route.fulfill(status=answer.status, json={**status, "pages": _OTHER_PAGES})


async def _away_and_back(page: Page, proxy: Proxy, wake_s: float) -> None:
    """Put the page away for a while (its network asleep, its clock moved on past a stream's silence), then bring it
    back into view on a network that takes ``wake_s`` to wake; the watch starts at the return."""
    await page.evaluate(_RECORD, list(_WORDS))
    await page.evaluate(_SIGHT, "hidden")
    proxy.sleep()
    await asyncio.sleep(_AWAY_S)
    await page.clock.fast_forward(_AWAY_MS)
    await page.evaluate("() => { window.__calm.start = performance.now(); }")
    proxy.wake(wake_s)
    await page.evaluate(_SIGHT, "visible")


async def test_a_phone_back_from_the_background_keeps_its_chat_still_while_the_network_wakes(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (runtime, manager, proxy, _asked), async_playwright() as playwright:
        run, earlier = await _at_work(runtime, manager)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await _phone(browser, playwright, "dark", "allow")
            page: Page = await _chat_page(context, proxy, run)
            # Back while the network wakes: the chat stays still and says nothing; the missed steps come once the
            # network is there; the rows that did not change are the rows that were there.
            await _away_and_back(page, proxy, _WAKE_S)
            await page.wait_for_timeout(_WATCH_S * 1000)
            await page.screenshot(path=str(tmp_path / "back.png"))
            back: JsonObject = await page.evaluate(_READ, earlier)
            assert back["moves"] == [], json.dumps(back)
            assert back["said"] == {}, json.dumps(back)
            steps_at: object = back["steps_at"]
            assert isinstance(steps_at, int) and steps_at <= (_WAKE_S + _CAUGHT_UP_S) * 1000, json.dumps(back)
            for kept in (back["sidebar"], back["helpers"]):
                assert isinstance(kept, list) and kept != [] and all(kept), json.dumps(back)

            # A network that stays away: the bar says so once the grace is over, not before, and the chat still does
            # not move; once the network is back, the bar says nothing again.
            await _away_and_back(page, proxy, _LOST_S)
            pill: Locator = page.locator(".pill:has(.live-dot)")
            await page.wait_for_timeout((_GRACE_S - 1) * 1000)
            await expect(pill).to_be_hidden()
            await expect(pill).to_be_visible(timeout=3_000)
            await page.screenshot(path=str(tmp_path / "lost.png"))
            await expect(pill).to_be_hidden(timeout=(_LOST_S - _GRACE_S + 6) * 1000)
            lost: JsonObject = await page.evaluate(_READ, earlier)
            assert lost["moves"] == [], json.dumps(lost)
            await context.close()
        finally:
            await browser.close()


async def test_dot_opened_again_shows_the_chat_in_place_and_the_bar_takes_the_pages_colour(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (runtime, manager, proxy, asked), async_playwright() as playwright:
        run, _earlier = await _at_work(runtime, manager)
        title: str = (await runtime.store.get_thread(run.thread_id)).title
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            for scheme in ("light", "dark"):
                context: BrowserContext = await _phone(browser, playwright, scheme, "allow")
                page: Page = await _chat_page(context, proxy, run)
                # The browser's bar takes the colour of the page's own.
                bars: object = await page.evaluate(
                    "() => [...document.querySelectorAll(\"meta[name='theme-color']\")].map((meta) => meta.content)"
                )
                ground: str = await page.evaluate(
                    "getComputedStyle(document.querySelector('.stagebar')).backgroundColor"
                )
                assert isinstance(bars, list) and len(bars) == 1 and _rgb(str(bars[0])) == _rgb(ground), (bars, ground)
                await _keep_pages_copy(page)
                await page.close()

                # Opened again after iOS let the page go, on a network that wakes: the chat's bar and composer stand
                # at once, where the chat's page has them once it is there, and nothing says that Dot connects.
                again: Page = await context.new_page()
                said: list[str] = []
                asked.clear()
                proxy.wake(_WAKE_S)
                started: float = time.monotonic()
                await again.goto(f"http://127.0.0.1:{proxy.port}/", wait_until="commit")
                opened_ms: float = (time.monotonic() - started) * 1000
                assert opened_ms < _IN_PLACE_MS, f"the page took {opened_ms:.0f} ms to open"
                await again.wait_for_timeout(_IN_PLACE_MS - opened_ms)
                await again.screenshot(path=str(tmp_path / f"again-{scheme}.png"))
                await expect(again.locator(".waiting .stage-heading-title")).to_have_text(title, timeout=100)
                await expect(again.locator(".waiting .composer textarea")).to_be_visible(timeout=100)
                waiting: dict[str, list[int]] = await _places(again, ".waiting")
                while time.monotonic() - started < _WAKE_S + 4:
                    text: str = await again.evaluate("document.body.innerText")
                    said.extend(word for word in _WORDS if word in text)
                    await again.wait_for_timeout(100)
                assert said == [], said
                await expect(again.locator(".helpers-body")).to_contain_text("step")
                await expect(again.locator(".waiting")).to_have_count(0)
                await again.screenshot(path=str(tmp_path / f"again-shown-{scheme}.png"))
                shown: dict[str, list[int]] = await _places(again, ".shell")
                # The composer and the title are where they were: the bar's tools are only on the page, in a pill as
                # tall as the way back (item 329), so only the title's width differs.
                assert waiting["composer"] == shown["composer"], (waiting, shown)
                assert waiting["title"][:2] == shown["title"][:2], (waiting, shown)
                assert await again.evaluate("() => scrollY + innerHeight >= document.documentElement.scrollHeight - 2")
                # The shell came from the copy: no script or style sheet was asked of the daemon.
                print(f"pages asked of the daemon at the opening ({scheme}): {asked}")
                assert [page for page in asked if page.startswith(("/js/", "/css/"))] == [], asked
                await context.close()
        finally:
            await browser.close()


async def test_a_page_that_an_update_left_behind_says_so_in_place_and_loads_again_once_put_away(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (runtime, manager, proxy, _asked), async_playwright() as playwright:
        run, _earlier = await _at_work(runtime, manager)
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            context: BrowserContext = await _phone(browser, playwright, "dark", "block")
            page: Page = await _chat_page(context, proxy, run)
            await page.evaluate("() => { window.dotStayed = true; }")
            await page.evaluate(_RECORD, list(_WORDS))
            await page.evaluate("() => { window.__calm.start = performance.now(); }")
            # The status says other pages until the page is put away.
            outdated: list[bool] = [True]

            async def status(route: Route) -> None:
                if outdated[0]:
                    await _from_other_pages(route)
                else:
                    await route.continue_()

            await page.route("**/api/status", status)
            banner = page.locator(".banner.update")
            await expect(banner).to_be_visible(timeout=20_000)
            await expect(banner).to_contain_text("Dot was updated since this page was loaded")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(tmp_path / "outdated.png"))
            calm: JsonObject = await page.evaluate(_READ, [])
            assert calm["moves"] == [], json.dumps(calm)

            # Put away, the page loads again, and comes back as the daemon's pages.
            async with page.expect_navigation():
                await page.evaluate(_SIGHT, "hidden")
                outdated[0] = False
            assert await page.evaluate("() => window.dotStayed === undefined")
            await expect(page.locator(".helpers-body")).to_contain_text("step", timeout=15_000)
            await expect(banner).to_be_hidden()
        finally:
            await browser.close()


def _rgb(colour: str) -> tuple[int, int, int]:
    """A CSS colour as ``rgb(r, g, b)`` or ``#rrggbb``, as its three channels."""
    hexed: re.Match[str] | None = re.fullmatch(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", colour.strip(), re.I)
    if hexed is not None:
        return (int(hexed[1], 16), int(hexed[2], 16), int(hexed[3], 16))
    channels: list[str] = re.findall(r"[\d.]+", colour)
    return (round(float(channels[0])), round(float(channels[1])), round(float(channels[2])))
