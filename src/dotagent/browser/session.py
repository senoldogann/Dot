"""The browser connector: one Chrome for Dot, and for every conversation thread a set of tabs of its own.

The Chrome is either Dot's own (``mode = "own"``: started here on Dot's profile) or the one the user already has open
(``mode = "attach"``: connected to over the debugging port that Chrome 144 and later offer, see ``attach``). In the open
Chrome Dot only works in the tabs it opened itself, never touches the others, and leaves Chrome running when it lets go.

Operations of one thread are serialized by the thread's lock; threads run in parallel. Everything the model is shown is
produced here as value types (``PageView``, ``ActOutcome``, ``TextView``); rendering and policy live elsewhere.
"""

import asyncio
import base64
import contextlib
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal
from urllib.parse import urldefrag

from playwright.async_api import Browser, BrowserContext, CDPSession, Page, Playwright, Response, async_playwright
from playwright.async_api import Error as PlaywrightError
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, DebugEndpoint, endpoint_url, read_debug_endpoint, redact_endpoint
from dotagent.browser.devtools import devtools_switches, port_is_taken, port_taken
from dotagent.browser.driver import (
    bring_to_front,
    capture_lines,
    go_back,
    install_consent_handler,
    keep_focused,
    measure_viewport,
    navigate,
    page_title,
    run_script,
    run_step,
    screenshot_jpeg,
    scroll_page,
    upload_files,
    visible_text,
    wait_dom_quiet,
    wait_for_text,
)
from dotagent.browser.keychain import KeychainMode, keychain_switches, playwright_defaults_to_drop, read_keychain_mode
from dotagent.browser.pages import Tab, ThreadTabs, add_note, attach_handlers
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.render import describe_step
from dotagent.browser.safety import check_browser_url
from dotagent.browser.snapshot import (
    SnapshotLine,
    count_refs,
    diff_views,
    find_lines,
    line_for_ref,
    link_lines,
    overlay_lines,
    prefer_diff,
    render_diff,
    render_lines,
    squeeze_text,
    viewport_filter,
)
from dotagent.browser.types import (
    ActOutcome,
    Attachment,
    FindResult,
    Frame,
    PageView,
    Screenshot,
    ScriptRun,
    SecretFieldCheck,
    Step,
    StepReport,
    TabSummary,
    TextView,
    TextWait,
    Viewport,
)
from dotagent.browser.upstream import ignore_repeated_frame_detach
from dotagent.browser.window import chrome_to_watch, focused_app, front_pid, give_focus_back, return_front
from dotagent.config import BrowserConfig, ServerConfig
from dotagent.core.errors import ApprovalRejected, BrowserError, HandedOff, PolicyDenied
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import JsonObject
from dotagent.desktop.types import AppInfo
from dotagent.store.models import BrowserMode

_log = get_logger("dotagent.browser.session")

_REAP_INTERVAL_S: float = 60.0
# The screencast of a tab (``screencast``): JPEG quality, the longest edge, and how often a silent cast says so.
_CAST_QUALITY: int = 50
_CAST_MAX_EDGE: int = 1280
_CAST_POLL_S: float = 1.0
_POPUP_WAIT_S: float = 1.0
# A page that shows fewer elements than this right after loading is probably still rendering.
_MIN_REFS_AFTER_LOAD: int = 3
_LATE_QUIET_MS: int = 300
_LATE_MAX_MS: int = 2500
_STILL_LOADING: str = "the page is still loading; what is shown may be incomplete"
_BLANK_URLS: frozenset[str] = frozenset({"about:blank", "chrome://newtab/", "chrome://new-tab-page/"})
_TAB_MARK_BYTES: int = 6
_LAUNCH_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]
# Playwright's own switches that Dot's Chrome starts without: the bar that says software controls it, and
# --disable-extensions, which would keep the extensions installed in its profile from loading (item 264).
_PLAYWRIGHT_SWITCHES_LEFT_OUT: list[str] = ["--enable-automation", "--disable-extensions"]
_LAUNCH_ERROR_CHARS: int = 900
# A page that answered with a bot wall gets this long to pass its own check (a challenge page reloads itself) before
# it is read as it stands: 202 is DuckDuckGo's check, the others are the usual walls.
_CHALLENGE_STATUS: frozenset[int] = frozenset({202, 403, 429, 503, 999})
_CHALLENGE_WAIT_MS: int = 8000
_DRIVER_LOST: str = "Connection closed while reading from the driver"
# Chrome exits by itself a few seconds after the driver that started it died.
_CHROME_EXIT_WAIT_S: float = 10.0
_CHROME_EXIT_POLL_S: float = 0.2


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


def driver_lost(error: BaseException) -> bool:
    """Whether ``error`` says that the Playwright driver process died. Playwright raises that as a plain ``Exception``
    (playwright/_impl/_transport.py), so the type and the message together identify it."""
    return type(error) is Exception and _DRIVER_LOST in str(error)


def browser_gone(error: Exception) -> bool:
    """Whether ``error`` is Playwright reporting that its target or its driver is gone."""
    return isinstance(error, PlaywrightError) or driver_lost(error)


PopupFate = Literal["closed", "gone"]


def popup_fate(error: Exception, closed: bool) -> PopupFate | None:
    """What became of a popup Dot could not adopt (item 57): it closed itself before Dot finished registering it
    ("closed": a sign-in callback that redirects and closes, the common case), the browser is gone ("gone"), or neither,
    so the error is a bug and is raised."""
    if closed:
        return "closed"
    if browser_gone(error):
        return "gone"
    return None


def closed_popup_note(url: str) -> str:
    """The note the opener carries when a popup it opened closed itself before the model could see it."""
    return f"a tab opened and closed itself ({truncate_middle(url, _LAUNCH_ERROR_CHARS)}); there is no tab to switch to"


def closed_tab_stop(url: str) -> str:
    """Why a batch stopped when the tab it worked in closed: the page did that, and the thread's current tab follows."""
    return f"the tab was closed by the page (it was at {truncate_middle(url, _LAUNCH_ERROR_CHARS)}); the tab now current is shown below"


_CLOSED_STEP_DETAIL: str = "the page closed under this step"


