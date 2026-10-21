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
import re
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal
from urllib.parse import urldefrag

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Cookie,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame as PlaywrightFrame
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, DebugEndpoint, endpoint_url, read_debug_endpoint, redact_endpoint
from dotagent.browser.devtools import devtools_switches, port_is_taken, port_taken
from dotagent.browser.driver import (
    bring_to_front,
    capture_lines,
    go_back,
    install_consent_handler,
    keep_focused,
    let_page_turn,
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
from dotagent.browser.look import PageLook, color_scheme
from dotagent.browser.pages import Tab, ThreadTabs, add_note, attach_handlers, taken_in, watch_births
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.profiles import INCOGNITO_PROFILE, empty_incognito, profile_dir, write_default_zoom
from dotagent.browser.render import describe_step
from dotagent.browser.safety import UrlVerdict, check_browser_url, request_verdict
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
from dotagent.browser.teach import TEACH_BINDING, TEACH_SCRIPT, Recording
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
from dotagent.core.errors import (
    ApprovalRejected,
    BrowserError,
    ForbiddenUrlError,
    HandedOff,
    PolicyDenied,
    RunConflict,
)
from dotagent.core.images import jpeg_of_size
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
# The longest a step waits for the tabs Chrome made for its page to be taken in by its thread (item 342).
_POPUP_WAIT_S: float = 1.0
# A page that shows fewer elements than this right after loading is probably still rendering.
_MIN_REFS_AFTER_LOAD: int = 3
_LATE_QUIET_MS: int = 300
_LATE_MAX_MS: int = 2500
_STILL_LOADING: str = "the page is still loading; what is shown may be incomplete"
# Why a batch stops after a step that made a tab whose page has not answered within _POPUP_WAIT_S (item 342).
_TAB_OPENING: str = (
    "a new tab is opening and its page has not answered yet; look again with browser_snapshot, then use "
    "browser_switch_tab to work in it"
)
_BLANK_URLS: frozenset[str] = frozenset({"about:blank", "chrome://newtab/", "chrome://new-tab-page/"})
_TAB_MARK_BYTES: int = 6
# Dot's own Chrome at this zoom draws a page at the size it was given, which the page need not be asked for (item 358).
_UNZOOMED: int = 100
# A scroll with no distance moves by this share of the page area the model sees.
_SCROLL_SHARE: float = 0.8

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
# What a step says when the Chrome of the user went away since Dot connected to it, and what the owner is told on the
# Mac when Dot connects to it again (item 341).
CHROME_RESTARTED: str = "Chrome was closed or restarted"
RECONNECT_NOTICE: str = "Chrome was closed or restarted: click Allow in Chrome so that Dot can go on."
# The URL rules on every request of Dot's own Chrome (item 135, measured 2026-10-15 with Playwright 1.63 and Chrome
# 155): a route on the context sees the first request of every page, popups included, each resource a page loads and
# the fetches of a service worker, on the address as Chrome read it; what it does not see is the hops of a redirect
# (Chrome follows them before it asks), so each tab is asked about those through a Fetch session of its own
# (:meth:`BrowserSession._guard_hops`). The owner's Chrome (attach mode) gets neither: its tabs are the owner's.
_EVERY_URL: re.Pattern[str] = re.compile(".*")
_FETCH_PATTERNS: list[dict[str, str]] = [{"urlPattern": "*", "requestStage": "Request"}]
_DOCUMENT_RESOURCE: str = "Document"
_BLOCKED_BY_CLIENT: str = "ERR_BLOCKED_BY_CLIENT"
_ABORT_REASON: str = "blockedbyclient"
_FAIL_REASON: str = "BlockedByClient"
# What Chrome answers to a verdict for a request the page cancelled or finished while the verdict was being made.
_REQUEST_GONE: str = "Invalid InterceptionId"
_FORBIDDEN_MESSAGE: str = "this URL may not be opened"
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
    return (
        f"the tab was closed by the page (it was at {truncate_middle(url, _LAUNCH_ERROR_CHARS)}); "
        "the tab now current is shown below"
    )


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
class _Paused:
    """A request Chrome holds for an answer (``Fetch.requestPaused``): its id, the address as Chrome read it, and what
    it is for; a navigation of a frame loads a document."""

    request_id: str
    url: str
    resource_type: str
    frame_id: str

    @property
    def navigation(self) -> bool:
        return self.resource_type == _DOCUMENT_RESOURCE


def paused_request(event: Mapping[str, object]) -> _Paused | None:
    """The request a ``Fetch.requestPaused`` event holds, ``None`` when the event lacks what an answer needs."""
    request_id: object = event.get("requestId")
    request: object = event.get("request")
    resource_type: object = event.get("resourceType")
    frame_id: object = event.get("frameId")
    if not isinstance(request_id, str) or not isinstance(request, Mapping):
        return None
    url: object = request.get("url")
    if not isinstance(url, str):
        return None
    return _Paused(
        request_id=request_id,
        url=url,
        resource_type=resource_type if isinstance(resource_type, str) else "",
        frame_id=frame_id if isinstance(frame_id, str) else "",
    )


def main_frame_id(tree: Mapping[str, object]) -> str | None:
    """The id of the main frame in what ``Page.getFrameTree`` answered, ``None`` when it is not there."""
    frame_tree: object = tree.get("frameTree")
    if not isinstance(frame_tree, Mapping):
        return None
    frame: object = frame_tree.get("frame")
    if not isinstance(frame, Mapping):
        return None
    identifier: object = frame.get("id")
    return identifier if isinstance(identifier, str) else None


def main_page_of(request: Request) -> Page | None:
    """The page whose main frame ``request`` navigates; ``None`` for any other request: a resource, a frame inside the
    page, a service worker's (which has no frame), or the first request of a popup, whose frame Playwright does not
    know yet."""
    if not request.is_navigation_request():
        return None
    try:
        frame: PlaywrightFrame = request.frame
    except PlaywrightError:
        return None
    page: Page = frame.page
    return page if frame == page.main_frame else None


def threads_in_use(threads: Mapping[str, ThreadTabs], kept: frozenset[str]) -> list[str]:
    """The chats whose tabs a switch of the browser would close under them: one in the middle of a step, and one whose
    run is at work between two of its calls (``kept``, item 305), which would find its pages gone."""
    return [thread_id for thread_id, thread in threads.items() if thread.lock.locked() or thread_id in kept]


def threads_to_release(threads: Mapping[str, ThreadTabs], cutoff: float, kept: frozenset[str]) -> list[str]:
    """The chats whose tabs the idle reaper releases: unused since ``cutoff`` and not in use now, unless a run of the
    chat is at work (``kept``): a run that waits for an answer or for a long command finds its pages as it left them."""
    return [
        thread_id
        for thread_id, thread in threads.items()
        if thread.last_used < cutoff and not thread.lock.locked() and thread_id not in kept
    ]


def no_runs_at_work() -> frozenset[str]:
    """The chats a session works for when no run does (``dotx browser``): none, so every idle chat is released."""
    return frozenset()


def blocked_by_rules(error: BrowserError) -> bool:
    """Whether a navigation failed because a request of it was refused by the URL rules: Chrome reports an aborted or
    failed request as ``net::ERR_BLOCKED_BY_CLIENT``."""
    return _BLOCKED_BY_CLIENT in str(error)


@dataclass(frozen=True, slots=True)
class _Refusal:
    """A navigation of a page's main frame that the URL rules refused, for the error of the call that ran into it: the
    page shows Chrome's error page, which says nothing of why."""

    url: str
    reason: str


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

    def __init__(
        self,
        config: BrowserConfig,
        server: ServerConfig,
        profile_dir: Path,
        downloads_dir: Path,
        kept: Callable[[], frozenset[str]],
    ) -> None:
        self._config: BrowserConfig = config
        # The chats whose tabs the idle reaper keeps: those of the runs at work (:func:`threads_to_release`).
        self._kept: Callable[[], frozenset[str]] = kept
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
        # The answers under way to requests Chrome holds (:meth:`_guard_hops`), and by page the navigation of its main
        # frame that the rules refused last, which the error page the page then shows is about.
        self._answering: set[asyncio.Task[None]] = set()
        self._refusals: dict[Page, _Refusal] = {}
        # The pages of Dot's own Chrome whose requests a Fetch session of their own judges (:meth:`_guard_hops`), each
        # with the setting up of its session, which a navigation of the page waits for.
        self._guarded: dict[Page, asyncio.Task[None]] = {}
        self._reaper: asyncio.Task[None] | None = None
        # The uses of Chrome under way that no chat's tabs hold (a page read for the web tools, a count of the cookies),
        # and when Chrome was last started or such a use ended: the idle close counts both as uses (item 454).
        self._uses: int = 0
        self._used_at: float = time.monotonic()
        self._closed: bool = False
        # The front of the Mac around Dot's own Chrome (item 118): Chrome's process, the application that had the front
        # when a thread last began to act, and the checks under way that give the front back after a page's own tab.
        self._chrome_pid: int | None = None
        self._front_before: int | None = None
        self._front_returns: set[asyncio.Task[None]] = set()
        # The Chrome of the user went away (quit, restarted, the connection broke) since Dot was last connected to it,
        # and Dot waits now for its Allow (item 341): what the browser card and a call that meets the loss say.
        self._lost: bool = False
        self._waiting_allow: bool = False
        # Told when Dot connects again to a Chrome of the user that went away, before Chrome asks for its Allow, and the
        # tellings under way: the connection does not wait for them.
        self._on_reconnect: Callable[[], Awaitable[None]] | None = None
        self._telling: set[asyncio.Task[None]] = set()
        # The task the owner is showing Dot now (item 423): the one recording of the tab they work in, and the pages
        # whose recorder's binding and script are already there (a page keeps them across the documents it loads).
        self._teaching: Recording | None = None
        self._teach_armed: set[Page] = set()
        self._teach_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    def tell_reconnects(self, notice: Callable[[], Awaitable[None]]) -> None:
        """Have ``notice`` told each time Dot connects again to a Chrome of the user that went away (item 341): Chrome
        asks for Allow again then, maybe behind another window, while the step that needs it waits."""
        self._on_reconnect = notice

    @property
    def lost(self) -> bool:
        """Whether the Chrome of the user went away since Dot was last connected to it."""
        return self._lost

    @property
    def waiting_allow(self) -> bool:
        """Whether Dot waits now for the Chrome of the user to let it connect (its Allow)."""
        return self._waiting_allow

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
        # Incognito starts with nothing, however its last Chrome ended (item 355).
        if config.profile == INCOGNITO_PROFILE:
            await asyncio.to_thread(empty_incognito)
        # Chrome reads the zoom it draws pages at from the profile when it starts (item 358).
        await asyncio.to_thread(write_default_zoom, self._profile, config.zoom)
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
                # A page fills the window, or has the size Settings › Browser gave it (item 354).
                viewport=None
                if config.fit_window
                else {"width": config.viewport_width, "height": config.viewport_height},
                no_viewport=config.fit_window,
                color_scheme=color_scheme(config.appearance),
                accept_downloads=True,
                # A service worker of a page would fetch on the page's behalf from outside the route that reads the
                # URL rules (``_EVERY_URL``); none is registered.
                service_workers="block",
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
            # Before any page of it is used: the rules read every request of this Chrome (``_EVERY_URL``).
            await context.route(_EVERY_URL, self._on_request)
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
        again: bool = self._lost
        if again and self._on_reconnect is not None:
            self._tell(self._on_reconnect)
        self._waiting_allow = True
        try:
            browser: Browser = await playwright.chromium.connect_over_cdp(
                endpoint_url(endpoint),
                timeout=_ms(config.attach_timeout_s),
                # Chrome runs on this Mac, and Playwright must not put its own emulation or download handling on it.
                is_local=True,
                no_defaults=True,
            )
        except PlaywrightError as error:
            # A wait that ran out says so: Chrome's Allow was not clicked in time, or Chrome did not answer.
            waited: str = (
                f" (no answer within {config.attach_timeout_s:g} s)"
                if isinstance(error, PlaywrightTimeoutError)
                else ""
            )
            raise BrowserError(
                (CHROME_RESTARTED if again else "Chrome did not accept the connection")
                + waited
                + f"; keep a Chrome window open, click Allow when Chrome asks, and check that remote debugging is on "
                f"({INSPECT_PAGE})",
                {
                    "data_dir": str(self._profile),
                    "port": endpoint.port,
                    "waited_s": config.attach_timeout_s,
                    "error": truncate_middle(redact_endpoint(str(error)), _LAUNCH_ERROR_CHARS),
                },
            ) from error
        finally:
            self._waiting_allow = False
        self._lost = False
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

    def _tell(self, notice: Callable[[], Awaitable[None]]) -> None:
        """Have ``notice`` told beside the connection, which does not wait for it (item 341): Chrome asks for its Allow
        meanwhile, and a notice that fails (a notification macOS did not show) is logged, never in the connection's
        way."""

        async def telling() -> None:
            await notice()

        task: asyncio.Task[None] = asyncio.create_task(telling())
        self._telling.add(task)
        task.add_done_callback(self._told)

    def _told(self, task: asyncio.Task[None]) -> None:
        self._telling.discard(task)
        if task.cancelled():
            return
        error: BaseException | None = task.exception()
        if error is not None:
            _log.warning("browser_reconnect_notice_failed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))

    def _arm(self, opened: _Opened) -> BrowserContext:
        config: BrowserConfig = self._config
        context: BrowserContext = opened.context
        context.set_default_timeout(_ms(config.action_timeout_s))
        context.set_default_navigation_timeout(_ms(config.navigation_timeout_s))
        context.on("page", self._on_page)
        context.on("close", self._on_context_closed)
        self._initial_pages = list(opened.spare_pages)
        self._context = context
        self._used_at = time.monotonic()
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
        """Chrome quit, or the connection to it broke. A later connection must not be mistaken for this one, and the
        next call connects again, which Chrome asks the user to allow again (item 341)."""
        if self._closed or browser is not self._browser:
            return
        _log.warning("browser_disconnected", threads=len(self._threads))
        self._forget_tabs()
        self._context = None
        self._browser = None
        self._lost = True

    def _forget_tabs(self) -> None:
        self._initial_pages = []
        self._chrome_pid = None
        self._refusals.clear()
        self._guarded.clear()
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

    def chrome_pid(self) -> int | None:
        """The pid of the Chrome that holds this session's profile now (blocking: it reads the profile's lock), or
        ``None`` when none does: Dot's own Chrome while it runs (or a window opened on its profile to sign in), the
        Chrome of the user while it is open."""
        return profile_holder_pid(self._profile)

    @property
    def connected(self) -> bool:
        """Whether a Chrome is started or connected at the moment (one starts on the first call that needs it)."""
        return self._context is not None

    def set_enabled(self, enabled: bool) -> None:
        """Whether runs are offered the browser tools, for the runs that start from now on (item 354): a run at work
        keeps the tools it started with, and Chrome is not let go of."""
        self._config = self._config.model_copy(update={"enabled": enabled})
        _log.info("browser_enabled", mode=self._config.mode, enabled=enabled)

    async def set_look(self, look: PageLook) -> None:
        """How pages look from the next call on (items 354 and 358). Dot's own Chrome is let go of first, as for a
        switch, so that it starts again with the size, the colour scheme and the zoom, which is refused while a chat's
        run is at work in it; in the Chrome of the user the connection stays, and the tabs Dot opens from now on are
        told the colour scheme."""
        if self._config.mode == "own":
            await self.switch(self._config.model_copy(update=look.model_dump()), self._profile)
            return
        self._config = self._config.model_copy(update=look.model_dump())
        _log.info("browser_look", mode=self._config.mode, appearance=look.appearance)

    async def use_profile(self, profile: str) -> None:
        """Work on the profile ``profile`` of Dot's own Chrome from the next call on (item 355), as a switch does: Dot's
        own Chrome is let go of first, and the change is refused while a chat's run is at work in it. A session in the
        Chrome of the user goes on there, and takes the profile up when it is moved to Dot's own Chrome."""
        if self._config.mode == "own":
            await self.switch(self._config.model_copy(update={"profile": profile}), profile_dir(profile))
            return
        self._config = self._config.model_copy(update={"profile": profile})
        _log.info("browser_profile", mode=self._config.mode, profile=profile)

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
        a chat's run is at work in the browser (as for a switch), or another program holds the port."""
        async with self._launch_lock:
            await self._refuse_debug_port(port)

    async def _refuse_debug_port(self, port: int) -> None:
        if self._closed:
            raise BrowserError("the browser session was closed", {})
        if port == self._config.debug_port:
            return
        if port and await asyncio.to_thread(port_is_taken, port):
            raise port_taken(port)
        busy: list[JsonValue] = [*threads_in_use(self._threads, self._kept())]
        if busy:
            raise BrowserError("the browser is in use; wait for the running work to finish", {"threads": busy})

    async def switch(self, config: BrowserConfig, profile_dir: Path) -> None:
        """Work in the Chrome that ``config`` and ``profile_dir`` name from the next call on: the current one is let go
        of first (Dot's own Chrome is closed; in the Chrome of the user the tabs Dot opened are closed and the
        connection is dropped). Refused while a chat's run is at work in it (:func:`threads_in_use`), so no run loses
        the page it works on."""
        async with self._launch_lock:
            if self._closed:
                raise BrowserError("the browser session was closed", {})
            busy: list[JsonValue] = [*threads_in_use(self._threads, self._kept())]
            if busy:
                raise BrowserError("the browser is in use; wait for the running work to finish", {"threads": busy})
            await self._let_go()
            self._config = config
            self._profile = profile_dir
            _log.info("browser_switched", mode=config.mode, profile=str(profile_dir))

    async def let_go_of_chrome(self) -> None:
        """Close Dot's own Chrome now, so that its profile can open in a plain window where the owner signs in to sites
        (item 344); the next call that needs it starts it again. Refused while a chat's run works in it, as a switch is,
        and for the Chrome of the user, which is the owner's to close."""
        async with self._launch_lock:
            if self._closed:
                raise BrowserError("the browser session was closed", {})
            if self._config.mode != "own":
                raise BrowserError("only Dot's own Chrome is closed for signing in", {"mode": self._config.mode})
            busy: list[JsonValue] = [*threads_in_use(self._threads, self._kept())]
            if busy:
                raise BrowserError("the browser is in use; wait for the running work to finish", {"threads": busy})
            await self._let_go()
            _log.info("browser_let_go_for_sign_in", profile=str(self._profile))

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
        for task in [*self._adopting, *self._front_returns, *self._answering, *self._telling]:
            task.cancel()
        context: BrowserContext | None = self._context
        browser: Browser | None = self._browser
        self._context = None
        self._browser = None
        self._chrome_pid = None
        # Let go of on purpose: nothing went away behind Dot's back.
        self._lost = False
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
            # What Incognito kept goes with its Chrome (item 355).
            if self._config.mode == "own" and self._config.profile == INCOGNITO_PROFILE and context is not None:
                await asyncio.to_thread(empty_incognito)
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
        """Release the tabs of the threads not used for ``tab_idle_s``, and close Dot's own Chrome once it is
        unused (:meth:`_close_unused`), as long as the session is open. A thread whose tabs cannot be released, and a
        Chrome that cannot be closed, are logged and tried again at the next round; the other threads are still
        released."""
        while True:
            await asyncio.sleep(_REAP_INTERVAL_S)
            cutoff: float = time.monotonic() - self._config.tab_idle_s
            for thread_id in threads_to_release(self._threads, cutoff, self._kept()):
                _log.info("browser_idle_thread_released", thread_id=thread_id)
                try:
                    await self.release_thread(thread_id)
                except Exception:  # noqa: BLE001 - logged with its traceback
                    _log.exception("browser_idle_release_failed", thread_id=thread_id)
            try:
                await self._close_unused()
            except Exception:  # noqa: BLE001 - logged with its traceback
                _log.exception("browser_idle_close_failed", profile=str(self._profile))

    def _unused(self) -> bool:
        """Whether Dot's own Chrome, or the driver left from one the owner quit, has been unused for ``idle_close_s``:
        no chat's step is under way in it, no run at work holds tabs in it, no use that no chat holds is under way
        (:meth:`_in_use`), no task is being taught in it, and nothing used it for that long since it started."""
        config: BrowserConfig = self._config
        if config.mode != "own" or config.idle_close_s == 0 or self._playwright is None or self._closed:
            return False
        if self._uses > 0 or self._teaching is not None or threads_in_use(self._threads, self._kept()):
            return False
        used: float = max([self._used_at, *(thread.last_used for thread in self._threads.values())])
        return time.monotonic() - used >= config.idle_close_s

    async def _close_unused(self) -> None:
        """Close Dot's own Chrome and stop the driver once they are unused (:meth:`_unused`, item 454), unless its
        window is the one in front, where the owner may be using it. A Chrome left open keeps its memory, and macOS
        takes it for the owner's Google Chrome: the Dock's icon then brings it forward instead of opening theirs. The
        next call that needs it starts it again on the same profile; the chats' tabs go with it."""
        if not self._unused():
            return
        chrome: int | None = self._chrome_pid
        if chrome is not None and await asyncio.to_thread(front_pid) == chrome:
            return
        async with self._launch_lock:
            if not self._unused():
                return
            had_chrome: bool = self._context is not None
            await self._let_go()
        _log.info("browser_idle_closed", profile=str(self._profile), chrome=had_chrome)

    @contextlib.contextmanager
    def _in_use(self) -> Iterator[None]:
        """Count a use of Chrome that no chat's tabs hold while it lasts, and as the latest use once it ends, so that
        the idle close (:meth:`_unused`) does not close Chrome under it."""
        self._uses += 1
        try:
            yield
        finally:
            self._uses -= 1
            self._used_at = time.monotonic()

    async def attachment(self) -> Attachment:
        """The Chrome of the user that this session works in; connects first when it is not connected yet."""
        with self._in_use():
            context: BrowserContext = await self._ensure_context()
            browser: Browser | None = self._browser
            if browser is None:
                raise BrowserError(
                    "this session works in Dot's own Chrome, not in the open one", {"mode": self._config.mode}
                )
            return Attachment(browser_version=browser.version, open_tabs=len(context.pages))

    async def live_cookies(self) -> frozenset[tuple[str, str]] | None:
        """The host and name of every cookie Dot's own Chrome holds now (item 278); ``None`` while it does not run, and
        in attach mode, whose Chrome is the owner's own."""
        context: BrowserContext | None = self._context
        if context is None or self.mode != "own":
            return None
        try:
            with self._in_use():
                cookies: list[Cookie] = await context.cookies()
        except PlaywrightError as error:
            raise BrowserError(
                "the cookies of Dot's browser could not be listed",
                {"profile": str(self._profile), "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)},
            ) from error
        return frozenset((cookie.get("domain", ""), cookie.get("name", "")) for cookie in cookies)

    async def stored_cookie_count(self) -> int:
        """How many cookies Chrome could read from the profile; cookies it cannot decrypt are not counted."""
        try:
            with self._in_use():
                return len(await (await self._ensure_context()).cookies())
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
        if self._config.mode == "own":
            # Every page of Dot's own Chrome has its requests judged, whoever opened it: a popup opened with noopener
            # has no opener to adopt it by, and is a page of Dot's Chrome all the same.
            guarding: asyncio.Task[None] = asyncio.create_task(self._guard_quietly(page))
            self._answering.add(guarding)
            guarding.add_done_callback(self._answering.discard)
        task: asyncio.Task[None] = asyncio.create_task(self._adopt_popup(page))
        self._adopting.add(task)
        task.add_done_callback(self._adopting.discard)

    async def _adopt_popup(self, page: Page) -> None:
        """A page that a tab of ours opened belongs to the same thread; any other new page is not ours to touch. A popup
        that closes itself while this runs (a sign-in callback) is a note on its opener, not an error."""
        try:
            opener: Page | None = await page.opener()
        except Exception as error:  # noqa: BLE001 - _popup_lost raises it unless the page or the browser is gone
            self._popup_lost(error, page, None)
            return
        if opener is None:
            return
        owner: tuple[ThreadTabs, Tab] | None = self._owner_of(opener)
        if owner is None:
            return
        self._return_front()
        thread, opener_tab = owner
        tab: Tab = Tab(page=page)
        # Whatever becomes of it, the tab is no longer on its way to the opener's thread: a step that waits for it ends
        # (the review's S1), also when registering it failed once Chrome had told its id.
        try:
            try:
                await self._register(tab)
            except Exception as error:  # noqa: BLE001 - _popup_lost raises it unless the page or the browser is gone
                self._popup_lost(error, page, opener_tab)
                return
            if page.is_closed():
                add_note(opener_tab, closed_popup_note(page.url))
                return
            thread.tabs.append(tab)
            add_note(opener_tab, f"a new tab opened as tab {len(thread.tabs)}")
            await self._enforce_cap(thread)
        finally:
            taken_in(opener_tab, tab)

    def _keeps_front(self) -> bool:
        """Whether Dot gives the front of the Mac back when its own Chrome takes it (item 118): a visible window that
        may not be raised, whose process is known."""
        config: BrowserConfig = self._config
        return config.mode == "own" and not config.headless and not config.raise_window and self._chrome_pid is not None

    async def _note_front(self) -> None:
        """Remember the application that has the front as a thread begins to act, Dot's own Chrome included (the owner
        may be looking at it), for :meth:`_return_front`."""
        if self._keeps_front():
            self._front_before = await asyncio.to_thread(front_pid)

    def _return_front(self) -> None:
        """A tab a page opens itself brings Chrome to the front by Chrome's own doing: the application that had the
        front when the thread began to act gets it back, unless that was Dot's Chrome itself. The check runs beside
        the step, which does not wait for it."""
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

    async def _register(self, tab: Tab) -> None:
        page: Page = tab.page
        attach_handlers(tab, self._downloads)
        page.on("close", partial(self._on_page_closed, tab))
        await watch_births(tab)
        if self._config.mode == "attach":
            await keep_focused(page)
        else:
            await self._guard_hops(page)
        if self._config.decline_cookie_banners:
            await install_consent_handler(page)

    def _on_page_closed(self, tab: Tab, _page: Page) -> None:
        self._refusals.pop(tab.page, None)
        self._teach_armed.discard(tab.page)
        if self._teaching is not None and self._teaching.page is tab.page:
            # The tab the owner showed the task in is gone: there is nothing more to record.
            self._teaching = None
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
            tab: Tab = Tab(page=page)
            await self._register(tab)
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
            browser: Browser | None = self._browser
            try:
                yield thread
            except Exception as error:
                # The Chrome of the user went away under the step (item 341): one clear line instead of what
                # Playwright says of a closed target.
                if browser is not None and browser is not self._browser and self._lost:
                    raise BrowserError(
                        f"{CHROME_RESTARTED} while this step ran; the next step connects again: click Allow when "
                        "Chrome asks",
                        {"error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)},
                    ) from error
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
        """The page area the model sees, in CSS pixels: the configured size in Dot's own Chrome at 100 %, what the page
        measures where it fills its window (item 354), is drawn larger or smaller (item 358: 1280 by 860 at 125 % is
        1024 by 688) or is in the open Chrome."""
        config: BrowserConfig = self._config
        if config.mode == "own" and not config.fit_window and config.zoom == _UNZOOMED:
            return Viewport(width=config.viewport_width, height=config.viewport_height)
        return await measure_viewport(tab.page)

    async def _capture(self, tab: Tab) -> _Capture:
        config: BrowserConfig = self._config
        # The window's size is read alongside the snapshot, not before it (item 342): in the Chrome of the user it is
        # one more round trip to the page on every capture, and the two reads do not depend on each other.
        viewport, lines = await asyncio.gather(
            self._viewport(tab), capture_lines(tab.page, _ms(config.action_timeout_s))
        )
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
        """The rules on an address before the browser is sent to it, as the model wrote it: a refusal this early costs
        no request. Chrome reads the address again on the way (:meth:`_on_request`), in its own spelling."""
        verdict = check_browser_url(url, self._config.allowed_hosts, self._config.blocked_hosts, self._server)
        if not verdict.allowed:
            raise ForbiddenUrlError(_FORBIDDEN_MESSAGE, url, verdict.reason, "document")

    # ------------------------------------------------------------------ the rules on what Chrome sends

    def _judge(self, url: str, navigation: bool, resource_type: str, page: Page | None) -> UrlVerdict:
        """The rules on one request Chrome is about to send; ``page`` the page whose main frame it navigates, when it
        is one, so that the error page it then shows can be told from a page that failed to load. A rule that fails on
        an address refuses it: the request must be answered either way, or Chrome holds it for good."""
        try:
            verdict: UrlVerdict = request_verdict(
                url, navigation, self._config.allowed_hosts, self._config.blocked_hosts, self._server
            )
        except Exception:  # noqa: BLE001 - logged with its traceback, and the request is refused
            _log.exception("browser_request_unjudged", url=truncate_middle(url, _LAUNCH_ERROR_CHARS))
            verdict = UrlVerdict(allowed=False, reason="the URL rules could not read this address")
        if not verdict.allowed:
            if page is not None:
                self._refusals[page] = _Refusal(url=url, reason=verdict.reason)
            _log.info(
                "browser_request_refused",
                url=truncate_middle(url, _LAUNCH_ERROR_CHARS),
                resource_type=resource_type,
                reason=verdict.reason,
            )
        return verdict

    async def _on_request(self, route: Route, request: Request) -> None:
        """The route of Dot's own Chrome: a request the rules refuse fails in the page as blocked by the client; the
        rest go on. A page that closed under its request has nothing left to answer."""
        verdict: UrlVerdict = self._judge(
            request.url, request.is_navigation_request(), request.resource_type, main_page_of(request)
        )
        try:
            if verdict.allowed:
                await route.continue_()
            else:
                await route.abort(_ABORT_REASON)
        except PlaywrightError as error:
            _log.info(
                "browser_request_unanswered",
                url=truncate_middle(request.url, _LAUNCH_ERROR_CHARS),
                error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
            )

    async def _guard_hops(self, page: Page) -> None:
        """Ask Chrome about every request of ``page`` through a Fetch session of the tab's own, redirect hops included:
        the route of the context is asked about the first address of a navigation only (measured 2026-10-15: of
        localhost → 302 → Dot's port, the route saw localhost and Chrome fetched Dot's port). A frame of another site,
        which Chrome runs as a target of its own, is not reached from here; the route sees its first requests.

        The session is set up once per page, and every call returns only once Chrome has it: a navigation that starts
        before then is not asked about at all (measured 2026-10-16: the guard begun when Chrome announced a new tab was
        still under way when the tab's first navigation started, and a redirect hop of one such navigation in five
        reached Dot's port)."""
        guarding: asyncio.Task[None] | None = self._guarded.get(page)
        if guarding is None:
            guarding = asyncio.create_task(self._set_up_guard(page))
            self._guarded[page] = guarding
            page.on("close", self._forget_page)
        await asyncio.shield(guarding)

    async def _set_up_guard(self, page: Page) -> None:
        """The Fetch session of :meth:`_guard_hops`. One that could not be set up is forgotten, so that the next call
        tries again. Closing the browser does not cancel it: it fails as the browser goes, and its callers get that
        failure rather than a cancellation of their own."""
        try:
            cdp: CDPSession = await page.context.new_cdp_session(page)
            main: str | None = main_frame_id(await cdp.send("Page.getFrameTree"))
            cdp.on("Fetch.requestPaused", partial(self._on_paused, cdp, page, main))
            await cdp.send("Fetch.enable", {"patterns": _FETCH_PATTERNS})
        except PlaywrightError:
            self._guarded.pop(page, None)
            raise

    async def _guard_quietly(self, page: Page) -> None:
        """:meth:`_guard_hops` for a page that nothing waits for: one that closed meanwhile needs no guard, and one
        that could not be guarded is logged (the route still judges its first requests)."""
        try:
            await self._guard_hops(page)
        except PlaywrightError as error:
            if page.is_closed():
                return
            _log.warning(
                "browser_page_unguarded",
                url=truncate_middle(page.url, _LAUNCH_ERROR_CHARS),
                error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
            )

    def _forget_page(self, page: Page) -> None:
        self._guarded.pop(page, None)
        self._refusals.pop(page, None)

    def _on_paused(self, cdp: CDPSession, page: Page, main: str | None, event: Mapping[str, object]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(self._answer_paused(cdp, page, main, event))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)

    async def _answer_paused(self, cdp: CDPSession, page: Page, main: str | None, event: Mapping[str, object]) -> None:
        paused: _Paused | None = paused_request(event)
        if paused is None:
            # A request that cannot be read is refused rather than held, when it can be answered at all.
            _log.warning("browser_hop_unreadable", fields=sorted(event))
            request_id: object = event.get("requestId")
            if isinstance(request_id, str):
                await self._answer_hop(cdp, page, request_id, "", False)
            return
        # The main frame's navigation is the page's; when its id is not known, any navigation is taken for it.
        navigated: Page | None = page if paused.navigation and main in (None, paused.frame_id) else None
        verdict: UrlVerdict = self._judge(paused.url, paused.navigation, paused.resource_type, navigated)
        await self._answer_hop(cdp, page, paused.request_id, paused.url, verdict.allowed)

    async def _answer_hop(self, cdp: CDPSession, page: Page, request_id: str, url: str, allowed: bool) -> None:
        """Let the request ``request_id`` go on, or fail it as blocked by the client. A page that closed under its
        request, or that cancelled or finished the request before this answer came (:data:`_REQUEST_GONE`), has nothing
        left to answer: the first is not logged and the second only at debug level, as it happens with the ordinary
        traffic of a page. Any other failure to answer is a warning."""
        try:
            if allowed:
                await cdp.send("Fetch.continueRequest", {"requestId": request_id})
            else:
                await cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": _FAIL_REASON})
        except PlaywrightError as error:
            if page.is_closed():
                return
            if _REQUEST_GONE in str(error):
                _log.debug("browser_hop_gone", url=truncate_middle(url, _LAUNCH_ERROR_CHARS), allowed=allowed)
                return
            _log.warning(
                "browser_hop_unanswered",
                url=truncate_middle(url, _LAUNCH_ERROR_CHARS),
                error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
            )

    def _refused_navigation(self, error: BrowserError, page: Page) -> ForbiddenUrlError | None:
        """The refusal behind a failed navigation of ``page``, ``None`` when it failed for another reason: Chrome says
        a request was blocked, and the navigation the rules refused on that page says which and why. Without such a
        refusal the block is another's (an extension of the profile blocks the same way, item 264) and the failure
        is reported as it is."""
        if not blocked_by_rules(error):
            return None
        refusal: _Refusal | None = self._refusals.pop(page, None)
        if refusal is None:
            return None
        return ForbiddenUrlError(_FORBIDDEN_MESSAGE, refusal.url, refusal.reason, "document")

    async def _navigate_checked(self, page: Page, url: str, attempts: int) -> int | None:
        """:func:`navigate`, with a navigation the rules refused on the way reported as such."""
        config: BrowserConfig = self._config
        try:
            return await navigate(page, url, _ms(config.navigation_timeout_s), attempts, config.navigation_backoff_ms)
        except BrowserError as error:
            refused: ForbiddenUrlError | None = self._refused_navigation(error, page)
            if refused is None:
                raise
            raise refused from error

    async def _blank(self, page: Page) -> None:
        """Reset ``page`` to a blank page after a refusal. Chrome's error page for the refused request can commit after
        the refusal is reported and interrupt the reset, which is then tried again as any interrupted navigation is."""
        config: BrowserConfig = self._config
        await navigate(
            page,
            "about:blank",
            _ms(config.navigation_timeout_s),
            config.navigation_attempts,
            config.navigation_backoff_ms,
        )

    async def _enforce_landing(self, tab: Tab) -> None:
        """A redirect or click may have ended on a URL the rules forbid; leave it before anything is read from it. One
        the rules refused on the way shows Chrome's error page, which is left the same way."""
        landed: str = tab.page.url
        config: BrowserConfig = self._config
        if landed.startswith("chrome-error://"):
            refused: _Refusal | None = self._refusals.pop(tab.page, None)
            if refused is None:
                add_note(tab, "the page failed to load (Chrome error page)")
                return
            await self._blank(tab.page)
            raise ForbiddenUrlError(
                "the page was sent to a URL that may not be opened; the request was refused and the tab was reset to "
                "a blank page",
                refused.url,
                refused.reason,
                "document",
            )
        verdict = check_browser_url(landed, config.allowed_hosts, config.blocked_hosts, self._server)
        if verdict.allowed:
            # The page is where it may be: a refusal on the way there, which it then left, is over.
            self._refusals.pop(tab.page, None)
            return
        await self._blank(tab.page)
        raise ForbiddenUrlError(
            "the page ended up on a URL that may not be opened; the tab was reset to a blank page",
            landed,
            verdict.reason,
            "document",
        )

    async def _load(self, thread: ThreadTabs, tab: Tab, url: str) -> PageView:
        self._require_allowed(url)
        config: BrowserConfig = self._config
        tab.status = None
        try:
            status: int | None = await self._navigate_checked(tab.page, url, config.navigation_attempts)
        except ForbiddenUrlError as refused:
            # The tab shows Chrome's error page for the refused request; it is left as a page the rules forbid is.
            await self._blank(tab.page)
            raise ForbiddenUrlError(
                f"{refused.message}; the tab was reset to a blank page",
                refused.url,
                refused.reason,
                refused.resource_type,
            ) from refused
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
        """Whether a page may be read in a tab the user does not work in: a scratch tab of Dot's own Chrome, or in the
        user's Chrome a tab in a window of its own behind the user's (items 98 and 340), closed after the read. True in
        both modes."""
        return True

    @property
    def human_like(self) -> bool:
        """Whether a page read here passes the bot checks a plain request meets: Dot's own Chrome with a visible window.
        A headless Chrome is told apart (measured 2026-10-11: DuckDuckGo shows it a CAPTCHA and a visible Chrome its
        results), so it does not count; the user's Chrome is not used for searches."""
        return self._config.mode == "own" and not self._config.headless

    async def read_page(self, url: str) -> RenderedDocument:
        """Read ``url`` (:meth:`_read_page`) as a use of Chrome that the idle close waits for."""
        with self._in_use():
            return await self._read_page(url)

    async def _read_page(self, url: str) -> RenderedDocument:
        """Read ``url`` in a tab of its own, one no thread owns and that closes when the read is over: the address the
        page ended on, the status of its last main response and its HTML after JavaScript ran and the page settled. A
        page that answered with a bot wall gets a few seconds to pass its own check first. For the web tools, when a
        plain request was turned away. In the user's Chrome the tab opens in a window of its own behind the user's
        (items 98 and 340), which costs about 30 ms more to make than a tab."""
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
            if config.mode == "own":
                await self._guard_hops(page)
            first: int | None = await self._navigate_checked(page, url, config.navigation_attempts)
            if first is None:
                raise BrowserError("the address started a download, not a page", {"url": url})
            if first in _CHALLENGE_STATUS:
                with contextlib.suppress(PlaywrightError):
                    await page.wait_for_event("load", timeout=_CHALLENGE_WAIT_MS)
            await wait_dom_quiet(page, config.settle_quiet_ms, config.settle_max_ms, _ms(config.navigation_timeout_s))
            landed: str = page.url
            refused: _Refusal | None = self._refusals.pop(page, None)
            if refused is not None and landed.startswith("chrome-error://"):
                raise ForbiddenUrlError(
                    "the page was sent to a URL that may not be opened; the request was refused",
                    refused.url,
                    refused.reason,
                    "document",
                )
            verdict = check_browser_url(landed, config.allowed_hosts, config.blocked_hosts, self._server)
            if not verdict.allowed:
                raise ForbiddenUrlError(
                    "the page ended up on a URL that may not be opened", landed, verdict.reason, "document"
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
        Chrome; a tab made this way does neither, and it still takes screenshots. In the Chrome of the user it opens in
        a window of its own (item 340), which keeps Dot's tabs out of the user's windows: made in the background, it
        stays behind the window the user works in, also when Chrome is the app in front, and leaves the app in front as
        it was. Either way the page runs at full speed once ``keep_focused`` has it (measured 2026-10-17: a tab made in
        the background draws no frame and ticks its timers 8 times a second until then, 61 frames and 208 ticks after,
        as in a window of its own); the window costs about 30 ms more to make than a tab. ``new_page`` stays for a
        context without a browser at hand."""
        browser: Browser | None = context.browser
        if browser is None:
            return await context.new_page()
        # Each tab is told apart by an address of its own: tabs made at the same time (a crawl reads pages side by side)
        # would otherwise all be handed the first new page (measured 2026-10-12: five calls, one page). It goes back to
        # a plain blank page at once, the address the URL rules and the tab views know.
        mark: str = f"about:blank#dot-{secrets.token_hex(_TAB_MARK_BYTES)}"
        target: dict[str, object] = {"url": mark, "background": True}
        if self._config.mode == "attach":
            target["newWindow"] = True
        cdp: CDPSession = await browser.new_browser_cdp_session()
        try:
            async with context.expect_page(lambda page: page.url == mark) as opened:
                await cdp.send("Target.createTarget", target)
            page: Page = await opened.value
        finally:
            await cdp.detach()
        await page.goto("about:blank", wait_until="commit")
        return page

    async def _new_tab(self, context: BrowserContext) -> Page:
        """A thread's new tab: in the background, unless the window may be raised (item 97), when it is the window's
        visible tab as before."""
        if self._config.raise_window:
            return await self._dressed(await context.new_page())
        return await self._dressed(await self._background_tab(context))

    async def _scratch_page(self, context: BrowserContext) -> Page:
        """A new tab for a quiet read, always in the background: the tab the user works in, and the application in
        front, keep the front."""
        return await self._dressed(await self._background_tab(context))

    async def _dressed(self, page: Page) -> Page:
        """``page`` told the colour scheme Settings › Browser chose (item 354): Dot's own Chrome tells each of its pages
        from its start, the Chrome of the user only the tabs Dot opens in it, one by one."""
        if self._config.mode == "attach" and self._config.appearance != "system":
            await page.emulate_media(color_scheme=color_scheme(self._config.appearance))
        return page

    async def _close_scratch(self, page: Page) -> None:
        self._refusals.pop(page, None)
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
            shot: bytes = await screenshot_jpeg(tab.page, config.screenshot_quality, _ms(config.action_timeout_s))
            # Chrome's zoom is left out of Playwright's CSS pixels: the picture is redrawn at the page's own size.
            data: bytes = await asyncio.to_thread(
                jpeg_of_size, shot, viewport.width, viewport.height, config.screenshot_quality
            )
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

    @property
    def teaching(self) -> Recording | None:
        """The task the owner is showing Dot now, or ``None`` when nothing is being recorded."""
        return self._teaching

    async def start_teaching(self, thread_id: str) -> Recording:
        """Start recording the steps the owner takes in the thread's current tab (item 423): the recorder's script goes
        into the page, the tab comes to the front, and every step they take there is kept until :meth:`stop_teaching`
        or the tab closes. A browser without a window refuses: there is nothing for the owner to work in."""
        if self._config.headless:
            raise BrowserError(
                "a task cannot be shown in a browser without a window: [browser] headless is on, so Dot's Chrome has "
                "none; turn it off in the config file and start the daemon again",
                {"headless": True},
            )
        if thread_id in self._kept():
            raise RunConflict(
                "a run is working in this chat now: show the task while none is, so that the steps recorded are "
                "yours alone",
                {"thread_id": thread_id},
            )
        async with self._teach_lock:
            if self._teaching is not None:
                raise RunConflict("a task is being recorded already", {"thread_id": self._teaching.thread_id})
            async with self._locked(thread_id) as thread:
                tab: Tab = await self._current_tab(thread)
                recording: Recording = Recording(thread_id=thread_id, page=tab.page, tab_index=thread.active + 1)
                # In place before the script goes in: the page it is put into reports itself at once, and a step that
                # comes before the recording would be dropped.
                self._teaching = recording
                try:
                    await self._arm_teaching(tab.page)
                except Exception:
                    # Nothing is recorded, and the next start must not meet a recording that was never one.
                    self._teaching = None
                    raise
                recording.opened(tab.page.url)
                await bring_to_front(tab.page)
                return recording

    def stop_teaching(self) -> Recording | None:
        """End the recording and return it: its steps are what the owner showed. The page keeps the script that reported
        them, which reports nothing while no recording is on."""
        recording, self._teaching = self._teaching, None
        return recording

    async def _arm_teaching(self, page: Page) -> None:
        """Put the recorder's binding and its script into ``page``, once: the script runs again in every document the
        tab loads, and the binding outlives them."""
        if page in self._teach_armed:
            return
        await page.expose_binding(TEACH_BINDING, self._taught)
        # Marked before the script goes in: the binding is in the page now, and a second one would be refused.
        self._teach_armed.add(page)
        await page.add_init_script(TEACH_SCRIPT)
        try:
            await page.evaluate(TEACH_SCRIPT)
        except PlaywrightError as error:
            # The document may still be loading: the script is among the page's init scripts now and runs at its next
            # load. Steps of this document alone are lost, and the panel shows the first one it gets.
            _log.warning("teach_script_not_put", url=page.url, error=str(error))

    def _taught(self, source: dict[str, object], payload: str) -> None:
        """What the recorder in a page reports: a step of the recording, when it is the recorded page and no run works
        in that chat. A run's own clicks are real ones, and they are not the owner's steps, and neither is anything an
        iframe says: the binding is in every frame of the page, and a reCAPTCHA's or an advert's words are not the task
        the owner showed (item 423)."""
        recording: Recording | None = self._teaching
        if recording is None:
            return
        frame: object | None = source.get("frame")
        if frame is not None and frame is not recording.page.main_frame:  # type: ignore[attr-defined]
            return
        if recording.thread_id in self._kept():
            _log.warning("teach_step_dropped_run_at_work", thread_id=recording.thread_id)
            return
        recording.take(source, payload)

    async def screencast(self, thread_id: str) -> AsyncGenerator[Frame | None]:
        """Frames of the thread's current tab as Chrome paints it (``Page.startScreencast``), for as long as the caller
        reads them: a tab that does not change sends nothing, and ``None`` comes once a second in between so the caller
        can check whether it still wants frames. The cast follows the current tab when the model switches or closes it,
        waits while the thread has no tab yet, and ends when the browser is closed."""
        # The owner watches the tab: a use of Chrome the idle close waits for (item 454).
        with self._in_use():
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
                    # The page closed under the step (a callback that did its job, a tab the user closed): not a
                    # failure.
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
            opening: bool = False
            if step.has_side_effects():
                opening = await self._let_events_land(tab)
            reports.append(StepReport(index=index, description=description, outcome="done", detail=""))
            stopped = describe_surprise(before, self._watch(thread, tab))
            if stopped is None and opening:
                stopped = _TAB_OPENING
        return reports, stopped

    def _watch(self, thread: ThreadTabs, tab: Tab) -> _Watch:
        return _Watch(url=tab.page.url, tabs=len(thread.tabs), events=tab.events, closed=tab.page.is_closed())

    async def _let_events_land(self, tab: Tab) -> bool:
        """Let what a step on ``tab`` set off land before the batch goes on (item 342), and return whether a tab Chrome
        made for it is still on its way. The page takes one turn of its event loop (:func:`let_page_turn`), and a tab
        Chrome made meanwhile is waited for until its thread has it (:meth:`_let_births_land`). No fixed wait: a step
        that sets off nothing costs the turn alone, a frame of the page (measured: 13 ms after a click, against the
        40 ms grace before), and a tab whose page answers late stops the batch all the same, where the grace let the
        batch go on: Playwright reports a tab only once its page has answered (411 ms after the click for a page that
        answers in 400 ms), while Chrome tells of it before the click returns."""
        await let_page_turn(tab.page, _ms(self._config.action_timeout_s))
        return await self._let_births_land(tab)

    async def _let_births_land(self, tab: Tab) -> bool:
        """Wait until each tab Chrome made for ``tab`` has been taken in by its thread or has closed, ``_POPUP_WAIT_S``
        at most, and return whether one was still on its way then. That one is let go of, so that it holds no later
        step; it joins the thread when Playwright reports it, with its note on ``tab``."""
        try:
            async with asyncio.timeout(_POPUP_WAIT_S):
                while tab.born:
                    tab.born_changed.clear()
                    await tab.born_changed.wait()
        except TimeoutError:
            _log.warning(
                "browser_new_tab_not_taken_in",
                url=truncate_middle(tab.page.url, _LAUNCH_ERROR_CHARS),
                tabs=len(tab.born),
                waited_s=_POPUP_WAIT_S,
            )
            tab.born.clear()
            return True
        return False

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
        pixels: int | None,
        ref: str | None,
    ) -> PageView:
        """Scroll the current tab by ``pixels``, or by most of the page area the model sees when ``pixels`` is None."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            viewport: Viewport = await self._viewport(tab)
            distance: int = pixels if pixels is not None else round(viewport.height * _SCROLL_SHARE)
            await scroll_page(
                tab.page, direction, distance, ref, viewport.width, viewport.height, _ms(self._config.action_timeout_s)
            )
            # The wheel lands a few tens of milliseconds after the call returns, inside the DOM quiet window.
            await self._settle(tab)
            return await self._changes_view(thread, tab)

    async def wait_text(self, thread_id: str, text: str, timeout_s: float) -> TextWait:
        """Wait until ``text`` is visible on the current tab, at most ``timeout_s``; the page is shown either way.
        After a wait that timed out the page is shown as it is, without a settle that could outlast the wait. A
        navigation still pending then holds the reading until it commits, so the URL rules are checked again once the
        page is read."""
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
            current: Tab
            opening: bool
            if end == "timeout":
                # The script holds the page still: it takes no turn, and the tabs it opened are what can land.
                opening = await self._let_births_land(tab)
                current = await self._current_tab(thread)
                await self._let_commit(current)
            else:
                opening = await self._let_events_land(tab)
                current = await self._current_tab(thread)
                await self._settle(current)
            surprise: str | None = describe_surprise(before, self._watch(thread, tab))
            await self._enforce_landing(current)
            return ScriptRun(
                end=end,
                text=text,
                url=current.page.url,
                surprise=_TAB_OPENING if surprise is None and opening else surprise,
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
