"""Settings › Browser (item 354): runs may be kept off the browser, and a page of Dot's own Chrome has the size and the
colour scheme chosen for it, from Chrome's next start.

The first test plays a run in the real loop with the scripted model and reads the tools it was offered; the second
drives a real headless Chrome on a throw-away profile against a page served on this Mac.
"""

import asyncio
import io
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.browser.look import PageLook
from dotagent.browser.modes import browser_data_dir
from dotagent.browser.profiles import DEFAULT_PROFILE, INCOGNITO_PROFILE, add_profile, profile_dir
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ScriptRun
from dotagent.config import AppConfig, BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.paths import config_path
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
TOKEN: str = "browser-settings-test-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_RUN_WAIT_S: float = 60.0
_SCRIPT_WAIT_S: float = 5.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
# What a page sees of itself: the size of its window, the colour scheme it is told to prefer and how large it is drawn.
_SEEN: str = (
    "({width: innerWidth, height: innerHeight, dark: matchMedia('(prefers-color-scheme: dark)').matches,"
    " light: matchMedia('(prefers-color-scheme: light)').matches, zoom: devicePixelRatio})"
)


class _Recorder:
    """The scripted model, keeping every request it was sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


async def _offered(enabled: bool) -> frozenset[str]:
    """The tools the first step of a task was offered, in a throw-away home whose runs may use the browser or not."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    model: _Recorder = _Recorder(ScriptedProvider(_PROVIDER, [_FINISH]))
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            runtime.browsers.set_enabled(enabled)
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Look at the page", "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask",
                    False, (),
                )  # fmt: skip
                async with asyncio.timeout(_RUN_WAIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    assert record.status == "done", record
    first: ChatRequest = next(request for request in model.requests if request.session_key == record.thread_id)
    return frozenset(spec.name for spec in first.tools)


async def test_runs_are_offered_no_browser_tool_while_the_browser_is_off() -> None:
    on: frozenset[str] = await _offered(True)
    off: frozenset[str] = await _offered(False)
    browser: frozenset[str] = frozenset(name for name in on if name.startswith("browser_"))
    assert "browser_open" in browser, sorted(on)
    assert not any(name.startswith("browser_") for name in off), sorted(off)
    # Nothing else goes with them.
    assert off == on - browser


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = b"<!doctype html><title>Look</title><h1>Look</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


async def _seen(session: BrowserSession, site: str) -> dict[str, object]:
    await session.open_url("look", site)
    seen: ScriptRun = await session.run_script("look", _SEEN, _SCRIPT_WAIT_S)
    assert seen.end == "value", seen
    value: object = json.loads(seen.text)
    assert isinstance(value, dict), seen
    return value


@pytest.fixture(scope="module")
def headless(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    """The defaults of ``[browser]``, with a Chrome that shows no window."""
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest.mark.browser
async def test_a_page_has_the_size_and_the_colour_scheme_chosen(
    site: str, headless: BrowserConfig, tmp_path: Path
) -> None:
    server: ServerConfig = load_default_config().server
    session: BrowserSession = BrowserSession(
        headless, server, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        await session.set_look(
            PageLook(fit_window=False, viewport_width=1024, viewport_height=768, appearance="dark", zoom=100)
        )
        dark: dict[str, object] = await _seen(session, site)
        assert (dark["width"], dark["height"], dark["dark"]) == (1024, 768, True), dark
        await session.set_look(
            PageLook(fit_window=False, viewport_width=390, viewport_height=844, appearance="light", zoom=100)
        )
        light: dict[str, object] = await _seen(session, site)
        assert (light["width"], light["height"], light["light"]) == (390, 844, True), light
        # Drawn at 125 %, a page of 1280 by 860 has the room of 1024 by 688 (item 358).
        await session.set_look(
            PageLook(fit_window=False, viewport_width=1280, viewport_height=860, appearance="light", zoom=125)
        )
        zoomed: dict[str, object] = await _seen(session, site)
        assert (zoomed["width"], zoomed["height"], zoomed["zoom"]) == (1024, 688, 1.25), zoomed
        # The model is told that size, and a pixel of its screenshot is one of the page's, for click_at.
        drawn = await session.screenshot("look")
        with Image.open(io.BytesIO(drawn.data)) as picture:
            assert (drawn.width, drawn.height, *picture.size) == (1024, 688, 1024, 688)
        # Filling its window, a page has the window's size, and the model is told that size.
        await session.set_look(
            PageLook(fit_window=True, viewport_width=390, viewport_height=844, appearance="light", zoom=100)
        )
        filled: dict[str, object] = await _seen(session, site)
        assert filled["width"] != 390, filled
        shot = await session.screenshot("look")
        assert (shot.width, shot.height) == (filled["width"], filled["height"]), (shot.width, shot.height, filled)
    finally:
        await session.aclose()


@asynccontextmanager
async def _served(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """The daemon's interface on ``runtime``, at the address it yields."""
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


@asynccontextmanager
async def _daemon(turns: Sequence[ScriptedTurn]) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon in a throw-away home whose scripted model plays ``turns``: its manager, and the address it serves on."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, _Recorder(ScriptedProvider(_PROVIDER, list(turns))))
            manager: RunManager = RunManager(runtime)
            try:
                async with _served(runtime, manager) as address:
                    yield manager, address
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    return page


def _kept() -> str:
    return config_path().read_text(encoding="utf-8")


async def _changed(page: Page, path: str, change: Callable[[], Awaitable[object]]) -> None:
    """Make ``change`` on the page and wait for the daemon's answer to the change it asks of ``path`` (a POST or a
    DELETE), which the daemon gives once the change is made and kept."""
    async with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method != "GET"
    ) as answer:
        await change()
    answered = await answer.value
    assert answered.ok, await answered.text()


@pytest.mark.browser
async def test_settings_browser_sets_the_browser_rows_and_keeps_them(tmp_path: Path) -> None:
    async with _daemon(()) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, address, width, scheme)
                await page.goto(f"{address}/#/settings/browser")
                # The rows stand in four groups, each named over its settings (item 378).
                await expect(page.locator(".setting-group-name")).to_have_text(
                    ["Where it browses", "The page it opens", "While a run works", "Developer"]
                )
                allowed: Locator = page.get_by_role("switch", name="Runs may use the browser")
                await expect(allowed).to_have_attribute("aria-checked", "true")
                await _changed(page, "/api/browser/enabled", allowed.click)
                await expect(allowed).to_have_attribute("aria-checked", "false")
                assert "enabled = false\n" in _kept()
                size: Locator = page.get_by_role("combobox", name="Size of a page")
                await expect(size).to_have_value("1280x860")
                await _changed(page, "/api/browser/look", partial(size.select_option, "390x844"))
                await expect(size).to_have_value("390x844")
                assert all(part in _kept() for part in ("viewport_width = 390\n", "viewport_height = 844\n"))
                await _changed(page, "/api/browser/look", partial(size.select_option, "window"))
                await expect(size).to_have_value("window")
                assert "fit_window = true\n" in _kept()
                looks: Locator = page.get_by_role("group", name="Appearance of pages")
                await _changed(page, "/api/browser/look", looks.get_by_role("button", name="Dark").click)
                await expect(looks.get_by_role("button", name="Dark")).to_have_attribute("aria-pressed", "true")
                assert 'appearance = "dark"\n' in _kept()
                zoom: Locator = page.get_by_role("combobox", name="Zoom of pages")
                await expect(zoom).to_have_value("100")
                await _changed(page, "/api/browser/look", partial(zoom.select_option, "125"))
                await expect(zoom).to_have_value("125")
                assert "zoom = 125\n" in _kept()
                shown: Locator = page.get_by_role("switch", name="Show Dot's computer when a run opens the browser")
                await expect(shown).to_have_attribute("aria-checked", "true")
                # Each row stays inside the window, on a phone too.
                for row in (allowed, size, zoom, shown):
                    assert await page.locator(".setting", has=row).evaluate(
                        "(row) => row.getBoundingClientRect().right <= window.innerWidth"
                    )
                await page.screenshot(path=tmp_path / f"settings-browser-{width}-{scheme}.png", full_page=True)
                # Back as they were, for the next width.
                await _changed(page, "/api/browser/enabled", allowed.click)
                await _changed(page, "/api/browser/look", partial(size.select_option, "1280x860"))
                await _changed(page, "/api/browser/look", looks.get_by_role("button", name="System").click)
                await _changed(page, "/api/browser/look", partial(zoom.select_option, "100"))
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_a_run_that_opens_its_browser_shows_its_page_beside_the_chat(site: str, tmp_path: Path) -> None:
    turns: tuple[ScriptedTurn, ...] = (
        ScriptedTurn(text="", calls=(ScriptedCall(tool="browser_open", arguments={"url": site}),)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": "Go on?"}),)),
    )
    async with _daemon(turns) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await manager.start(
            "Look at the page", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
        )
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            wide: Page = await _page(browser, address, 1280, "light")
            await wide.goto(f"{address}/#/runs/{run.id}")
            # The run waits on its question with its page open: the panel shows it, beside the chat, by itself.
            await expect(wide.locator(".approval").first).to_be_visible(timeout=30_000)
            panel: Locator = wide.locator(".inspector")
            await expect(panel.get_by_role("tab", name="Screen")).to_have_attribute("aria-selected", "true")
            await expect(wide.locator("[data-inspector='on']")).to_have_count(1)
            await wide.screenshot(path=tmp_path / "shown-1280-light.png")
            # Nothing of the reader's choices changed: the next page follows them again.
            assert await wide.evaluate("localStorage.getItem('dot.panelTab')") is None
            # The panel's switch hides it, as it came.
            details: Locator = wide.get_by_role("button", name="Run details")
            await expect(details).to_have_attribute("aria-pressed", "true")
            await details.click()
            await expect(wide.locator("[data-inspector='on']")).to_have_count(0)
            # Turned off in Settings › Browser, the next look at the run leaves the panel as it was.
            await wide.goto(f"{address}/#/settings/browser")
            await wide.get_by_role("switch", name="Show Dot's computer when a run opens the browser").click()
            await wide.goto(f"{address}/#/runs/{run.id}")
            await expect(wide.locator(".approval").first).to_be_visible()
            await expect(wide.locator("[data-inspector='on']")).to_have_count(0)
            await wide.close()
            # A phone keeps its button, and its drawer stays closed.
            phone: Page = await _page(browser, address, 390, "dark")
            await phone.goto(f"{address}/#/runs/{run.id}")
            await expect(phone.locator(".approval").first).to_be_visible()
            await expect(phone.get_by_role("button", name="Show Dot's computer")).to_be_visible()
            await expect(phone.locator(".inspector[data-open='true']")).to_have_count(0)
            await phone.screenshot(path=tmp_path / "phone-390-dark.png")
            await phone.close()
        finally:
            await browser.close()


async def _chrome_worked_in(folder: Path) -> bool:
    """Whether a Chrome worked on the profile in ``folder``: its Local State, which only Chrome writes (Dot writes the
    zoom into the profile's preferences before Chrome starts, item 358)."""
    return await asyncio.to_thread((folder / "Local State").is_file)


@pytest.mark.browser
async def test_dots_chrome_works_on_the_profile_in_use_and_incognito_keeps_nothing(
    site: str, headless: BrowserConfig
) -> None:
    server: ServerConfig = load_default_config().server
    with scratch_home() as home, eval_environment(home, {}):
        session: BrowserSession = BrowserSession(
            headless, server, browser_data_dir("own", DEFAULT_PROFILE), home / "downloads", no_runs_at_work
        )
        try:
            await session.open_url("profiles", site)
            assert await _chrome_worked_in(profile_dir(DEFAULT_PROFILE))
            # An added profile is a folder of its own, which Chrome fills when it works on it.
            await asyncio.to_thread(add_profile, "Work")
            await session.use_profile("Work")
            assert session.profile_dir == profile_dir("Work")
            await session.open_url("profiles", site)
            assert await _chrome_worked_in(profile_dir("Work"))
            await session.use_profile(INCOGNITO_PROFILE)
            await session.open_url("profiles", site)
            assert await _chrome_worked_in(profile_dir(INCOGNITO_PROFILE))
            # Its Chrome closed, Incognito keeps nothing; the other profiles keep their folders.
            await session.use_profile(DEFAULT_PROFILE)
            assert not await asyncio.to_thread(profile_dir(INCOGNITO_PROFILE).exists)
            assert await _chrome_worked_in(profile_dir("Work"))
        finally:
            await session.aclose()


@pytest.mark.browser
async def test_settings_browser_adds_uses_and_deletes_a_profile(tmp_path: Path) -> None:
    async with _daemon(()) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, address, width, scheme)
                await page.goto(f"{address}/#/settings/browser")
                profiles: Locator = page.get_by_role("list", name="Profiles")
                await expect(profiles.get_by_role("listitem")).to_have_count(2)
                await expect(profiles.locator("[data-profile='default']")).to_contain_text("In use")
                await page.get_by_role("button", name="Add profile").click()
                await page.get_by_role("textbox", name="Name of the new profile").fill("Work")
                await _changed(page, "/api/browser/profiles", page.get_by_role("button", name="Add", exact=True).click)
                work: Locator = profiles.locator("[data-profile='Work']")
                await expect(work).to_be_visible()
                await _changed(page, "/api/browser/profile", page.get_by_role("button", name="Use Work for runs").click)
                await expect(work).to_contain_text("In use")
                assert 'profile = "Work"\n' in _kept()
                # The profile in use can be cleared, not deleted.
                await expect(page.get_by_role("button", name="Delete Work")).to_have_count(0)
                await page.screenshot(path=tmp_path / f"profiles-{width}-{scheme}.png", full_page=True)
                await _changed(
                    page, "/api/browser/profile", page.get_by_role("button", name="Use Default for runs").click
                )
                await page.get_by_role("button", name="Delete Work").click()
                await _changed(
                    page,
                    "/api/browser/profiles/Work",
                    page.get_by_role("dialog").get_by_role("button", name="Delete").click,
                )
                await expect(work).to_have_count(0)
                assert 'profile = "default"\n' in _kept()
                await page.close()
        finally:
            await browser.close()