def connection_broke(error: Exception) -> BrowserError:
    return BrowserError(
        "the connection to the browser broke; it restarts on the next call, so repeat the step",
        {"error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)},
    )


@dataclass(frozen=True, slots=True)
class _Watch:
    """What a step could change besides the page content: the URL, the tab set and interrupting events."""

    url: str
    tabs: int
    events: int
    closed: bool


@dataclass(frozen=True, slots=True)
class _Opened:
    """A browser context that was just opened, and the pages in it that are Dot's to reuse (a fresh Chrome starts with
    one blank tab; the open Chrome of the user has none that Dot may take)."""

    context: BrowserContext
    spare_pages: tuple[Page, ...]


@dataclass(frozen=True, slots=True)
class _Capture:
    lines: tuple[SnapshotLine, ...]
    rendered: str
    refs: int
    offscreen: int
    overlay: str
    overlays: int


def describe_surprise(before: _Watch, after: _Watch) -> str | None:
    """Why a batch should stop after a step, or ``None`` when the page behaved as expected."""
    if after.closed:
        return closed_tab_stop(after.url)
    if after.tabs > before.tabs:
        return "a new tab opened; use browser_switch_tab to work in it"
    if after.tabs < before.tabs:
        return "a tab was closed"
    if after.events != before.events:
        return "a dialog, download or file chooser appeared (see notes)"
    if urldefrag(after.url).url != urldefrag(before.url).url:
        return f"the page navigated to {after.url}"
    return None


def _error_detail(error: BrowserError) -> str:
    covered_by: object = error.context.get("covered_by")
    if isinstance(covered_by, str) and covered_by:
        return (
            f"{error.message}: something lies in front of the element and takes the click ({covered_by}). Answer or "
            "close it first (its Close, X, Accept, Reject or No thanks button, or press Escape), then try again"
        )
    raw: object = error.context.get("error")
    return f"{error.message}: {raw}" if isinstance(raw, str) and raw else error.message


@dataclass(frozen=True)
class RenderedDocument:
    """A page read in a scratch tab: the address it ended on, the status of its last main response and its HTML after
    JavaScript ran."""

    url: str
    status: int
    html: str


def _note_main_status(page: Page, response: Response, statuses: list[int]) -> None:
    """Keep the status of each main-frame document response, so a bot wall that reloads into the page is read right."""
    if response.frame == page.main_frame and response.request.is_navigation_request():
        statuses.append(response.status)


async def _close_given_up(launching: asyncio.Task[BrowserContext]) -> None:
    """Close the Chrome of a launch that was given up, once the launch is over; a launch that failed started none."""
    try:
        context: BrowserContext = await launching
    except PlaywrightError as error:
        _log.info("browser_launch_given_up_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))
        return
    await context.close()
    _log.info("browser_launch_given_up_closed")


class BrowserSession:
    """Owns the connection to Chrome. Every public method takes the id of the thread whose tabs it works on.

    ``profile_dir`` is the user data directory that Dot's own Chrome runs on or, in attach mode, the data directory of
    the Chrome the user has open. Chrome and the Playwright driver can die at any moment. A call that meets the loss
    fails with a ``BrowserError`` and the next call starts or connects again; nothing is retried behind the model's
    back, because a click must not repeat."""

    def __init__(self, config: BrowserConfig, server: ServerConfig, profile_dir: Path, downloads_dir: Path) -> None:
        self._config: BrowserConfig = config
        # Whose interface the browser never opens (:func:`check_browser_url`).
        self._server: ServerConfig = server
        self._profile: Path = profile_dir
        self._downloads: Path = downloads_dir
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._launch_lock: asyncio.Lock = asyncio.Lock()
        self._tab_lock: asyncio.Lock = asyncio.Lock()
        self._threads: dict[str, ThreadTabs] = {}
        self._initial_pages: list[Page] = []
        self._adopting: set[asyncio.Task[None]] = set()
        self._reaper: asyncio.Task[None] | None = None
        self._closed: bool = False
        # The front of the Mac around Dot's own Chrome (item 118): Chrome's process, the application that had the front
        # when a thread last began to act, and the checks under way that give the front back after a page's own tab.
        self._chrome_pid: int | None = None
        self._front_before: int | None = None
        self._front_returns: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ lifecycle

    async def _driver(self) -> Playwright:
        if self._playwright is None:
            ignore_repeated_frame_detach()
            self._playwright = await async_playwright().start()
        return self._playwright

    async def _ensure_context(self) -> BrowserContext:
        async with self._launch_lock:
            if self._closed:
                raise BrowserError("the browser session was closed", {})
            if self._context is not None:
                return self._context
            try:
                opened: _Opened = await self._open()
            except Exception as error:
                lost: Playwright | None = self._playwright
                if lost is None or not driver_lost(error):
                    raise
                await self._discard_driver(lost)
                raise connection_broke(error) from error
            return self._arm(opened)

    async def _open(self) -> _Opened:
        match self._config.mode:
            case "own":
                return await self._launch_own()
            case "attach":
                return await self._attach()

    async def _launch_own(self) -> _Opened:
        """Start Dot's own Chrome on its own profile, taking DevTools connections on ``[browser] debug_port`` when
        one is set (item 265): a port another program holds stops the start, so that a script told the address does not
        reach that program instead."""
        config: BrowserConfig = self._config
        holder: int | None = await asyncio.to_thread(profile_holder_pid, self._profile)
        if holder is not None:
            raise BrowserError(
                "the Dot browser profile is open in another process; close that Chrome window and try again",
                {"profile": str(self._profile), "pid": holder},
            )
        if config.debug_port and await asyncio.to_thread(port_is_taken, config.debug_port):
            raise port_taken(config.debug_port)
        keychain: KeychainMode = await asyncio.to_thread(read_keychain_mode, self._profile)
        playwright: Playwright = await self._driver()
        # With the window kept in the background (item 97), whoever has the focus now gets it back after the launch.
        earlier: AppInfo | None = (
            None if config.headless or config.raise_window else await asyncio.to_thread(focused_app)
        )
        launching: asyncio.Task[BrowserContext] = asyncio.create_task(
            playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._profile),
                channel=config.channel,
                headless=config.headless,
                viewport={"width": config.viewport_width, "height": config.viewport_height},
                accept_downloads=True,
                # Playwright adds --no-sandbox unless asked not to; Chrome then shows its "unsupported flag" banner.
                chromium_sandbox=True,
                # The cookies of the profile must be read with the key they were written with (see keychain.py).
                ignore_default_args=[*_PLAYWRIGHT_SWITCHES_LEFT_OUT, *playwright_defaults_to_drop(keychain)],
                args=[*_LAUNCH_ARGS, *keychain_switches(keychain), *devtools_switches(config.debug_port)],
            )
        )
        try:
            context: BrowserContext = await asyncio.shield(launching)
        except asyncio.CancelledError:
            # Given up while Chrome starts (its run stopped): the launch goes on in the driver, and the Chrome it starts
            # would keep the profile locked with nothing to close it.
            await _close_given_up(launching)
            raise
        except PlaywrightError as error:
            raise BrowserError(
                "Chrome could not be started; install Google Chrome or change browser.channel",
                {
                    "channel": config.channel,
                    "headless": config.headless,
                    "profile": str(self._profile),
                    "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
                },
            ) from error
        _log.info(
            "browser_started",
            mode=config.mode,
            channel=config.channel,
            headless=config.headless,
            raise_window=config.raise_window,
            debug_port=config.debug_port,
            profile=str(self._profile),
            keychain=keychain,
        )
        # Until the session holds the context, nothing else would close this Chrome: one that is left running keeps
        # the profile locked, and every later launch is refused.
        try:
            self._chrome_pid = None if config.headless else await asyncio.to_thread(chrome_to_watch, self._profile)
            if earlier is not None:
                await asyncio.to_thread(give_focus_back, earlier)
        except BaseException:
            self._chrome_pid = None
            await context.close()
            raise
        return _Opened(context=context, spare_pages=tuple(context.pages))

    async def _attach(self) -> _Opened:
        """Connect to the Chrome the user has open and work in the browser context it already has. Chrome asks the
        user to click Allow; the connection waits for the answer for ``attach_timeout_s``."""
        config: BrowserConfig = self._config
        endpoint: DebugEndpoint = await asyncio.to_thread(read_debug_endpoint, self._profile)
        playwright: Playwright = await self._driver()
        try:
            browser: Browser = await playwright.chromium.connect_over_cdp(
                endpoint_url(endpoint),
                timeout=_ms(config.attach_timeout_s),
                # Chrome runs on this Mac, and Playwright must not put its own emulation or download handling on it.
                is_local=True,
                no_defaults=True,
            )
        except PlaywrightError as error:
            raise BrowserError(
                "Chrome did not accept the connection; keep a Chrome window open, click Allow when Chrome asks, and "
                f"check that remote debugging is on ({INSPECT_PAGE})",
                {
                    "data_dir": str(self._profile),
                    "port": endpoint.port,
                    "waited_s": config.attach_timeout_s,
                    "error": truncate_middle(redact_endpoint(str(error)), _LAUNCH_ERROR_CHARS),
                },
            ) from error
        if not browser.contexts:
            await browser.close()
            raise BrowserError(
                "Chrome has no browser context to open tabs in", {"data_dir": str(self._profile), "port": endpoint.port}
            )
        context: BrowserContext = browser.contexts[0]
        browser.on("disconnected", self._on_browser_disconnected)
        self._browser = browser
        _log.info("browser_attached", port=endpoint.port, version=browser.version, tabs=len(context.pages))
        return _Opened(context=context, spare_pages=())

    def _arm(self, opened: _Opened) -> BrowserContext:
        config: BrowserConfig = self._config
        context: BrowserContext = opened.context
        context.set_default_timeout(_ms(config.action_timeout_s))
        context.set_default_navigation_timeout(_ms(config.navigation_timeout_s))
        context.on("page", self._on_page)
        context.on("close", self._on_context_closed)
        self._initial_pages = list(opened.spare_pages)
        self._context = context
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_idle())
        return context

    def _on_context_closed(self, context: BrowserContext) -> None:
        if self._closed or context is not self._context:
            return
        _log.warning("browser_context_closed", threads=len(self._threads))
        self._forget_tabs()
        self._context = None

    def _on_browser_disconnected(self, browser: Browser) -> None:
        """Chrome quit, or the connection to it broke. A later connection must not be mistaken for this one."""
        if self._closed or browser is not self._browser:
            return
        _log.warning("browser_disconnected", threads=len(self._threads))
        self._forget_tabs()
        self._context = None
        self._browser = None

    def _forget_tabs(self) -> None:
        self._initial_pages = []
        self._chrome_pid = None
        for thread in self._threads.values():
            thread.tabs.clear()
            thread.active = 0

    async def _discard_driver(self, lost: Playwright) -> None:
        """Forget the dead driver ``lost`` and, for Dot's own Chrome, wait for that Chrome to exit. The caller holds the
        launch lock, so no browser starts on the profile meanwhile. Of several calls that meet the same loss only the
        first has work to do: the others find the driver already forgotten. A Chrome that outlives the wait is reported
        by the next ``_ensure_context`` as the holder of the profile. The Chrome of the user does not exit with the
        driver, so there is nothing to wait for."""
        if self._playwright is not lost:
            return
        _log.warning("browser_driver_lost", threads=len(self._threads))
        self._playwright = None
        self._context = None
        self._browser = None
        self._forget_tabs()
        await lost.stop()
        if self._config.mode == "attach":
            return
        for _ in range(int(_CHROME_EXIT_WAIT_S / _CHROME_EXIT_POLL_S)):
            if await asyncio.to_thread(profile_holder_pid, self._profile) is None:
                return
            await asyncio.sleep(_CHROME_EXIT_POLL_S)

    @property
    def mode(self) -> BrowserMode:
        """Which Chrome this session works in now: Dot's own or the one the user has open."""
        return self._config.mode

    @property
    def config(self) -> BrowserConfig:
        return self._config

    @property
    def profile_dir(self) -> Path:
        return self._profile

    @property
    def connected(self) -> bool:
        """Whether a Chrome is started or connected at the moment (one starts on the first call that needs it)."""
        return self._context is not None

    def set_raise_window(self, raise_window: bool) -> None:
        """Whether the window is raised while a run works, from the next call on; Chrome is not restarted."""
        self._config = self._config.model_copy(update={"raise_window": raise_window})
        _log.info("browser_raise_window", raise_window=raise_window)

    async def set_debug_port(self, port: int) -> None:
        """The port Dot's own Chrome takes DevTools connections on (item 265; 0 for none), from its next start: Dot's
        own Chrome, when it runs on another port, is closed, so that the next call starts it on this one. Refused as
        :meth:`check_debug_port` refuses; nothing changes then."""
        async with self._launch_lock:
            await self._refuse_debug_port(port)
            if port == self._config.debug_port:
                return
            if self._config.mode == "own":
                await self._let_go()
            self._config = self._config.model_copy(update={"debug_port": port})
            _log.info("browser_debug_port", mode=self._config.mode, debug_port=port)

    async def check_debug_port(self, port: int) -> None:
        """Raise the error :meth:`set_debug_port` would refuse ``port`` with, changing nothing: the session was closed,
        a thread is in the middle of a step (as for a switch), or another program holds the port."""
        async with self._launch_lock:
            await self._refuse_debug_port(port)

    async def _refuse_debug_port(self, port: int) -> None:
        if self._closed:
            raise BrowserError("the browser session was closed", {})
        if port == self._config.debug_port:
            return
        if port and await asyncio.to_thread(port_is_taken, port):
            raise port_taken(port)
        busy: list[JsonValue] = [thread_id for thread_id, thread in self._threads.items() if thread.lock.locked()]
        if busy:
            raise BrowserError("the browser is in use; wait for the running work to finish", {"threads": busy})

    async def switch(self, config: BrowserConfig, profile_dir: Path) -> None:
        """Work in the Chrome that ``config`` and ``profile_dir`` name from the next call on: the current one is let go
        of first (Dot's own Chrome is closed; in the Chrome of the user the tabs Dot opened are closed and the connection
        is dropped). Refused while a thread is in the middle of a step, so no run loses the page it works on."""
        async with self._launch_lock:
            if self._closed:
                raise BrowserError("the browser session was closed", {})
            busy: list[JsonValue] = [thread_id for thread_id, thread in self._threads.items() if thread.lock.locked()]
            if busy:
                raise BrowserError("the browser is in use; wait for the running work to finish", {"threads": busy})
            await self._let_go()
            self._config = config
            self._profile = profile_dir
            _log.info("browser_switched", mode=config.mode, profile=str(profile_dir))

    async def aclose(self) -> None:
        """Let go of Chrome and stop the Playwright driver. Dot's own Chrome is closed; the Chrome of the user stays
        open and only the tabs Dot opened in it are closed. Safe to call more than once."""
        self._closed = True
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        await self._let_go()

    async def _let_go(self) -> None:
        """Close Dot's own Chrome, or close the tabs Dot opened in the Chrome of the user and drop the connection; then
        stop the Playwright driver, also when the close failed, so no driver (and no Chrome it started) is left running.
        The tabs of every thread are gone with it."""
        for task in [*self._adopting, *self._front_returns]:
            task.cancel()
        context: BrowserContext | None = self._context
        browser: Browser | None = self._browser
        self._context = None
        self._browser = None
        self._chrome_pid = None
        try:
            if browser is not None:
                await self._detach(browser)
            elif context is not None:
                try:
                    await context.close()
                except Exception as error:
                    if not browser_gone(error):
                        raise
                    _log.warning("browser_close_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            self._threads.clear()
            self._initial_pages = []

    async def _detach(self, browser: Browser) -> None:
        """Close the tabs Dot opened in the Chrome of the user, then drop the connection (Chrome keeps running)."""
        for thread_id in list(self._threads):
            await self.release_thread(thread_id)
        try:
            await browser.close()
        except Exception as error:
            if not browser_gone(error):
                raise
            _log.warning("browser_detach_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))

    async def _reap_idle(self) -> None:
        """Release the tabs of the threads not used for ``tab_idle_s``, as long as the session is open. A thread whose
        tabs cannot be released is logged and tried again at the next round, and the others are still released."""
        while True:
            await asyncio.sleep(_REAP_INTERVAL_S)
            cutoff: float = time.monotonic() - self._config.tab_idle_s
            for thread_id, thread in list(self._threads.items()):
                if thread.last_used < cutoff and not thread.lock.locked():
                    _log.info("browser_idle_thread_released", thread_id=thread_id)
                    try:
                        await self.release_thread(thread_id)
                    except Exception:
                        _log.exception("browser_idle_release_failed", thread_id=thread_id)

    async def attachment(self) -> Attachment:
        """The Chrome of the user that this session works in; connects first when it is not connected yet."""
        context: BrowserContext = await self._ensure_context()
        browser: Browser | None = self._browser
        if browser is None:
            raise BrowserError(
                "this session works in Dot's own Chrome, not in the open one", {"mode": self._config.mode}
            )
        return Attachment(browser_version=browser.version, open_tabs=len(context.pages))

    async def stored_cookie_count(self) -> int:
        """How many cookies Chrome could read from the profile; cookies it cannot decrypt are not counted."""
        context: BrowserContext = await self._ensure_context()
        try:
            return len(await context.cookies())
        except PlaywrightError as error:
            raise BrowserError(
                "the cookies of the browser profile could not be listed",
                {"profile": str(self._profile), "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)},
            ) from error

    async def release_thread(self, thread_id: str) -> None:
        """Close the tabs of a thread that is finished."""
        thread: ThreadTabs | None = self._threads.pop(thread_id, None)
        if thread is None:
            return
        for tab in list(thread.tabs):
            await self._close_tab_object(thread, tab)

    # ------------------------------------------------------------------ tabs

    def _on_page(self, page: Page) -> None:
        task: asyncio.Task[None] = asyncio.create_task(self._adopt_popup(page))
        self._adopting.add(task)
        task.add_done_callback(self._adopting.discard)

    async def _adopt_popup(self, page: Page) -> None:
        """A page that a tab of ours opened belongs to the same thread; any other new page is not ours to touch. A popup
        that closes itself while this runs (a sign-in callback) is a note on its opener, not an error."""
        try:
            opener: Page | None = await page.opener()
        except Exception as error:
            self._popup_lost(error, page, None)
            return
        if opener is None:
            return
        owner: tuple[ThreadTabs, Tab] | None = self._owner_of(opener)
        if owner is None:
            return
        self._return_front()
        thread, opener_tab = owner
        try:
            tab: Tab = await self._register(page)
        except Exception as error:
            self._popup_lost(error, page, opener_tab)
            return
        if page.is_closed():
            add_note(opener_tab, closed_popup_note(page.url))
            return
        thread.tabs.append(tab)
        add_note(opener_tab, f"a new tab opened as tab {len(thread.tabs)}")
        await self._enforce_cap(thread)

    def _keeps_front(self) -> bool:
        """Whether Dot gives the front of the Mac back when its own Chrome takes it (item 118): a visible window that may
        not be raised, whose process is known."""
        config: BrowserConfig = self._config
        return config.mode == "own" and not config.headless and not config.raise_window and self._chrome_pid is not None

    async def _note_front(self) -> None:
        """Remember the application that has the front as a thread begins to act, Dot's own Chrome included (the owner
        may be looking at it), for :meth:`_return_front`."""
        if self._keeps_front():
            self._front_before = await asyncio.to_thread(front_pid)

    def _return_front(self) -> None:
        """A tab a page opens itself brings Chrome to the front by Chrome's own doing: the application that had the front
        when the thread began to act gets it back, unless that was Dot's Chrome itself. The check runs beside the step,
        which does not wait for it."""
        before: int | None = self._front_before
        chrome: int | None = self._chrome_pid
        if not self._keeps_front() or before is None or chrome is None or before == chrome:
            return
        task: asyncio.Task[None] = asyncio.create_task(asyncio.to_thread(return_front, before, chrome))
        self._front_returns.add(task)
        task.add_done_callback(self._front_returns.discard)

    def _popup_lost(self, error: Exception, page: Page, opener_tab: Tab | None) -> None:
        """Account for a popup that could not be adopted; an error that is neither a closed page nor a gone browser is
        raised as it is."""
        fate: PopupFate | None = popup_fate(error, page.is_closed())
        if fate is None:
            raise error
        if fate == "closed":
            _log.info("browser_popup_closed_itself", url=truncate_middle(page.url, _LAUNCH_ERROR_CHARS))
            if opener_tab is not None:
                add_note(opener_tab, closed_popup_note(page.url))
            return
        _log.warning("browser_popup_not_adopted", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))

    def _owner_of(self, page: Page) -> tuple[ThreadTabs, Tab] | None:
        for thread in self._threads.values():
            for tab in thread.tabs:
                if tab.page is page:
                    return thread, tab
        return None

    async def _register(self, page: Page) -> Tab:
        tab: Tab = Tab(page=page)
        attach_handlers(tab, self._downloads)
        page.on("close", partial(self._on_page_closed, tab))
        if self._config.mode == "attach":
            await keep_focused(page)
        if self._config.decline_cookie_banners:
            await install_consent_handler(page)
        return tab

    def _on_page_closed(self, tab: Tab, _page: Page) -> None:
        for thread in self._threads.values():
            if tab in thread.tabs:
                thread.remove(tab)
                return

    def _take_initial_page(self) -> Page | None:
        """The blank tab Chrome starts with, once, so a new thread does not leave it behind."""
        while self._initial_pages:
            page: Page = self._initial_pages.pop(0)
            if not page.is_closed() and page.url in _BLANK_URLS:
                return page
        return None

    async def _create_tab(self, thread: ThreadTabs) -> Tab:
        context: BrowserContext = await self._ensure_context()
        async with self._tab_lock:
            page: Page | None = self._take_initial_page()
            if page is None:
                try:
                    page = await self._new_tab(context)
                except PlaywrightError as error:
                    raise BrowserError(
                        "opening a tab failed", {"error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)}
                    ) from error
            tab: Tab = await self._register(page)
            thread.tabs.append(tab)
            thread.active = len(thread.tabs) - 1
        await self._enforce_cap(thread)
        return tab

    async def _current_tab(self, thread: ThreadTabs) -> Tab:
        for tab in [candidate for candidate in thread.tabs if candidate.page.is_closed()]:
            thread.remove(tab)
        if not thread.tabs:
            return await self._create_tab(thread)
        return thread.tabs[thread.active]

    async def _enforce_cap(self, thread: ThreadTabs) -> None:
        while len(thread.tabs) > self._config.max_tabs:
            keep: Tab = thread.tabs[thread.active]
            victim: Tab = next(tab for tab in thread.tabs if tab is not keep)
            add_note(keep, f"closed the oldest background tab to stay within max_tabs={self._config.max_tabs}")
            await self._close_tab_object(thread, victim)

    async def _close_tab_object(self, thread: ThreadTabs, tab: Tab) -> None:
        if tab in thread.tabs:
            thread.remove(tab)
        try:
            await tab.page.close()
        except Exception as error:
            if not browser_gone(error):
                raise
            _log.warning("browser_tab_close_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))

    @asynccontextmanager
    async def _locked(self, thread_id: str) -> AsyncIterator[ThreadTabs]:
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None:
            thread = ThreadTabs()
            self._threads[thread_id] = thread
        async with thread.lock:
            await self._ensure_context()
            await self._note_front()
            playwright: Playwright | None = self._playwright
            thread.last_used = time.monotonic()
            try:
                yield thread
            except Exception as error:
                if playwright is None or not driver_lost(error):
                    raise
                async with self._launch_lock:
                    await self._discard_driver(playwright)
                raise connection_broke(error) from error
            finally:
                thread.last_used = time.monotonic()

    # ------------------------------------------------------------------ observation

    async def _let_commit(self, tab: Tab) -> None:
        """Let a navigation that is under way commit."""
        try:
            await asyncio.wait_for(tab.nav_idle.wait(), timeout=self._config.navigation_timeout_s)
        except TimeoutError:
            add_note(tab, _STILL_LOADING)

    async def _settle(self, tab: Tab) -> None:
        """Let a navigation that is under way commit, then wait for a quiet DOM."""
        if tab.page.is_closed():
            return
        config: BrowserConfig = self._config
        await self._let_commit(tab)
        await wait_dom_quiet(tab.page, config.settle_quiet_ms, config.settle_max_ms, _ms(config.navigation_timeout_s))

    async def _viewport(self, tab: Tab) -> Viewport:
        """The page area the model sees: the configured size in Dot's own Chrome, the real window in the open Chrome."""
        config: BrowserConfig = self._config
        if config.mode == "own":
            return Viewport(width=config.viewport_width, height=config.viewport_height)
        return await measure_viewport(tab.page)

    async def _capture(self, tab: Tab) -> _Capture:
        config: BrowserConfig = self._config
        viewport: Viewport = await self._viewport(tab)
        lines: tuple[SnapshotLine, ...] = await capture_lines(tab.page, _ms(config.action_timeout_s))
        kept: tuple[SnapshotLine, ...] = viewport_filter(
            lines, viewport.width, viewport.height, config.viewport_margin_px
        )
        overlay, overlays = overlay_lines(lines, viewport.width, viewport.height)
        return _Capture(
            lines=lines,
            rendered=render_lines(kept),
            refs=count_refs(kept),
            offscreen=max(0, count_refs(lines) - count_refs(kept)),
            overlay=render_lines(overlay),
            overlays=overlays,
        )

    async def _capture_loaded(self, tab: Tab) -> _Capture:
        """Capture after a navigation; a nearly empty first view gets one more, longer, settle before it is shown."""
        capture: _Capture = await self._capture(tab)
        if capture.refs >= _MIN_REFS_AFTER_LOAD or tab.page.url in _BLANK_URLS:
            return capture
        await wait_dom_quiet(tab.page, _LATE_QUIET_MS, _LATE_MAX_MS, _ms(self._config.navigation_timeout_s))
        return await self._capture(tab)

    async def _tab_title(self, tab: Tab) -> str:
        try:
            return await page_title(tab.page)
        except BrowserError as error:
            _log.warning("browser_tab_title_unavailable", error=error.message)
            return "(title unavailable)"

    async def _summaries(self, thread: ThreadTabs) -> tuple[TabSummary, ...]:
        tabs: list[Tab] = list(thread.tabs)
        titles: list[str] = list(await asyncio.gather(*(self._tab_title(tab) for tab in tabs)))
        return tuple(
            TabSummary(index=index, url=tab.page.url, title=title, active=index - 1 == thread.active)
            for index, (tab, title) in enumerate(zip(tabs, titles, strict=True), start=1)
        )

    async def _assemble(
        self, thread: ThreadTabs, tab: Tab, capture: _Capture, body: str, mode: Literal["full", "delta"]
    ) -> PageView:
        url: str = tab.page.url
        view: PageView = PageView(
            tab_index=thread.active + 1,
            url=url,
            title=await self._tab_title(tab),
            status=tab.status,
            mode=mode,
            body=body,
            overlay=capture.overlay,
            overlays=capture.overlays,
            offscreen_refs=capture.offscreen,
            tabs=await self._summaries(thread),
            notes=tuple(tab.notes),
        )
        tab.notes.clear()
        tab.lines = capture.lines
        tab.rendered = capture.rendered
        tab.rendered_url = url
        return view

    async def _full_view(self, thread: ThreadTabs, tab: Tab) -> PageView:
        await self._settle(tab)
        return await self._settled_view(thread, tab)

    async def _settled_view(self, thread: ThreadTabs, tab: Tab) -> PageView:
        """The whole page, of a tab that has just settled."""
        capture: _Capture = await self._capture(tab)
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    async def _changes_view(self, thread: ThreadTabs, tab: Tab) -> PageView:
        """The page as it is now, as a delta against the last view when that is clearly smaller."""
        capture: _Capture = await self._capture(tab)
        if tab.rendered and tab.rendered_url == tab.page.url:
            diff = diff_views(tab.rendered, capture.rendered)
            if prefer_diff(diff, capture.rendered):
                return await self._assemble(thread, tab, capture, render_diff(diff), "delta")
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    def _require_allowed(self, url: str) -> None:
        verdict = check_browser_url(url, self._config.allowed_hosts, self._config.blocked_hosts, self._server)
        if not verdict.allowed:
            raise BrowserError("this URL may not be opened", {"url": url, "reason": verdict.reason})

    async def _enforce_landing(self, tab: Tab) -> None:
        """A redirect or click may have ended on a URL the rules forbid; leave it before anything is read from it."""
        landed: str = tab.page.url
        if landed.startswith("chrome-error://"):
            add_note(tab, "the page failed to load (Chrome error page)")
            return
        verdict = check_browser_url(landed, self._config.allowed_hosts, self._config.blocked_hosts, self._server)
        if verdict.allowed:
            return
        config: BrowserConfig = self._config
        await navigate(tab.page, "about:blank", _ms(config.navigation_timeout_s), 1, config.navigation_backoff_ms)
        raise BrowserError(
            "the page ended up on a URL that may not be opened; the tab was reset to a blank page",
            {"landed": landed, "reason": verdict.reason},
        )

    async def _load(self, thread: ThreadTabs, tab: Tab, url: str) -> PageView:
        self._require_allowed(url)
        config: BrowserConfig = self._config
        tab.status = None
        status: int | None = await navigate(
            tab.page, url, _ms(config.navigation_timeout_s), config.navigation_attempts, config.navigation_backoff_ms
        )
        if status is not None:
            tab.status = status
        await self._settle(tab)
        await self._enforce_landing(tab)
        capture: _Capture = await self._capture_loaded(tab)
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    # ------------------------------------------------------------------ public operations

    async def open_url(self, thread_id: str, url: str) -> PageView:
        """Navigate the current tab of the thread."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            return await self._load(thread, tab, url)

    async def new_tab(self, thread_id: str, url: str) -> PageView:
        """Open ``url`` in a new tab that becomes the current one."""
        self._require_allowed(url)
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._create_tab(thread)
            return await self._load(thread, tab, url)

    async def snapshot(self, thread_id: str) -> PageView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            return await self._full_view(thread, tab)

    async def find(self, thread_id: str, needle: str, limit: int) -> FindResult:
        """Search the whole page, also outside the viewport, for elements whose role or name contains ``needle``."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            capture: _Capture = await self._capture(tab)
            tab.lines = capture.lines
            return FindResult(url=tab.page.url, hits=find_lines(capture.lines, needle, limit))

    async def links(self, thread_id: str, needle: str, limit: int) -> FindResult:
        """The links of the whole page with absolute targets; ``needle`` filters on link text and target."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            capture: _Capture = await self._capture(tab)
            tab.lines = capture.lines
            return FindResult(url=tab.page.url, hits=link_lines(capture.lines, tab.page.url, needle, limit))

    @property
    def quiet_reads(self) -> bool:
        """Whether a page may be read in a tab the user does not work in: a scratch tab of Dot's own Chrome, or a tab
        opened behind the user's in the user's Chrome and closed after the read (item 98). True in both modes."""
        return True

    @property
    def human_like(self) -> bool:
        """Whether a page read here passes the bot checks a plain request meets: Dot's own Chrome with a visible window.
        A headless Chrome is told apart (measured 2026-10-11: DuckDuckGo shows it a CAPTCHA and a visible Chrome its
        results), so it does not count; the user's Chrome is not used for searches."""
        return self._config.mode == "own" and not self._config.headless

    async def read_page(self, url: str) -> RenderedDocument:
        """Read ``url`` in a tab of its own, one no thread owns and that closes when the read is over: the address the
        page ended on, the status of its last main response and its HTML after JavaScript ran and the page settled. A
        page that answered with a bot wall gets a few seconds to pass its own check first. For the web tools, when a
        plain request was turned away. In the user's Chrome the tab opens behind the user's (item 98)."""
        self._require_allowed(url)
        config: BrowserConfig = self._config
        context: BrowserContext = await self._ensure_context()
        try:
            page: Page = await self._scratch_page(context)
        except PlaywrightError as error:
            raise BrowserError(
                "opening a tab failed", {"error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)}
            ) from error
        statuses: list[int] = []
        page.on("response", lambda response: _note_main_status(page, response, statuses))
        try:
            first: int | None = await navigate(
                page, url, _ms(config.navigation_timeout_s), config.navigation_attempts, config.navigation_backoff_ms
            )
            if first is None:
                raise BrowserError("the address started a download, not a page", {"url": url})
            if first in _CHALLENGE_STATUS:
                with contextlib.suppress(PlaywrightError):
                    await page.wait_for_event("load", timeout=_CHALLENGE_WAIT_MS)
            await wait_dom_quiet(page, config.settle_quiet_ms, config.settle_max_ms, _ms(config.navigation_timeout_s))
            landed: str = page.url
            verdict = check_browser_url(landed, config.allowed_hosts, config.blocked_hosts, self._server)
            if not verdict.allowed:
                raise BrowserError(
                    "the page ended up on a URL that may not be opened", {"landed": landed, "reason": verdict.reason}
                )
            try:
                html: str = await page.content()
            except PlaywrightError as error:
                raise BrowserError(
                    "reading the page failed", {"url": url, "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)}
                ) from error
        finally:
            await self._close_scratch(page)
        return RenderedDocument(url=landed, status=statuses[-1] if statuses else first, html=html)

    async def _background_tab(self, context: BrowserContext) -> Page:
        """A new tab created in the background through CDP. Playwright's ``new_page`` opens in the foreground, which
        brings Chrome to the front of the Mac (measured 2026-10-12, item 118) and takes the user's tab in the user's
        Chrome; a tab made this way does neither, and it still paints, screencasts and takes screenshots as a visible tab
        does. ``new_page`` stays for a context without a browser at hand."""
        browser: Browser | None = context.browser
        if browser is None:
            return await context.new_page()
        # Each tab is told apart by an address of its own: tabs made at the same time (a crawl reads pages side by side)
        # would otherwise all be handed the first new page (measured 2026-10-12: five calls, one page). It goes back to a
        # plain blank page at once, the address the URL rules and the tab views know.
        mark: str = f"about:blank#dot-{secrets.token_hex(_TAB_MARK_BYTES)}"
        cdp: CDPSession = await browser.new_browser_cdp_session()
        try:
            async with context.expect_page(lambda page: page.url == mark) as opened:
                await cdp.send("Target.createTarget", {"url": mark, "background": True})
            page: Page = await opened.value
        finally:
            await cdp.detach()
        await page.goto("about:blank", wait_until="commit")
        return page

    async def _new_tab(self, context: BrowserContext) -> Page:
        """A thread's new tab: in the background, unless the window may be raised (item 97), when it is the window's
        visible tab as before."""
        if self._config.raise_window:
            return await context.new_page()
        return await self._background_tab(context)

    async def _scratch_page(self, context: BrowserContext) -> Page:
        """A new tab for a quiet read, always in the background: the tab the user works in, and the application in
        front, keep the front."""
        return await self._background_tab(context)

    async def _close_scratch(self, page: Page) -> None:
        try:
            await page.close()
        except Exception as error:
            if not browser_gone(error):
                raise
            _log.warning("browser_tab_close_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))

    async def read_text(self, thread_id: str, ref: str | None, offset: int, max_chars: int) -> TextView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            text: str = squeeze_text(await visible_text(tab.page, ref, _ms(self._config.action_timeout_s)))
            return TextView(
                url=tab.page.url, text=text[offset : offset + max_chars], offset=offset, total_chars=len(text)
            )

    async def screenshot(self, thread_id: str) -> Screenshot:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            config: BrowserConfig = self._config
            viewport: Viewport = await self._viewport(tab)
            data: bytes = await screenshot_jpeg(tab.page, config.screenshot_quality, _ms(config.action_timeout_s))
            return Screenshot(url=tab.page.url, width=viewport.width, height=viewport.height, data=data)

    def _tab_of(self, thread_id: str) -> Tab | None:
        """The current tab of the thread without waiting for its lock, ``None`` when the thread has no open tab. Starts
        nothing: a thread that never used the browser has no tab."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return None
        tab: Tab = thread.tabs[thread.active]
        return None if tab.page.is_closed() else tab

    async def focus(self, thread_id: str) -> str | None:
        """Bring the thread's current tab to the front of its window, for the user to take over; returns its address,
        ``None`` when the thread has no tab."""
        tab: Tab | None = self._tab_of(thread_id)
        if tab is None:
            return None
        await bring_to_front(tab.page)
        return tab.page.url

    async def screencast(self, thread_id: str) -> AsyncGenerator[Frame | None]:
        """Frames of the thread's current tab as Chrome paints it (``Page.startScreencast``), for as long as the caller
        reads them: a tab that does not change sends nothing, and ``None`` comes once a second in between so the caller
        can check whether it still wants frames. The cast follows the current tab when the model switches or closes it,
        waits while the thread has no tab yet, and ends when the browser is closed."""
        while not self._closed:
            tab: Tab | None = self._tab_of(thread_id)
            if tab is None:
                await asyncio.sleep(_CAST_POLL_S)
                yield None
                continue
            frames: asyncio.Queue[JsonObject] = asyncio.Queue()
            try:
                session: CDPSession = await tab.page.context.new_cdp_session(tab.page)
            except PlaywrightError:
                await asyncio.sleep(_CAST_POLL_S)
                yield None
                continue
            try:
                session.on("Page.screencastFrame", frames.put_nowait)
                await session.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": _CAST_QUALITY,
                        "maxWidth": _CAST_MAX_EDGE,
                        "maxHeight": _CAST_MAX_EDGE,
                        "everyNthFrame": 1,
                    },
                )
                while self._tab_of(thread_id) is tab:
                    try:
                        frame: JsonObject = await asyncio.wait_for(frames.get(), _CAST_POLL_S)
                    except TimeoutError:
                        yield None
                        continue
                    await session.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})
                    yield Frame(url=tab.page.url, data=base64.b64decode(str(frame["data"])))
            except PlaywrightError:
                # The tab went away under the cast; the loop attaches to whatever tab is current now.
                pass
            finally:
                with contextlib.suppress(PlaywrightError):
                    await session.send("Page.stopScreencast")
                    await session.detach()

    async def _run_steps(
        self, thread: ThreadTabs, tab: Tab, steps: Sequence[Step], secret_field: SecretFieldCheck
    ) -> tuple[list[StepReport], str | None]:
        config: BrowserConfig = self._config
        reports: list[StepReport] = []
        stopped: str | None = None
        for index, step in enumerate(steps, start=1):
            description: str = describe_step(step)
            if stopped is not None:
                reports.append(StepReport(index=index, description=description, outcome="skipped", detail=""))
                continue
            before: _Watch = self._watch(thread, tab)
            try:
                await run_step(tab.page, step, _ms(config.action_timeout_s), secret_field)
            except BrowserError as error:
                if tab.page.is_closed():
                    # The page closed under the step (a callback that did its job, a tab the user closed): not a failure.
                    reports.append(
                        StepReport(index=index, description=description, outcome="skipped", detail=_CLOSED_STEP_DETAIL)
                    )
                    stopped = closed_tab_stop(tab.page.url)
                    continue
                reports.append(
                    StepReport(index=index, description=description, outcome="failed", detail=_error_detail(error))
                )
                stopped = f"step {index} failed"
                continue
            except (PolicyDenied, ApprovalRejected) as error:
                # The gate refused the step at a field that takes a secret.
                detail: str = f"Not allowed: {error.message}. Do not retry this step or look for a way around it."
                reports.append(StepReport(index=index, description=description, outcome="failed", detail=detail))
                stopped = "a step was not allowed"
                continue
            except HandedOff as error:
                detail = f"the user handled this step themselves ({error.message})"
                reports.append(StepReport(index=index, description=description, outcome="done", detail=detail))
                stopped = "the user took over this step; look at the page before going on"
                continue
            if step.has_side_effects():
                await self._let_events_land()
            reports.append(StepReport(index=index, description=description, outcome="done", detail=""))
            stopped = describe_surprise(before, self._watch(thread, tab))
        return reports, stopped

    def _watch(self, thread: ThreadTabs, tab: Tab) -> _Watch:
        return _Watch(url=tab.page.url, tabs=len(thread.tabs), events=tab.events, closed=tab.page.is_closed())

    async def _let_events_land(self) -> None:
        """Popups, dialogs and navigations are announced a few milliseconds after the action that caused them."""
        await asyncio.sleep(self._config.side_effect_grace_ms / 1000)
        pending: list[asyncio.Task[None]] = list(self._adopting)
        if pending:
            await asyncio.wait(pending, timeout=_POPUP_WAIT_S)

    async def act(
        self, thread_id: str, steps: Sequence[Step], accept_confirms: bool, secret_field: SecretFieldCheck
    ) -> ActOutcome:
        """Run the steps in order against the latest snapshot; stop at the first failure or surprise. ``secret_field``
        decides what a step does at a field that takes a secret: a step it refuses fails and stops the batch, and one
        the user takes over stops it too."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            tab.accept_confirms = accept_confirms
            try:
                reports, stopped = await self._run_steps(thread, tab, steps, secret_field)
            finally:
                tab.accept_confirms = False
            current: Tab = await self._current_tab(thread)
            await self._settle(current)
            await self._enforce_landing(current)
            view: PageView = (
                await self._changes_view(thread, current)
                if current is tab
                else await self._settled_view(thread, current)
            )
            return ActOutcome(steps=tuple(reports), stopped=stopped, view=view)

    async def scroll(
        self,
        thread_id: str,
        direction: Literal["down", "up", "top", "bottom"],
        pixels: int,
        ref: str | None,
    ) -> PageView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            viewport: Viewport = await self._viewport(tab)
            await scroll_page(
                tab.page, direction, pixels, ref, viewport.width, viewport.height, _ms(self._config.action_timeout_s)
            )
            # The wheel lands a few tens of milliseconds after the call returns, inside the DOM quiet window.
            await self._settle(tab)
            return await self._changes_view(thread, tab)

    async def wait_text(self, thread_id: str, text: str, timeout_s: float) -> TextWait:
        """Wait until ``text`` is visible on the current tab, at most ``timeout_s``; the page is shown either way. After a
        wait that timed out the page is shown as it is, without a settle that could outlast the wait. A navigation still
        pending then holds the reading until it commits, so the URL rules are checked again once the page is read."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            started: float = time.monotonic()
            appeared: bool = await wait_for_text(tab.page, text, _ms(timeout_s))
            waited_s: float = time.monotonic() - started
            if appeared:
                await self._settle(tab)
            elif not tab.nav_idle.is_set():
                add_note(tab, _STILL_LOADING)
            await self._enforce_landing(tab)
            view: PageView = await self._changes_view(thread, tab)
            await self._enforce_landing(tab)
            return TextWait(appeared=appeared, waited_s=waited_s, view=view)

    async def run_script(self, thread_id: str, script: str, timeout_s: float) -> ScriptRun:
        """Run ``script`` in the page of the thread's current tab and wait at most ``timeout_s`` for its value (item
        263). What the script led to is waited for as after a step (a tab it opened, a dialog), and the page is settled
        after every script as after a step (item 270), so that a navigation it started is let load, also one that
        commits after its value came back; the address rules then hold as after a step: a tab that ended on an address
        they forbid is reset to a blank page and the call fails. After a script still running when the wait ended, only
        a navigation under way is let commit: the page cannot tell its DOM is quiet while the script holds it."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            before: _Watch = self._watch(thread, tab)
            started: float = time.monotonic()
            end, text = await run_script(tab.page, script, timeout_s)
            seconds: float = time.monotonic() - started
            await self._let_events_land()
            current: Tab = await self._current_tab(thread)
            if end == "timeout":
                await self._let_commit(current)
            else:
                await self._settle(current)
            after: _Watch = self._watch(thread, tab)
            await self._enforce_landing(current)
            return ScriptRun(
                end=end,
                text=text,
                url=current.page.url,
                surprise=describe_surprise(before, after),
                seconds=seconds,
            )

    async def back(self, thread_id: str) -> PageView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            tab.status = await go_back(tab.page, _ms(self._config.navigation_timeout_s))
            await self._settle(tab)
            await self._enforce_landing(tab)
            capture: _Capture = await self._capture_loaded(tab)
            return await self._assemble(thread, tab, capture, capture.rendered, "full")

    async def switch_tab(self, thread_id: str, index: int) -> PageView:
        async with self._locked(thread_id) as thread:
            self._require_tab_index(thread, index)
            thread.active = index - 1
            tab: Tab = thread.tabs[thread.active]
            # The tab is made the window's visible one only when the window may be raised (item 97).
            if self._config.raise_window:
                await bring_to_front(tab.page)
            return await self._full_view(thread, tab)

    async def close_tab(self, thread_id: str, index: int) -> PageView:
        async with self._locked(thread_id) as thread:
            self._require_tab_index(thread, index)
            await self._close_tab_object(thread, thread.tabs[index - 1])
            tab: Tab = await self._current_tab(thread)
            return await self._full_view(thread, tab)

    def _require_tab_index(self, thread: ThreadTabs, index: int) -> None:
        if not 1 <= index <= len(thread.tabs):
            raise BrowserError("there is no such tab", {"index": index, "open_tabs": len(thread.tabs)})

    async def upload(self, thread_id: str, ref: str, paths: Sequence[str]) -> PageView:
        """Click the upload control ``ref`` and give the file chooser it opens ``paths``."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            tab.expect_chooser = True
            try:
                await upload_files(tab.page, ref, list(paths), _ms(self._config.action_timeout_s))
            finally:
                tab.expect_chooser = False
            await self._settle(tab)
            return await self._changes_view(thread, tab)

    # ------------------------------------------------------------------ reads without browser access

    def peek_line(self, thread_id: str, ref: str) -> str | None:
        """The snapshot line of ``ref`` in the thread's current tab, for judging a step before it runs."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return None
        return line_for_ref(thread.tabs[thread.active].lines, ref)

    def current_url(self, thread_id: str) -> str | None:
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return None
        return thread.tabs[thread.active].page.url
