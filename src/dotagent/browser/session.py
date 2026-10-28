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
import math
import re
import secrets
import time
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal
from urllib.parse import urldefrag, urlsplit

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

from dotagent.browser import ledger
from dotagent.browser.attach import INSPECT_PAGE, DebugEndpoint, endpoint_url, read_debug_endpoint, redact_endpoint
from dotagent.browser.console import console_lines
from dotagent.browser.devtools import devtools_switches, port_is_taken, port_taken
from dotagent.browser.driver import (
    PageLines,
    Pulse,
    TextWaitEnd,
    bring_to_front,
    capture_page,
    cookie_secrets,
    document_of,
    fill_login_field,
    go_back,
    holds_field_focus,
    holds_secret_field,
    install_consent_handler,
    keep_focused,
    let_page_turn,
    measure_viewport,
    navigate,
    no_dialog_open,
    page_pulse,
    page_title,
    run_script,
    run_step,
    screenshot_jpeg,
    scroll_page,
    upload_files,
    visible_text,
    wait_dom_quiet,
    wait_for_text_or_dialog,
)
from dotagent.browser.extract import extract_records, render_records
from dotagent.browser.keychain import KeychainMode, keychain_switches, playwright_defaults_to_drop, read_keychain_mode
from dotagent.browser.look import PageLook, color_scheme
from dotagent.browser.network import Fetched, answer_text, call_lines, hidden_address
from dotagent.browser.pages import Tab, ThreadTabs, add_note, attach_handlers, taken_in, watch_births
from dotagent.browser.profile_lock import held_for_start, profile_holder_pid
from dotagent.browser.profiles import (
    HIDE_CRASH_BUBBLE,
    INCOGNITO_PROFILE,
    clear_session_files,
    empty_incognito,
    keep_session_cookies,
    profile_dir,
    write_default_zoom,
)
from dotagent.browser.remote_input import InputEvent, InputResult, MouseButton, ScreenHold, dispatch, let_go
from dotagent.browser.render import describe_step
from dotagent.browser.safety import UrlVerdict, check_browser_url, request_verdict
from dotagent.browser.snapshot import (
    HIDDEN_VALUE,
    SnapshotLine,
    controls_near,
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
    HeldValue,
    PageView,
    Screenshot,
    ScriptRun,
    SecretFieldCheck,
    Step,
    StepReport,
    TabSummary,
    TextView,
    TextWait,
    VaultFieldKind,
    VaultFill,
    Viewport,
)
from dotagent.browser.upstream import ignore_repeated_frame_detach
from dotagent.browser.window import chrome_to_watch, focused_app, front_pid, give_focus_back, return_front
from dotagent.config import BrowserConfig, ServerConfig
from dotagent.core.errors import (
    ApprovalParked,
    ApprovalRejected,
    BrowserError,
    DotError,
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
from dotagent.vault.hosts import SameOrigin
from dotagent.vault.masking import in_address, mask_error, masked
from dotagent.web.addresses import is_private_text, non_public_address

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
# Where a task shown on a blank tab starts (item 510): a page to search from, rather than an empty one.
_TEACH_START_PAGE: str = "https://www.google.com/"
_TAB_MARK_BYTES: int = 6
# Dot's own Chrome at this zoom draws a page at the size it was given, which the page need not be asked for (item 358).
_UNZOOMED: int = 100
# The height of a tab given a width alone (``browser_viewport``, item 746): a phone's, 390 by 844 being an iPhone's.
_PHONE_HEIGHT: int = 844
# A scroll with no distance moves by this share of the page area the model sees.
_SCROLL_SHARE: float = 0.8
# How long input from the screen waits for a step of the run that is under way when the owner took it over (item 470),
# how long the page is given to measure itself and take a batch of it, and to let go of a button it left down (item
# 479): a page that does not answer holds nothing up longer.
_INPUT_WAIT_S: float = 2.0
_INPUT_DO_S: float = 3.0
_LET_GO_S: float = 1.0

_LAUNCH_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    HIDE_CRASH_BUBBLE,
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
# Chrome's own error page, which a tab shows where a page failed to load or a request of it was refused.
_ERROR_PAGE: str = "chrome-error://"
_LOAD_FAILED_NOTE: str = "the page failed to load (Chrome error page)"
# What the tab says of itself when the owner took the run over after a script of the run ran in the chat (item 554).
_SCRIPTED_NOTE: str = (
    "a script of this run ran in this chat's tabs and could have copied what is typed there: when the user took over, "
    "the other tabs were closed and this page was opened again"
)
# How long after an Allow nobody clicked the Chrome of the user is not asked again (item 699): a run of a schedule
# that fires with nobody there waited attach_timeout_s at each of its browser calls, nine minutes in one run.
UNANSWERED_HOLD_S: float = 300.0
# What ended a wait for a text that a dialog interrupted (item 698).
_DIALOG_OPENED: str = "a dialog opened in front of the page"
# A wait for a text ends early once the page stands still (item 774): no change of its documents for _TEXT_STILL_S, no
# navigation, no request started or under way and no WebSocket open, once the wait has gone on for _TEXT_STILL_AFTER_S.
# Over 30 days of the owner's runs 64 waits saw their text, half of them within 0.4 s and 60 within 5 s, while 42 that
# did not lasted their whole time, 20 s at the median: a guess of a thank-you text after a form that had not gone. The
# page is looked at every _TEXT_SLICE_S.
_TEXT_STILL_S: float = 3.0
_TEXT_STILL_AFTER_S: float = 5.0
_TEXT_SLICE_S: float = 0.5
# Why a chat's tabs were made new before a login was filled (item 697).
_FRESH_FOR_LOGIN_NOTE: str = (
    "a script of this run ran in this chat's tabs and could copy what is typed there: before the login, Dot closed "
    "them and opened this page again in a new tab"
)
# The title a tab list gives a tab on a URL the rules forbid, which is not read (item 554).
_UNREAD_TITLE: str = "(not read: a URL that may not be opened; switching to the tab resets it)"
# The longest address a note on a tab carries whole.
_SHOWN_URL_CHARS: int = 300
# The schemes of a frame's address that the URL rules judge: the others (about:, data:, blob:, an extension's, Chrome's
# error page) reach no server, or are not a site's.
_WEB_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# A request a page sends whose address carries a password or a one-time code, from the password manager (item 493) or
# Dot's vault (item 621): an address is written into Chrome's history and Dot's log, and shortened where it is long.
_CARRIES_SECRET: str = (
    "its address would carry a password or a code that Dot filled in from your password manager or vault"
)
# How long after it went in a one-time code is kept out of addresses: it opens nothing past its few minutes.
_CODE_HELD_S: float = 300.0
# The parts of a login whose value is masked in what the tools of the chat give back (item 621).
_MASKED_PARTS: frozenset[VaultFieldKind] = frozenset({"username", "password", "otp"})
# The parts that no request a page sends may carry in its address, and those of them that are kept out for a few
# minutes only.
_KEPT_OUT_OF_ADDRESSES: frozenset[VaultFieldKind] = frozenset({"password", "otp"})
_KEPT_OUT_FOR_A_WHILE: frozenset[VaultFieldKind] = frozenset({"otp"})
# The parts whose value a field of a later document is marked for holding (``remark_held``): the secret ones.
_REMARKED_PARTS: frozenset[VaultFieldKind] = frozenset({"password", "otp"})
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


def sending_document(request: Request) -> str | None:
    """The address of the document that sent ``request``: the one its frame shows now (:func:`document_of`). ``None``
    when none can be named: a request of no frame (a service worker's), the first request of a popup, whose frame
    Playwright does not know yet, a blank main frame. What the guard does by a value filled into a page it does to the
    requests of the documents of the origin the value was typed into, in the tab it was typed into, and to no other
    (:meth:`BrowserSession._judge`)."""
    try:
        frame: PlaywrightFrame = request.frame
    except PlaywrightError:
        return None
    return document_of(frame)


def sending_tab(request: Request) -> Page | None:
    """The page whose frame sent ``request``; ``None`` when no frame can be named (:func:`sending_document`). The guard
    holds a request to a value only when the page it comes from is a tab the value was typed into
    (:meth:`BrowserSession._kept_out_of`)."""
    try:
        frame: PlaywrightFrame = request.frame
    except PlaywrightError:
        return None
    return frame.page


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


def idle_background_tabs(thread: ThreadTabs, cutoff: float) -> list[Tab]:
    """The tabs of ``thread`` that are not its current one and that no call has had current since ``cutoff``: the
    pages a run went on from (item 696). Only the thread's own tabs are looked at, so a chat never closes a tab of
    another chat, or of the owner."""
    return [tab for index, tab in enumerate(thread.tabs) if index != thread.active and tab.last_used < cutoff]


def _none_unattended(thread_id: str) -> bool:
    """No chat is taken for one whose run nobody watches, unless the pool says so (:meth:`BrowserSession.watch_for`)."""
    return False


def no_runs_at_work() -> frozenset[str]:
    """The chats a session works for when no run does (``dotx browser``): none, so every idle chat is released."""
    return frozenset()


def _within(frame: PlaywrightFrame, frames: Sequence[PlaywrightFrame]) -> bool:
    """Whether ``frame`` is one of ``frames`` or lies inside one of them."""
    current: PlaywrightFrame | None = frame
    while current is not None:
        if current in frames:
            return True
        current = current.parent_frame
    return False


def _judged_in_frame(url: str) -> bool:
    """Whether the URL rules judge a frame at ``url``: an address of the web, or one that cannot be read as any. The
    other schemes (``about:``, ``data:``, ``blob:``, an extension's, Chrome's error page) reach no server of the web."""
    try:
        return urlsplit(url).scheme in _WEB_SCHEMES
    except ValueError:
        return True


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
    # Says that the page was read only in part (item 743); empty for a whole read.
    partial: str = ""


# The methods of a request that only reads, and how much of what a script's page sent its value says (item 741).
_READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_SENT_LINES: int = 10
_SENT_CHARS: int = 200
# How long a step that should change the page is watched for a change after it (item 739), and how often: only a
# step that changed nothing waits that long.
_EFFECT_WAIT_S: float = 0.5
_EFFECT_POLL_S: float = 0.1
# The steps that are meant to change the page: a click, or Enter, which sends a form.
_MOVING_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "click_at", "drag_at"})


def _moves_the_page(step: Step) -> bool:
    """Whether ``step`` is meant to change the page, so that a step that does not is told (item 739)."""
    return step.action in _MOVING_ACTIONS or (step.action == "press" and (step.key or "").lower() == "enter")


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


def _note_main_status(page: Page, response: Response, statuses: list[int], documents: list[Response]) -> None:
    """Keep the status of each main-frame document response, so a bot wall that reloads into the page is read right,
    and the response itself, which says what Chrome connected to (:meth:`BrowserSession._require_public`)."""
    if response.frame == page.main_frame and response.request.is_navigation_request():
        statuses.append(response.status)
        documents.append(response)


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
        # The values filled into each thread's pages from the password manager (item 493), which no result of the
        # browser tools of the thread shows: kept apart from its tabs, which an idle thread lets go of while its chat
        # goes on, and for as long as the daemon runs.
        self._vault_values: dict[str, set[str]] = {}
        # The cookie values of the sites a thread's scripts ran in (item 533), which no result of the browser tools
        # shows either, kept as long as the daemon runs.
        self._cookie_values: dict[str, set[str]] = {}
        # The passwords and one-time codes among them, with the moment each stops being named in a refusal or a log line
        # (a password's never comes while the daemon runs): the words Dot writes of an address are made without them
        # (:meth:`_shown`), whatever tab they went into. Nothing is decided by this list.
        self._vault_secrets: dict[str, float] = {}
        # The same values by the tab each was typed into, with the moment each stops being kept out of addresses: no
        # request of a document of that tab's page, of the origin the value was typed into, carries one in its address
        # (:meth:`_judge`). Only these tabs are held to a value: no script of a run runs in one while it is open, so no
        # run can try addresses there and read which were refused. A tab that was never filled, in any chat and of any
        # origin, is judged by the rules on addresses alone (item 621, F3).
        self._kept_out: dict[Page, dict[HeldValue, float]] = {}
        # The profiles of Dot's own Chrome that a login was filled into since their session files were last deleted
        # (item 621): a form it sent by POST has its body in them, until Chrome has exited and they are deleted. By
        # profile, not a flag of the session: a switch to another profile, made while the Chrome of the one that was
        # filled had not yet exited, leaves that one to be cleaned when Chrome next starts on it, and the profile it
        # went to is not cleaned for it.
        self._filled_profiles: set[Path] = set()
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
        # When a wait for that Allow last ran out (item 699): for ``UNANSWERED_HOLD_S`` from then a connection for a
        # chat whose run nobody watches (``_unattended``, a schedule's or a watcher's) fails at once instead of waiting
        # ``attach_timeout_s`` again.
        self._unanswered_at: float | None = None
        self._unattended: Callable[[str], bool] = _none_unattended
        # Told when Dot connects again to a Chrome of the user that went away, before Chrome asks for its Allow, and the
        # tellings under way: the connection does not wait for them.
        self._on_reconnect: Callable[[], Awaitable[None]] | None = None
        self._telling: set[asyncio.Task[None]] = set()
        # The task the owner is showing Dot now (item 423): the one recording of the tab they work in, and the pages
        # whose recorder's binding and script are already there (a page keeps them across the documents it loads).
        self._teaching: Recording | None = None
        self._teach_armed: set[Page] = set()
        self._teach_lock: asyncio.Lock = asyncio.Lock()
        # Chrome's ids of the tabs this session has open in the Chrome of the user, kept in the ledger (item 703) so
        # that a Dot that did not end cleanly has them closed by the next; the writes go one at a time.
        self._dot_targets: set[str] = set()
        self._ledger_lock: asyncio.Lock = asyncio.Lock()
        self._ledger_writes: set[asyncio.Task[None]] = set()

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

    async def _ensure_context(self, unattended: bool = False) -> BrowserContext:
        """The browser context, connected or started first; ``unattended`` says the call is of a run nobody watches."""
        async with self._launch_lock:
            if self._closed:
                raise BrowserError("the browser session was closed", {})
            if self._context is not None:
                return self._context
            try:
                opened: _Opened = await self._open(unattended)
            except Exception as error:
                lost: Playwright | None = self._playwright
                if lost is None or not driver_lost(error):
                    raise
                await self._discard_driver(lost)
                raise connection_broke(error) from error
            return self._arm(opened)

    async def _open(self, unattended: bool) -> _Opened:
        match self._config.mode:
            case "own":
                return await self._launch_own()
            case "attach":
                return await self._attach(unattended)

    async def _launch_own(self) -> _Opened:
        """Start Dot's own Chrome (:meth:`_start_own`) with the profile held for the start (item 554): an import of
        sign-ins (``dotx browser import-chrome``) swaps files into a profile that no Chrome has open, and runs not at
        all while a Chrome is starting on it; a start is refused while an import is under way."""
        with held_for_start(self._profile):
            return await self._start_own()

    async def _start_own(self) -> _Opened:
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
        # No Chrome holds the profile: what a Chrome that ended on its own left of a form it sent goes (item 621).
        if self._profile in self._filled_profiles:
            await asyncio.to_thread(clear_session_files, self._profile)
            self._filled_profiles.discard(self._profile)
        # Incognito starts with nothing, however its last Chrome ended (item 355).
        if config.profile == INCOGNITO_PROFILE:
            await asyncio.to_thread(empty_incognito)
        # Chrome reads the zoom it draws pages at from the profile when it starts (item 358), and whether it gives the
        # cookies of its last run without an expiry back (item 453).
        await asyncio.to_thread(write_default_zoom, self._profile, config.zoom)
        await asyncio.to_thread(keep_session_cookies, self._profile)
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

    async def _attach(self, unattended: bool) -> _Opened:
        """Connect to the Chrome the user has open and work in the browser context it already has. Chrome asks the
        user to click Allow; the connection waits for the answer for ``attach_timeout_s``. A call of a run nobody
        watches (``unattended``) fails at once instead while an Allow nobody clicked is recent (item 699)."""
        config: BrowserConfig = self._config
        unanswered: float | None = self._unanswered_at
        if unattended and unanswered is not None and time.monotonic() - unanswered < UNANSWERED_HOLD_S:
            # Nobody clicked Allow a moment ago: a run of a schedule or a watcher does not wait for it again at every
            # call. A run the owner started still waits, as they may be there to click; after the hold, so does this.
            raise BrowserError(
                f"Chrome's Allow went unanswered {time.monotonic() - unanswered:.0f} s ago, and Dot asks again only "
                f"after {UNANSWERED_HOLD_S:g} s: nobody may be at the Mac. Go on without the browser where you can "
                "(the web tools read pages), or try the browser again later",
                {"data_dir": str(self._profile), "hold_s": UNANSWERED_HOLD_S},
            )
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
            timed_out: bool = isinstance(error, PlaywrightTimeoutError)
            if timed_out:
                self._unanswered_at = time.monotonic()
            waited: str = f" (no answer within {config.attach_timeout_s:g} s)" if timed_out else ""
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
        self._unanswered_at = None
        if not browser.contexts:
            await browser.close()
            raise BrowserError(
                "Chrome has no browser context to open tabs in", {"data_dir": str(self._profile), "port": endpoint.port}
            )
        context: BrowserContext = browser.contexts[0]
        browser.on("disconnected", self._on_browser_disconnected)
        self._browser = browser
        await self._close_left(browser)
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
        # The Chrome they were in is gone, and its ids with it.
        self._dot_targets.clear()
        self._initial_pages = []
        self._chrome_pid = None
        self._refusals.clear()
        self._guarded.clear()
        self._kept_out.clear()
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

    def watch_for(self, unattended: Callable[[str], bool]) -> None:
        """Tell the session which chats have a run nobody watches (item 699): a schedule's, a watcher's and their
        helpers'."""
        self._unattended = unattended

    def set_remote_input(self, remote_input: bool) -> None:
        """Whether the owner may use a held run's page from the screen (item 470), from the next input on."""
        self._config = self._config.model_copy(update={"remote_input": remote_input})
        _log.info("browser_remote_input", remote_input=remote_input)

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
            # The owner switched, so the owner is there to click Allow (item 699).
            self._unanswered_at = None
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
        # The ledger says, once its writes have landed, that this process has no tab left open (item 703).
        await asyncio.gather(*self._ledger_writes, return_exceptions=True)

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
            # What Incognito kept goes with its Chrome (item 355), also when that Chrome ended before Dot let go of it:
            # the owner quit it, or it crashed (item 554).
            if self._config.mode == "own" and self._config.profile == INCOGNITO_PROFILE:
                await self._empty_incognito()
            await self._clear_session_files()
            self._threads.clear()
            self._kept_out.clear()
            self._initial_pages = []

    async def _clear_session_files(self) -> None:
        """Once Dot's own Chrome has exited, delete the session files it left in the profile, if a login was filled
        into one of its pages (item 621): Chrome keeps the body of a form it sent by POST in them, which can be a
        password. A Chrome that has not exited by then leaves them for the next start."""
        if self._profile not in self._filled_profiles or self._config.mode != "own":
            return
        for _ in range(int(_CHROME_EXIT_WAIT_S / _CHROME_EXIT_POLL_S)):
            try:
                holder: int | None = await asyncio.to_thread(profile_holder_pid, self._profile)
            except BrowserError as error:
                _log.warning("browser_sessions_not_cleared", error=error.message)
                return
            if holder is None:
                await asyncio.to_thread(clear_session_files, self._profile)
                self._filled_profiles.discard(self._profile)
                return
            await asyncio.sleep(_CHROME_EXIT_POLL_S)
        _log.warning("browser_sessions_not_cleared", error="a Chrome still holds the profile")

    async def _empty_incognito(self) -> None:
        """Empty Incognito's folder, unless a Chrome holds it now: a window that Sign in… opened is not Dot's to take
        the folder from, and the next start of Dot's own Chrome on Incognito empties it (item 355)."""
        try:
            holder: int | None = await asyncio.to_thread(profile_holder_pid, self._profile)
        except BrowserError as error:
            _log.warning("browser_incognito_not_emptied", error=error.message)
            return
        if holder is not None:
            _log.info("browser_incognito_held", pid=holder)
            return
        await asyncio.to_thread(empty_incognito)

    async def _write_ledger(self) -> None:
        """Write the tabs this session has open in the Chrome of the user to the ledger (item 703); a ledger that
        cannot be written is logged, and only a crash would leave its tabs behind."""
        async with self._ledger_lock:
            targets: frozenset[str] = frozenset(self._dot_targets)
            try:
                await asyncio.to_thread(ledger.record, ledger.ledger_path(), targets)
            except OSError as error:
                _log.warning("browser_ledger_not_written", error=str(error))

    async def _close_left(self, browser: Browser) -> None:
        """Close the tabs a Dot that no longer runs left in the Chrome of the user (item 703): those the ledger names,
        and that this Chrome still has under the same id. A tab of the user's, and a tab of a Dot that still runs, is
        not touched. A close that fails is logged and the connection goes on."""
        try:
            left: frozenset[str] = await asyncio.to_thread(ledger.take_left, ledger.ledger_path())
        except OSError as error:
            _log.warning("browser_ledger_unreadable", error=str(error))
            return
        if not left:
            return
        closed: int = 0
        try:
            cdp: CDPSession = await browser.new_browser_cdp_session()
            try:
                answer: dict[str, object] = await cdp.send("Target.getTargets")
                infos: object = answer.get("targetInfos")
                for info in infos if isinstance(infos, list) else []:
                    target: object = info.get("targetId") if isinstance(info, Mapping) else None
                    if isinstance(target, str) and target in left and info.get("type") == "page":
                        await cdp.send("Target.closeTarget", {"targetId": target})
                        closed += 1
            finally:
                with contextlib.suppress(PlaywrightError):
                    await cdp.detach()
        except PlaywrightError as error:
            _log.warning("browser_left_tabs_not_closed", error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS))
            return
        _log.info("browser_left_tabs_closed", named=len(left), closed=closed)

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
                await self.close_idle_background_tabs()
            except Exception:  # noqa: BLE001 - logged with its traceback
                _log.exception("browser_idle_tabs_close_failed")
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

    async def close_idle_background_tabs(self) -> None:
        """Close the background tabs that no call of their chat has had current for ``background_tab_idle_s`` (item
        696), while its run works as well: a page the run went on from. A chat in the middle of a step is left for the
        next round, and so is the tab the owner is showing a task in. The current tab of the chat is told which closed.
        """
        idle_s: float = self._config.background_tab_idle_s
        if idle_s == 0:
            return
        cutoff: float = time.monotonic() - idle_s
        for thread_id, thread in list(self._threads.items()):
            if thread.lock.locked() or not idle_background_tabs(thread, cutoff):
                continue
            async with thread.lock:
                if self._threads.get(thread_id) is not thread:
                    continue
                closed: list[Tab] = await self._close_background(thread, idle_background_tabs(thread, cutoff))
            if closed:
                _log.info("browser_idle_background_tabs_closed", thread_id=thread_id, tabs=len(closed))

    async def end_of_run(self, thread_id: str, *, keep_current: bool) -> None:
        """Tidy the tabs of a chat whose run has ended (item 696): every one when ``keep_current`` is false (a helper's
        chat, which no message continues), else every one but the current tab, which stays for a message that goes on
        from it until ``tab_idle_s``. Nothing is closed while another run works in the chat. Only the chat's own tabs
        are closed: those of other chats, at work or not, and the owner's tabs are not touched."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or thread_id in self._kept():
            return
        if not keep_current:
            async with thread.lock:
                if self._threads.get(thread_id) is thread and thread_id not in self._kept():
                    await self.release_thread(thread_id)
            _log.info("browser_run_tabs_released", thread_id=thread_id)
            return
        async with thread.lock:
            if self._threads.get(thread_id) is not thread or thread_id in self._kept():
                return
            background: list[Tab] = [tab for index, tab in enumerate(thread.tabs) if index != thread.active]
            closed: list[Tab] = await self._close_background(thread, background)
        if closed:
            _log.info("browser_run_background_tabs_closed", thread_id=thread_id, tabs=len(closed))

    async def _close_background(self, thread: ThreadTabs, tabs: Sequence[Tab]) -> list[Tab]:
        """Close ``tabs`` of ``thread``, but not its current one nor the tab a task is being shown in, and note on the
        current tab what closed; return the tabs closed."""
        current: Tab | None = thread.tabs[thread.active] if thread.tabs else None
        teaching: object = None if self._teaching is None else self._teaching.page
        closing: list[Tab] = [tab for tab in tabs if tab is not current and tab.page is not teaching]
        for tab in closing:
            url: str = tab.page.url
            await self._close_tab_object(thread, tab)
            if current is not None:
                add_note(current, f"closed a tab no longer in use: {truncate_middle(url, _LAUNCH_ERROR_CHARS)}")
        return closing

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
        that closes itself while this runs (a sign-in callback) is a note on its opener, not an error. One taken in on
        a URL the rules forbid is reset at once (:meth:`_leave_refused_popup`)."""
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
            if not any(held is opener_tab for held in thread.tabs) or not any(
                held is thread for held in self._threads.values()
            ):
                # Its opener left the chat while it was registered (item 697): the chat's tabs were made new for a
                # login, given back clean to the owner or let go of. A page a script of the run may have reached is not
                # taken into a chat that is clean now; it closes, as its opener did.
                with contextlib.suppress(PlaywrightError):
                    await page.close()
                return
            thread.tabs.append(tab)
            add_note(opener_tab, f"a new tab opened as tab {len(thread.tabs)}")
            await self._enforce_cap(thread)
            await self._leave_refused_popup(opener_tab, tab)
        finally:
            taken_in(opener_tab, tab)

    async def _leave_refused_popup(self, opener: Tab, tab: Tab) -> None:
        """Reset ``tab``, a popup taken in on a URL the rules forbid, to a blank page, and say so on its opener (item
        554). Chrome tells of a popup only once its page answered (:func:`watch_births`), so in Dot's own Chrome the
        hops of its first redirect were sent before its guard was up (nothing of what they answered is read), and the
        Chrome of the user holds no request at all. Every read of the tab checks it again
        (:meth:`_enforce_landing`); a popup that closed meanwhile has nothing left to reset."""
        try:
            await self._enforce_landing(tab)
        except ForbiddenUrlError as refused:
            address: str = truncate_middle(self._shown_to(opener.page, refused.url), _SHOWN_URL_CHARS)
            add_note(
                opener,
                f"the new tab went to a URL that may not be opened ({address}: {refused.reason}); it was reset to a "
                "blank page",
            )
        except (BrowserError, PlaywrightError) as error:
            if tab.page.is_closed():
                return
            _log.warning(
                "browser_popup_unchecked",
                url=truncate_middle(self._shown(tab.page.url), _LAUNCH_ERROR_CHARS),
                error=truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
            )

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
            self._dot_targets.add(tab.target_id)
            await self._write_ledger()
            await keep_focused(page)
        else:
            await self._guard_hops(page)
        if self._config.decline_cookie_banners:
            await install_consent_handler(page)

    def _on_page_closed(self, tab: Tab, _page: Page) -> None:
        if tab.target_id in self._dot_targets:
            self._dot_targets.discard(tab.target_id)
            writing: asyncio.Task[None] = asyncio.create_task(self._write_ledger())
            self._ledger_writes.add(writing)
            writing.add_done_callback(self._ledger_writes.discard)
        self._refusals.pop(tab.page, None)
        # What was kept out of addresses for this tab goes with it: another tab at the same origin is held to nothing.
        self._kept_out.pop(tab.page, None)
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
            await self._ensure_context(self._unattended(thread_id))
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
                if thread.tabs:
                    thread.tabs[thread.active].last_used = thread.last_used

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
        if tab.viewport is not None:
            return tab.viewport
        if config.mode == "own" and not config.fit_window and config.zoom == _UNZOOMED:
            return Viewport(width=config.viewport_width, height=config.viewport_height)
        return await measure_viewport(tab.page)

    async def _read_held[T](self, tab: Tab, read: Callable[[], Awaitable[T]]) -> T:
        """What ``read`` gives of the page of ``tab``, with the URL rules held on either side of it (item 554): a page
        that went to a URL they forbid before the read is left first, and one that went there while it was read fails
        the call, so that nothing read of it is shown."""
        await self._enforce_landing(tab)
        result: T = await read()
        await self._enforce_landing(tab, after_read=True)
        return result

    async def _capture(self, tab: Tab, hide_secrets: bool) -> _Capture:
        """The page as the model reads it, under the URL rules (:meth:`_read_held`)."""
        config: BrowserConfig = self._config

        async def read() -> tuple[Viewport, PageLines]:
            # The window's size is read alongside the snapshot, not before it (item 342): in the Chrome of the user it
            # is one more round trip to the page on every capture, and the two reads do not depend on each other. When
            # one fails the other is cancelled (item 752): a snapshot left running after the call failed replaced the
            # tab's refs under the next call.
            sizing: asyncio.Task[Viewport] = asyncio.ensure_future(self._viewport(tab))
            snapping: asyncio.Task[PageLines] = asyncio.ensure_future(
                capture_page(tab.page, _ms(config.action_timeout_s), hide_secrets, tab.vault_held, tab.filled_values)
            )
            try:
                # The first to fail ends the wait, whichever it is; the other is cancelled below.
                await asyncio.wait((sizing, snapping), return_when=asyncio.FIRST_EXCEPTION)
                for task in (sizing, snapping):
                    failed: BaseException | None = task.exception() if task.done() else None
                    if failed is not None:
                        raise failed
                return sizing.result(), snapping.result()
            finally:
                for task in (sizing, snapping):
                    if not task.done():
                        task.cancel()
                    elif not task.cancelled():
                        task.exception()  # read, so that the one not raised here is not reported as never read

        viewport, (lines, part_note) = await self._read_held(tab, read)
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
            partial=part_note,
        )

    async def _capture_loaded(self, tab: Tab, hide_secrets: bool) -> _Capture:
        """Capture after a navigation; a nearly empty first view gets one more, longer, settle before it is shown."""
        capture: _Capture = await self._capture(tab, hide_secrets)
        if capture.refs >= _MIN_REFS_AFTER_LOAD or tab.page.url in _BLANK_URLS:
            return capture
        await wait_dom_quiet(tab.page, _LATE_QUIET_MS, _LATE_MAX_MS, _ms(self._config.navigation_timeout_s))
        return await self._capture(tab, hide_secrets)

    async def _tab_title(self, tab: Tab) -> str:
        """The title of ``tab``'s page; a page on a URL the rules forbid is not read (item 554), its title included: a
        tab in the background a popup's redirect or a page's own timer sent there is reset when it is switched to."""
        if not self._may_show(tab.page.url):
            return _UNREAD_TITLE
        try:
            return await page_title(tab.page)
        except BrowserError as error:
            _log.warning("browser_tab_title_unavailable", error=error.message)
            return "(title unavailable)"

    def _may_show(self, url: str) -> bool:
        """Whether a page at ``url`` may be read: one the URL rules allow, a blank one, or Chrome's own error page."""
        if url.startswith(_ERROR_PAGE) or url in _BLANK_URLS:
            return True
        config: BrowserConfig = self._config
        return check_browser_url(url, config.allowed_hosts, config.blocked_hosts, self._server).allowed

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
            notes=(*tab.notes, capture.partial) if capture.partial else tuple(tab.notes),
        )
        tab.notes.clear()
        tab.lines = capture.lines
        tab.rendered = capture.rendered
        tab.rendered_url = url
        tab.rendered_partial = bool(capture.partial)
        return view

    async def _full_view(self, thread: ThreadTabs, tab: Tab, hide_secrets: bool) -> PageView:
        await self._settle(tab)
        return await self._settled_view(thread, tab, hide_secrets)

    async def _settled_view(self, thread: ThreadTabs, tab: Tab, hide_secrets: bool) -> PageView:
        """The whole page, of a tab that has just settled."""
        capture: _Capture = await self._capture(tab, hide_secrets)
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    async def _changes_view(self, thread: ThreadTabs, tab: Tab, hide_secrets: bool) -> PageView:
        """The page as it is now, as a delta against the last view when that is clearly smaller."""
        capture: _Capture = await self._capture(tab, hide_secrets)
        if tab.rendered and tab.rendered_url == tab.page.url:
            diff = diff_views(tab.rendered, capture.rendered)
            # A part of the page is not compared with a whole one, either way: what it leaves out would read as gone,
            # or as new.
            if prefer_diff(diff, capture.rendered) and not capture.partial and not tab.rendered_partial:
                return await self._assemble(thread, tab, capture, render_diff(diff), "delta")
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    def _require_allowed(self, url: str) -> None:
        """The rules on an address before the browser is sent to it, as the model wrote it: a refusal this early costs
        no request. Chrome reads the address again on the way (:meth:`_on_request`), in its own spelling."""
        verdict = check_browser_url(url, self._config.allowed_hosts, self._config.blocked_hosts, self._server)
        if not verdict.allowed:
            raise ForbiddenUrlError(_FORBIDDEN_MESSAGE, url, verdict.reason, "document")

    # ------------------------------------------------------------------ the rules on what Chrome sends

    def _judge(
        self,
        url: str,
        navigation: bool,
        resource_type: str,
        page: Page | None,
        sender: str | None,
        tab: Page | None = None,
    ) -> UrlVerdict:
        """The rules on one request Chrome is about to send; ``page`` the page whose main frame it navigates, when it
        is one, so that the error page it then shows can be told from a page that failed to load, ``sender`` the
        address of the document that sent it, when it can be named (:func:`sending_document`), and ``tab`` the page
        that document is in (:func:`sending_tab`). A rule that fails on an address refuses it: the request must be
        answered either way, or Chrome holds it for good.

        What no request may carry in its address (:meth:`_kept_out_of`) is looked for in the requests a page sends, and
        only in those of the documents of the origin it was typed into, in the tab it was typed into: a rule that acts
        differently by what a secret is tells whoever can watch it act, and in every other tab the one who can is the
        run, whose own script (``browser_eval``) tries addresses and reads which were refused (a list of 1,640 words
        gave a weak password up in twelve requests). In the tab the value was typed into no script of a run runs while
        it is open. A navigation of a tab's main frame is not a request a page sends but the run's own, however it came
        about (the run opened the address, clicked the link, typed into the form the page then sends): it is judged by
        the rules on addresses and by no value filled, since a run cannot carry a value it never saw, and a refusal by
        a value would tell it whether a guess is the value."""
        kept: tuple[str, ...] = () if page is not None else self._kept_out_of(sender, tab)
        if kept and in_address(url, kept):
            verdict: UrlVerdict = UrlVerdict(allowed=False, reason=_CARRIES_SECRET)
        else:
            try:
                verdict = request_verdict(
                    url, navigation, self._config.allowed_hosts, self._config.blocked_hosts, self._server
                )
            except Exception:  # noqa: BLE001 - logged with its traceback, and the request is refused
                _log.exception("browser_request_unjudged", url=truncate_middle(url, _LAUNCH_ERROR_CHARS))
                verdict = UrlVerdict(allowed=False, reason="the URL rules could not read this address")
        if not verdict.allowed:
            if page is not None:
                # What the run reads of it: made from the values of its own chat (:meth:`_shown_to`).
                self._refusals[page] = _Refusal(url=self._shown_to(page, url), reason=verdict.reason)
            _log.info(
                "browser_request_refused",
                url=truncate_middle(self._shown(url), _LAUNCH_ERROR_CHARS),
                resource_type=resource_type,
                reason=verdict.reason,
            )
        return verdict

    def _kept_out_of(self, document: str | None, tab: Page | None) -> tuple[str, ...]:
        """The passwords and one-time codes that no request of the document at ``document``, in the page ``tab``, may
        carry in its address now (items 493 and 621): those typed into a document of its origin in that very tab. None
        for a document or a tab that cannot be named (:func:`sending_document`, :func:`sending_tab`), for a tab no value
        was typed into, and for a document of any other origin."""
        if document is None or tab is None:
            return ()
        now: float = time.monotonic()
        return tuple(
            held.value for held, until in self._kept_out.get(tab, {}).items() if until > now and held.covers(document)
        )

    def _shown(self, url: str) -> str:
        """``url`` as Dot writes it in its log (item 493): :meth:`_named` with every password and code kept out of
        addresses now, of whatever site and tab, and without the cookies any chat's scripts ran with (item 533). That
        is the words of a log line, which no page and no run reads; no request is judged by them."""
        now: float = time.monotonic()
        live: tuple[str, ...] = tuple(value for value, until in self._vault_secrets.items() if until > now)
        return self._words_of(url, live, tuple(value for values in self._cookie_values.values() for value in values))

    def _shown_to(self, page: Page, url: str) -> str:
        """``url`` as the chat that owns ``page`` reads it in a refusal or a note (item 621): :meth:`_named` with the
        passwords and codes that chat filled and the cookies its scripts ran with, and no other chat's. Whatever sent
        the page to the address chose it, a run among them, and the words change when it carries a value: made from
        every chat's values, they would tell a run in a chat that filled nothing whether a word of its own is the
        password another chat filled (a list of words in the address tells whether one is, and halving finds it)."""
        thread_id: str | None = next(
            (one for one, thread in self._threads.items() if any(tab.page is page for tab in thread.tabs)),
            None,
        )
        now: float = time.monotonic()
        mine: tuple[str, ...] = () if thread_id is None else tuple(self._vault_values.get(thread_id, ()))
        live: tuple[str, ...] = tuple(value for value in mine if self._vault_secrets.get(value, 0.0) > now)
        cookies: tuple[str, ...] = () if thread_id is None else tuple(self._cookie_values.get(thread_id, ()))
        return self._words_of(url, live, cookies)

    def _words_of(self, url: str, live: tuple[str, ...], cookies: tuple[str, ...]) -> str:
        """``url`` as :meth:`_named` names it when it carries one of ``live``, with ``cookies`` taken out: a script may
        have put one in the address it sends the tab to."""
        return masked(self._named(url, live, bool(live) and in_address(url, live)), cookies, HIDDEN_VALUE)

    @staticmethod
    def _named(url: str, live: tuple[str, ...], carries: bool) -> str:
        """``url`` named for a refusal or a log line. One that ``carries`` one of ``live``, however its escapes write it
        (:func:`in_address`), is named by its host alone, or by its scheme when the value is in the host: a form of the
        value masking does not know may be anywhere in it. Any other has the forms masking knows masked. Naming never
        fails: the request it is for is answered either way."""
        if not live:
            return url
        if not carries:
            return masked(url, live)
        try:
            parts = urlsplit(url)
        except ValueError as error:
            # An address of a host Python cannot split (unbalanced brackets) is named alone, and why is logged.
            _log.info("browser_address_unnamed", error=type(error).__name__)
            return "[an address that carries a filled value]"
        host: str = parts.hostname or ""
        if in_address(host, live):
            return f"{parts.scheme}://[a host that carries a filled value]"
        return f"{parts.scheme}://{host}/[an address that carries a filled value]"

    async def _on_request(self, route: Route, request: Request) -> None:
        """The route of Dot's own Chrome: a request the rules refuse fails in the page as blocked by the client; the
        rest go on. A page that closed under its request has nothing left to answer."""
        verdict: UrlVerdict = self._judge(
            request.url,
            request.is_navigation_request(),
            request.resource_type,
            main_page_of(request),
            sending_document(request),
            sending_tab(request),
        )
        try:
            if verdict.allowed:
                await route.continue_()
            else:
                await route.abort(_ABORT_REASON)
        except PlaywrightError as error:
            _log.info(
                "browser_request_unanswered",
                url=truncate_middle(self._shown(request.url), _LAUNCH_ERROR_CHARS),
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
        # The main frame's navigation is the page's; when its id is not known, any navigation is taken for it. A
        # request of the main frame is sent by the document the frame shows; for a request of another frame no document
        # is named here (the route names it, for the first request of each).
        in_main: bool = (main is None and paused.navigation) or main == paused.frame_id
        navigated: Page | None = page if paused.navigation and in_main else None
        sender: str | None = document_of(page.main_frame) if in_main else None
        verdict: UrlVerdict = self._judge(paused.url, paused.navigation, paused.resource_type, navigated, sender, page)
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
                _log.debug(
                    "browser_hop_gone", url=truncate_middle(self._shown(url), _LAUNCH_ERROR_CHARS), allowed=allowed
                )
                return
            _log.warning(
                "browser_hop_unanswered",
                url=truncate_middle(self._shown(url), _LAUNCH_ERROR_CHARS),
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

    async def _enforce_landing(self, tab: Tab, *, after_read: bool = False) -> None:
        """A redirect, a click, a script or a page's own timer may have sent the tab to a URL the rules forbid; leave it
        before anything is read from it or done in it. Every operation that reads a run's tab or acts in it asks this
        first (item 554): a popup's first redirect is not held in Dot's own Chrome, and the Chrome of the user holds
        none of its requests. One the rules refused on the way shows Chrome's error page, which is left the same way. A
        frame of the page on such a URL is left for a blank page (:meth:`_leave_refused_frames`); with ``after_read``,
        the check that follows a read, a frame left fails the call, since the read may hold its page."""
        landed: str = tab.page.url
        config: BrowserConfig = self._config
        if landed.startswith(_ERROR_PAGE):
            refused: _Refusal | None = self._refusals.pop(tab.page, None)
            if refused is None:
                if _LOAD_FAILED_NOTE not in tab.notes:
                    add_note(tab, _LOAD_FAILED_NOTE)
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
            await self._leave_refused_frames(tab, after_read)
            return
        await self._blank(tab.page)
        raise ForbiddenUrlError(
            "the page ended up on a URL that may not be opened; the tab was reset to a blank page",
            landed,
            verdict.reason,
            "document",
        )

    async def _leave_refused_frames(self, tab: Tab, after_read: bool) -> None:
        """Send each frame of the page that is on a URL the rules forbid to a blank page (item 554), so that nothing of
        it is read or done: a frame of another site, which Chrome runs as a target of its own, is not reached by the
        tab's guard (:meth:`_guard_hops`), so the hops of its redirects are not held, and the Chrome of the user holds
        no request of a frame at all. A frame that cannot be left takes the tab with it. After a read
        (``after_read``) a frame left fails the call: the read may hold what the frame showed."""
        config: BrowserConfig = self._config
        left: list[PlaywrightFrame] = []
        refusal: ForbiddenUrlError | None = None
        for frame in tab.page.frames:
            if frame is tab.page.main_frame or frame.is_detached() or _within(frame, left):
                continue
            url: str = frame.url
            if not _judged_in_frame(url):
                continue
            verdict: UrlVerdict = check_browser_url(url, config.allowed_hosts, config.blocked_hosts, self._server)
            if verdict.allowed:
                continue
            try:
                await frame.goto("about:blank", wait_until="commit", timeout=_ms(config.navigation_timeout_s))
            except PlaywrightError as error:
                if frame.is_detached():
                    continue
                await self._blank(tab.page)
                raise ForbiddenUrlError(
                    "a frame of the page is on a URL that may not be opened and could not be left; the tab was reset "
                    "to a blank page",
                    url,
                    verdict.reason,
                    "document",
                ) from error
            left.append(frame)
            _log.info("browser_frame_left", url=truncate_middle(self._shown(url), _SHOWN_URL_CHARS))
            add_note(
                tab,
                "a frame of the page was on a URL that may not be opened "
                f"({truncate_middle(self._shown_to(tab.page, url), _SHOWN_URL_CHARS)}: {verdict.reason}); it was left "
                "for a blank page",
            )
            if refusal is None:
                refusal = ForbiddenUrlError(
                    "a frame of the page went to a URL that may not be opened while the page was read; it was left "
                    "for a blank page and nothing of this read is shown: read the page again",
                    url,
                    verdict.reason,
                    "document",
                )
        if after_read and refusal is not None:
            raise refusal

    async def _load(self, thread: ThreadTabs, tab: Tab, url: str, hide_secrets: bool) -> PageView:
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
        capture: _Capture = await self._capture_loaded(tab, hide_secrets)
        return await self._assemble(thread, tab, capture, capture.rendered, "full")

    # ------------------------------------------------------------------ public operations
    # A view the model reads takes ``hide_secrets``: the value of a field the page marks secret then shows as
    # ``[hidden]``, in the page's lines and in a screenshot (item 467); the tools give it in every mode but yolo.

    async def open_url(self, thread_id: str, url: str, hide_secrets: bool) -> PageView:
        """Navigate the current tab of the thread."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            return await self._load(thread, tab, url, hide_secrets)

    async def new_tab(self, thread_id: str, url: str, hide_secrets: bool) -> PageView:
        """Open ``url`` in a new tab that becomes the current one."""
        self._require_allowed(url)
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._create_tab(thread)
            return await self._load(thread, tab, url, hide_secrets)

    async def snapshot(self, thread_id: str, hide_secrets: bool) -> PageView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            return await self._full_view(thread, tab, hide_secrets)

    async def find(self, thread_id: str, needle: str, limit: int, hide_secrets: bool) -> FindResult:
        """Search the whole page, also outside the viewport, for elements whose role or name contains ``needle``; a
        hidden value is not searched."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            capture: _Capture = await self._capture(tab, hide_secrets)
            tab.lines = capture.lines
            return FindResult(url=tab.page.url, hits=find_lines(capture.lines, needle, limit))

    async def links(self, thread_id: str, needle: str, limit: int, hide_secrets: bool) -> FindResult:
        """The links of the whole page with absolute targets; ``needle`` filters on link text and target."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            capture: _Capture = await self._capture(tab, hide_secrets)
            tab.lines = capture.lines
            return FindResult(url=tab.page.url, hits=link_lines(capture.lines, tab.page.url, needle, limit))

    async def background_calls(self, thread_id: str, needle: str, limit: int, hide_secrets: bool) -> FindResult:
        """The calls the page of the thread's current tab made in the background (XHR and fetch, item 714) whose address
        contains ``needle``, newest first, with what :meth:`held_back` holds back taken out."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)

            def shown_as(url: str) -> str:
                return self.held_back(thread_id, hidden_address(url) if hide_secrets else url)

            async def read() -> list[str]:
                return call_lines(list(tab.calls), needle, limit, shown_as)

            # Under the URL rules, as every read of a tab is (item 554).
            lines: list[str] = await self._read_held(tab, read)
            return FindResult(url=self.held_back(thread_id, tab.page.url), hits=tuple(lines))

    async def console_messages(self, thread_id: str, level: str | None, limit: int, hide_secrets: bool) -> FindResult:
        """What the page of the thread's current tab wrote to its console since it was loaded, and the errors its
        scripts did not catch (item 746), newest first, ``level`` only when given, with what :meth:`held_back` holds
        back taken out."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)

            def shown_as(url: str) -> str:
                return self.held_back(thread_id, url)

            async def read() -> list[str]:
                return console_lines(list(tab.console), level, limit, shown_as, hide_secrets)

            # Under the URL rules, as every read of a tab is (item 554).
            lines: list[str] = await self._read_held(tab, read)
            return FindResult(
                url=self.held_back(thread_id, tab.page.url),
                hits=tuple(self.held_back(thread_id, line) for line in lines),
            )

    async def set_viewport(self, thread_id: str, width: int | None, height: int | None) -> Viewport:
        """Give the thread's current tab the page area ``width`` by ``height`` CSS pixels (item 746), or, with no
        ``width``, the size every tab of Dot's Chrome has (the one of Settings › Browser, or its window's); it returns
        the size the page then measures. Dot's own Chrome only: the open Chrome of the user is never given an emulation
        of Playwright's, and its window is theirs."""
        if self._config.mode == "attach":
            raise BrowserError(
                "the browser is the open Chrome of the owner, whose window is theirs: Dot does not resize it or put an "
                "emulation on it. Look at the page at a phone's size with Dot's own Chrome (Settings › Browser)",
                {"mode": self._config.mode},
            )
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            if width is None and tab.viewport is None:
                return await self._viewport(tab)
            size: Viewport = Viewport(width=width or 0, height=height or _PHONE_HEIGHT)
            try:
                # The size is laid over the tab's own with a DevTools session of the tab's. Where every tab follows its
                # window (item 354), ending the session's size gives the tab that back rather than a fixed copy of the
                # size it had. Where every tab has the size of Settings › Browser, the session gives it that size:
                # ending its override ends Playwright's too, which Playwright, seeing no change of its own, does not
                # give again.
                if width is None and tab.sizing is not None and self._config.fit_window:
                    await tab.sizing.send("Emulation.clearDeviceMetricsOverride")
                    await tab.sizing.detach()
                    tab.sizing = None
                elif width is not None or tab.sizing is not None:
                    if tab.sizing is None:
                        tab.sizing = await tab.page.context.new_cdp_session(tab.page)
                    given: Viewport = (
                        size
                        if width is not None
                        else Viewport(width=self._config.viewport_width, height=self._config.viewport_height)
                    )
                    await tab.sizing.send(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": given.width, "height": given.height, "deviceScaleFactor": 0, "mobile": False},
                    )
            except PlaywrightError as error:
                raise BrowserError(
                    "setting the size of the tab failed",
                    {
                        "width": width,
                        "height": size.height,
                        "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS),
                    },
                ) from error
            tab.viewport = None if width is None else size
            await self._settle(tab)
            return await self._read_held(tab, lambda: measure_viewport(tab.page))

    async def call_answer(self, thread_id: str, number: int, hide_secrets: bool) -> TextView:
        """What the server answered to the call ``number`` of :meth:`background_calls` (item 714), JSON written
        compactly with its secret keys hidden when ``hide_secrets``, and what :meth:`held_back` holds back taken out."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            call: Fetched | None = next((kept for kept in tab.calls if kept.number == number), None)
            if call is None:
                raise BrowserError(
                    "no call kept in this tab has that number (the tab keeps its newest 50): list them again with "
                    "browser_requests",
                    {"number": number, "calls": len(tab.calls)},
                )
            answer: str = await self._read_held(tab, lambda: answer_text(call, hide_secrets))
            text: str = self.held_back(thread_id, answer)
            address: str = hidden_address(call.url) if hide_secrets else call.url
            return TextView(url=self.held_back(thread_id, address), text=text, offset=0, total_chars=len(text))

    async def extract(
        self, thread_id: str, records: str, fields: dict[str, str], limit: int, hide_secrets: bool
    ) -> TextView:
        """The records the page of the thread's current tab holds by ``fields`` (item 714,
        :func:`~dotagent.browser.extract.extract_records`), written one JSON line each, with what :meth:`held_back`
        holds back taken out."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            found: list[JsonValue] = await self._read_held(
                tab, lambda: extract_records(tab.page, records, fields, limit, hide_secrets)
            )
            url: str = self.held_back(thread_id, tab.page.url)
            text: str = self.held_back(thread_id, render_records(url, found))
            return TextView(url=url, text=text, offset=0, total_chars=len(text))

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

    async def read_page(self, url: str, *, public_only: bool) -> RenderedDocument:
        """Read ``url`` (:meth:`_read_page`) as a use of Chrome that the idle close waits for. ``public_only`` is the
        rule of the web tools (``[web] allow_private_hosts`` off): the page is given back only when it was served from
        public addresses (:meth:`_require_public`)."""
        with self._in_use():
            return await self._read_page(url, public_only)

    async def _require_public(self, landed: str, documents: Sequence[Response]) -> None:
        """The rule of the web tools for a page the browser read for them, which the plain client holds by connecting
        to checked addresses only (item 554): each document of the way, redirects included, and the page it ended on
        were served from public addresses. What Chrome connected to is asked of each response, so a name that was
        pointed at the local network after it was looked at shows too; the host of the page that landed is looked at as
        the plain client looks at one. What was served from a private or local address is not given back: the request
        for it, which a redirect of the page may have sent, a plain request would not have."""
        private: str | None = None
        for response in documents:
            try:
                served = await response.server_addr()
            except PlaywrightError:
                continue
            if served is not None and is_private_text(served["ipAddress"]):
                private = served["ipAddress"]
                break
        if private is None:
            host: str = urlsplit(landed).hostname or ""
            private = await non_public_address(host) if host else None
        if private is not None:
            raise ForbiddenUrlError(
                "the page is on a private or local network address; the web tools only reach the public internet "
                "(web.allow_private_hosts)",
                landed,
                f"it was served from {private}",
                "document",
            )

    async def _read_page(self, url: str, public_only: bool) -> RenderedDocument:
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
        documents: list[Response] = []
        page.on("response", lambda response: _note_main_status(page, response, statuses, documents))
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
            if public_only:
                await self._require_public(landed, documents)
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
            # Masked before it is cut into slices, so that no slice holds a part of a value (items 493 and 533).
            page_text: str = await self._read_held(
                tab, lambda: visible_text(tab.page, ref, _ms(self._config.action_timeout_s))
            )
            text: str = squeeze_text(self.held_back(thread_id, page_text))
            return TextView(
                url=tab.page.url, text=text[offset : offset + max_chars], offset=offset, total_chars=len(text)
            )

    async def screenshot(self, thread_id: str, hide_secrets: bool) -> Screenshot:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            config: BrowserConfig = self._config

            async def take() -> tuple[Viewport, bytes]:
                viewport: Viewport = await self._viewport(tab)
                return viewport, await screenshot_jpeg(
                    tab.page, config.screenshot_quality, _ms(config.action_timeout_s), hide_secrets, tab.filled_values
                )

            viewport, shot = await self._read_held(tab, take)
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

    async def send_input(self, thread_id: str, events: Sequence[InputEvent], hold: ScreenHold) -> InputResult:
        """Do the owner's ``events`` on the thread's current tab (item 470), which starts no Chrome. A step of the run
        that was under way when the owner took it over is waited for, up to a moment, under the thread's lock; then the
        ``hold`` must still go on (:meth:`ScreenHold.begin`), so that input sent before the owner handed the run back
        never lands after. The page is given a moment to take the batch. The last batch of a hold that ended while it
        was done lets go of what the input left pressed before the page is anyone else's (item 479). A failure of the
        page is said with its address and the kinds of the events only: what the owner types is in no error and no
        log."""
        if not self._config.remote_input:
            raise RunConflict(
                "using the page from the screen is switched off: [browser] remote_input, in Settings › Browser",
                {"thread_id": thread_id},
            )
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or self._tab_of(thread_id) is None:
            raise RunConflict("the run has no page open", {"thread_id": thread_id})
        if hold.over:
            raise RunConflict("the run is not taken over", {"thread_id": thread_id})
        hold.under_way += 1
        locked: bool = False
        try:
            try:
                async with asyncio.timeout(_INPUT_WAIT_S):
                    await thread.lock.acquire()
            except TimeoutError:
                raise RunConflict(
                    "a step of the run is still going; try again in a moment", {"thread_id": thread_id}
                ) from None
            locked = True
            return await self._do_input(thread, thread_id, events, hold)
        finally:
            hold.under_way -= 1
            left: frozenset[MouseButton] = hold.take_release()
            if locked:
                try:
                    await self._let_go_now(thread_id, left)
                finally:
                    thread.lock.release()
            elif left:
                # This batch never had the page, and the hold ended meanwhile: the release waits its turn for it.
                await self._let_go_logged(thread_id, left)

    async def _do_input(
        self, thread: ThreadTabs, thread_id: str, events: Sequence[InputEvent], hold: ScreenHold
    ) -> InputResult:
        """Do ``events`` on the thread's tab, under its lock, while ``hold`` goes on; the page is given
        :data:`_INPUT_DO_S` to measure itself and take them."""
        tab: Tab | None = self._tab_of(thread_id)
        if tab is None:
            raise RunConflict("the run has no page open", {"thread_id": thread_id})
        if not hold.begin(events):
            raise RunConflict("the run is not taken over", {"thread_id": thread_id})
        try:
            async with asyncio.timeout(_INPUT_DO_S):
                viewport: Viewport = await self._viewport(tab)
                await dispatch(tab.page, viewport, events)
        except Exception as error:  # noqa: BLE001 - raised again below without its words
            # Whatever failed, its words may hold what the owner typed: only its kind is kept, and it is not chained,
            # so that no traceback carries it either. A page that did not answer in time is a TimeoutError.
            raise BrowserError(
                "the page did not take the input",
                {"url": tab.page.url, "events": [event.kind for event in events], "error_type": type(error).__name__},
            ) from None
        thread.last_used = time.monotonic()
        return InputResult(accepted=len(events), url=tab.page.url, width=viewport.width, height=viewport.height)

    async def _let_go_now(self, thread_id: str, buttons: frozenset[MouseButton]) -> None:
        """Let go of ``buttons`` on the thread's tab, its lock held by the caller, giving the page :data:`_LET_GO_S`; a
        page that does not take it is logged, since the hold it belonged to is over and nobody waits on it."""
        tab: Tab | None = self._tab_of(thread_id)
        if tab is None or not buttons:
            return
        try:
            async with asyncio.timeout(_LET_GO_S):
                await let_go(tab.page, buttons)
        except (PlaywrightError, TimeoutError) as error:
            _log.warning(
                "screen_input_release_failed",
                thread_id=thread_id,
                url=tab.page.url,
                buttons=", ".join(sorted(buttons)),
                error_type=type(error).__name__,
            )

    async def _let_go_logged(self, thread_id: str, buttons: frozenset[MouseButton]) -> None:
        try:
            await self.let_go_of_buttons(thread_id, buttons)
        except BrowserError as error:
            _log.warning("screen_input_release_failed", thread_id=thread_id, error=error.message)

    async def let_go_of_buttons(self, thread_id: str, buttons: frozenset[MouseButton]) -> None:
        """Let go of the buttons the owner's input left down (items 470 and 479), once its hold is over and none of
        its batches is under way: the thread's lock is waited for a moment, then the page is given
        :data:`_LET_GO_S`. A thread with no page has nothing to let go of; a page that is busy past the moment, or does
        not take the release, is a :class:`BrowserError`."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or self._tab_of(thread_id) is None or not buttons:
            return
        try:
            async with asyncio.timeout(_INPUT_WAIT_S):
                await thread.lock.acquire()
        except TimeoutError:
            raise BrowserError(
                "the page was busy, so a button left down was not let go of",
                {"thread_id": thread_id, "buttons": ", ".join(sorted(buttons))},
            ) from None
        try:
            tab: Tab | None = self._tab_of(thread_id)
            if tab is None:
                return
            try:
                async with asyncio.timeout(_LET_GO_S):
                    await let_go(tab.page, buttons)
            except (PlaywrightError, TimeoutError) as error:
                raise BrowserError(
                    "the page did not take the release of a button",
                    {
                        "url": tab.page.url,
                        "buttons": ", ".join(sorted(buttons)),
                        "error": truncate_middle(str(error), 300) or type(error).__name__,
                    },
                ) from error
        finally:
            thread.lock.release()

    async def focus(self, thread_id: str) -> str | None:
        """Bring the thread's current tab to the front of its window, for the user to take over; returns its address,
        ``None`` when the thread has no tab."""
        tab: Tab | None = self._tab_of(thread_id)
        if tab is None:
            return None
        await bring_to_front(tab.page)
        return tab.page.url

    async def clean_for_owner(self, thread_id: str) -> bool:
        """Give the owner who takes the run over a page that no script of the run runs in (item 554). A script of the
        run (``browser_eval``) may have left a listener in the page, or in a tab it opened, that copies what is typed,
        and the owner is sent to the browser for exactly what a run may not type itself: a password, a code, a card
        number. So when a script ran in a tab of the chat (:attr:`ThreadTabs.scripted`), the chat's other tabs are
        closed and the current one is opened again from its address, as a new document: a listener lives in its
        document. A page that was the answer of a form is asked for again by its address, and the form is not sent
        again. Returns whether it did so; a chat no script ran in is left as it is."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.scripted or not thread.tabs:
            return False
        async with self._locked(thread_id) as thread:
            tab: Tab | None = self._tab_of(thread_id)
            for other in [one for one in thread.tabs if one is not tab]:
                await self._close_tab_object(thread, other)
            if tab is None or not thread.scripted:
                return False
            address: str = tab.page.url
            tab.lines, tab.rendered = (), ""
            await self._blank(tab.page)
            thread.scripted = False
            add_note(tab, _SCRIPTED_NOTE)
            if address not in _BLANK_URLS and not address.startswith("about:blank"):
                try:
                    await self._navigate_checked(tab.page, address, self._config.navigation_attempts)
                except BrowserError as error:
                    add_note(tab, f"the page could not be opened again: {error.message}")
            return True

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
                if tab.page.url in _BLANK_URLS or tab.page.url.startswith("about:blank"):
                    try:
                        await tab.page.goto(_TEACH_START_PAGE, wait_until="commit")
                    except PlaywrightError as error:
                        raise BrowserError(
                            "the blank tab could not open the page a task starts from: open the page yourself, then "
                            "start again",
                            {"url": _TEACH_START_PAGE, "error": truncate_middle(str(error), _LAUNCH_ERROR_CHARS)},
                        ) from error
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
            pulse: Pulse | None = await page_pulse(tab.page) if _moves_the_page(step) else None
            requests: int = tab.requests
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
            except ApprovalParked as error:
                # Kept for the owner (item 482): its words say that it did not happen and waits for them.
                reports.append(StepReport(index=index, description=description, outcome="failed", detail=error.message))
                stopped = "a step waits for the user"
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
            effect: str = ""
            if pulse is not None and not tab.page.is_closed():
                # Only a click is meant to focus a field; Enter pressed in one leaves it there whatever it did.
                clicked: str | None = step.ref if step.action in _MOVING_ACTIONS else None
                effect = await self._effect(thread, tab, before, pulse, requests, clicked)
            reports.append(StepReport(index=index, description=description, outcome="done", detail=effect))
            stopped = describe_surprise(before, self._watch(thread, tab))
            if stopped is None and opening:
                stopped = _TAB_OPENING
        return reports, stopped

    async def _effect(
        self, thread: ThreadTabs, tab: Tab, before: _Watch, pulse: Pulse, requests: int, ref: str | None
    ) -> str:
        """What a step that should change the page did (item 739), as its report says it: nothing when the page
        changed and shows nothing new to read; what it shows new (an alert, a field it marks invalid) when it did; and,
        when nothing changed within :data:`_EFFECT_WAIT_S` (no change to its documents, no box ticked or field typed
        in, no request, no new address, tab or dialog, and the element clicked took no focus as a field), that it did
        not, with what the page marks invalid, so that the run does not take a click that
        did nothing for one that worked. A page that could not be read counts as changed."""
        after: Pulse = pulse
        moved: bool = False
        waited: float = 0.0
        while True:
            after = await page_pulse(tab.page)
            moved = (
                after.changes is None
                or pulse.changes is None
                or after.changes != pulse.changes
                or tab.requests != requests
                or describe_surprise(before, self._watch(thread, tab)) is not None
            )
            if moved or waited >= _EFFECT_WAIT_S or tab.page.is_closed():
                break
            await asyncio.sleep(_EFFECT_POLL_S)
            waited += _EFFECT_POLL_S
        if not moved and ref is not None and await holds_field_focus(tab.page, ref):
            return ""
        if not moved:
            invalid: str = f"; fields it marks invalid: {'; '.join(after.invalid)}" if after.invalid else ""
            return (
                "but the page did not change (no new content, request or address)"
                f"{invalid}. Something in front may have taken the click, or the control may wait for another event: "
                "look at the page, close what is in front, or reach the element with browser_eval and its ref"
            )
        said: list[str] = [one for one in after.alerts if one not in pulse.alerts]
        marked: list[str] = [one for one in after.invalid if one not in pulse.invalid]
        parts: list[str] = []
        if said:
            parts.append(f"the page says: {' | '.join(said)}")
        if marked:
            parts.append(f"it marks invalid: {'; '.join(marked)}")
        return "; ".join(parts)

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
        self,
        thread_id: str,
        steps: Sequence[Step],
        accept_confirms: bool,
        secret_field: SecretFieldCheck,
        hide_secrets: bool,
    ) -> ActOutcome:
        """Run the steps in order against the latest snapshot; stop at the first failure or surprise. ``secret_field``
        decides what a step does at a field that takes a secret: a step it refuses fails and stops the batch, and one
        the user takes over stops it too."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            # Before a step as well as after (item 554): a page that went to a URL the rules forbid since it was last
            # read takes no click.
            await self._enforce_landing(tab)
            tab.accept_confirms = accept_confirms
            try:
                reports, stopped = await self._run_steps(thread, tab, steps, secret_field)
            finally:
                tab.accept_confirms = False
            current: Tab = await self._current_tab(thread)
            await self._settle(current)
            await self._enforce_landing(current)
            view: PageView = (
                await self._changes_view(thread, current, hide_secrets)
                if current is tab
                else await self._settled_view(thread, current, hide_secrets)
            )
            return ActOutcome(steps=tuple(reports), stopped=stopped, view=view)

    async def scroll(
        self,
        thread_id: str,
        direction: Literal["down", "up", "top", "bottom"],
        pixels: int | None,
        ref: str | None,
        hide_secrets: bool,
    ) -> PageView:
        """Scroll the current tab by ``pixels``, or by most of the page area the model sees when ``pixels`` is None."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            await self._enforce_landing(tab)
            viewport: Viewport = await self._viewport(tab)
            distance: int = pixels if pixels is not None else round(viewport.height * _SCROLL_SHARE)
            await scroll_page(
                tab.page, direction, distance, ref, viewport.width, viewport.height, _ms(self._config.action_timeout_s)
            )
            # The wheel lands a few tens of milliseconds after the call returns, inside the DOM quiet window.
            await self._settle(tab)
            return await self._changes_view(thread, tab, hide_secrets)

    async def wait_text(self, thread_id: str, text: str, timeout_s: float, hide_secrets: bool) -> TextWait:
        """Wait until ``text`` is visible on the current tab, at most ``timeout_s``; the page is shown either way.
        After a wait that timed out the page is shown as it is, without a settle that could outlast the wait. A
        navigation still pending then holds the reading until it commits, so the URL rules are checked again once the
        page is read."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            # Not waited on a page that went to a URL the rules forbid (item 554): the answer would tell what it shows.
            await self._enforce_landing(tab)
            started: float = time.monotonic()
            end: TextWaitEnd | Literal["still"] = await self._text_or_still(tab, text, timeout_s)
            appeared: bool = end == "text"
            waited_s: float = time.monotonic() - started
            if appeared:
                await self._settle(tab)
            elif not tab.nav_idle.is_set():
                add_note(tab, _STILL_LOADING)
            await self._enforce_landing(tab)
            view: PageView = await self._changes_view(thread, tab, hide_secrets)
            await self._enforce_landing(tab)
            ended_by: str | None = _DIALOG_OPENED if end == "dialog" else None
            still_s: float | None = _TEXT_STILL_S if end == "still" else None
            return TextWait(appeared=appeared, waited_s=waited_s, view=view, ended_by=ended_by, still_s=still_s)

    async def _text_or_still(self, tab: Tab, text: str, timeout_s: float) -> TextWaitEnd | Literal["still"]:
        """What ended a wait of at most ``timeout_s`` for ``text`` (:func:`wait_for_text_or_dialog`), or ``"still"``:
        the page stood still once the wait had gone on for a while (item 774, :data:`_TEXT_STILL_S`). The page is
        looked at between short waits, each ending by its own time, so that none is left waiting in the page; a
        page whose changes cannot be read is never taken for still."""
        started: float = time.monotonic()
        ends: float = started + timeout_s
        watch_dialogs: bool = await no_dialog_open(tab.page)
        changes: int | None = (await page_pulse(tab.page)).changes
        requests: int = tab.requests
        moved: float = started
        while True:
            left: float = ends - time.monotonic()
            if left <= 0:
                return "timeout"
            # Playwright waits without end for a time of 0 ms: a slice is at least one.
            end: TextWaitEnd = await wait_for_text_or_dialog(
                tab.page, text, max(1, _ms(min(_TEXT_SLICE_S, left))), watch_dialogs
            )
            if end != "timeout":
                return end
            now: float = time.monotonic()
            # The page is not read once the time is spent: its frames could take a second more than was asked.
            if now >= ends:
                return "timeout"
            seen: int | None = (await page_pulse(tab.page)).changes
            # A request begun since the last look is the page at work too: one that polls its server every few seconds
            # with short requests is rarely seen with one under way.
            busy: bool = tab.requests != requests or tab.fetches > 0 or tab.sockets > 0 or not tab.nav_idle.is_set()
            if seen is None or seen != changes or busy:
                changes, requests, moved = seen, tab.requests, now
            elif now - moved >= _TEXT_STILL_S and now - started >= _TEXT_STILL_AFTER_S:
                return "still"

    def _refuse_fill(self, url: str, fills_on: Callable[[str], bool], kinds: Sequence[VaultFieldKind]) -> None:
        """Raise when a fill of ``kinds`` into the page at ``url`` is refused whatever the tabs: the page is not one of
        the login's sites, or a password or a code would go into the owner's Chrome."""
        if not fills_on(url):
            raise BrowserError(
                "this page is not one of the sites the fill was asked for: Dot fills a login only on its own site",
                {"url": url},
            )
        if self._config.mode == "attach" and any(kind != "username" for kind in kinds):
            # The owner's Chrome sends its requests unguarded: nothing would keep a value out of an address.
            raise BrowserError(
                "in your own Chrome Dot fills no password or code, since it does not guard what that Chrome sends: "
                "your password manager fills them there, or switch to Dot's own Chrome in Settings › Browser",
                {"url": url},
            )

    async def fresh_for_login(
        self, thread_id: str, fills_on: Callable[[str], bool], kinds: Sequence[VaultFieldKind]
    ) -> PageView | None:
        """Make the chat's tabs ones a login may be filled into (item 697), or say they are already: ``None`` when no
        script of the run ran in a tab of the chat. Else every tab of the chat closes and the current one's address is
        opened in a new tab, as a new document with no history, where no script of the run ran and none can come back
        from Chrome's cache of pages; the page is returned, its refs the ones a fill goes on with. A page that was the
        answer of a form is asked for again by its address, and the form is not sent again. A fill that would be refused
        on that page anyway (another site, a password in the owner's Chrome) is refused first, with no tab closed."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.scripted:
            return None
        async with self._locked(thread_id) as thread:
            if not thread.scripted or not thread.tabs:
                return None
            address: str = thread.tabs[thread.active].page.url
            self._refuse_fill(address, fills_on, kinds)
            old: list[Tab] = list(thread.tabs)
            fresh: Tab = await self._create_tab(thread)
            for tab in old:
                await self._close_tab_object(thread, tab)
            thread.scripted = False
            add_note(fresh, _FRESH_FOR_LOGIN_NOTE)
            if address not in _BLANK_URLS and not address.startswith("about:blank"):
                try:
                    fresh.status = await self._navigate_checked(fresh.page, address, self._config.navigation_attempts)
                except BrowserError as error:
                    add_note(fresh, f"the page could not be opened again: {error.message}")
            await self._settle(fresh)
            await self._enforce_landing(fresh)
            capture: _Capture = await self._capture_loaded(fresh, True)
            return await self._assemble(thread, fresh, capture, capture.rendered, "full")

    async def fill_from_vault(
        self, thread_id: str, fills: Sequence[VaultFill], fills_on: Callable[[str], bool]
    ) -> PageView:
        """Fill a login of the owner's password manager or of Dot's vault into the thread's current tab (items 493 and
        621), each part into its field, once the page is one of the login's sites (``fills_on`` of its address) and
        each field one its part may go into, in a document of those sites (:func:`fill_login_field`). A thread a
        script of the run ran in is refused while it has a tab open: the script may have left a listener that copies
        what is typed. The values are the thread's to mask from the moment one may be in the page, so that a fill that
        fails half way leaves masked what went in, and no script runs in the thread's tabs while the tab stays open; a
        field refused before anything went in leaves neither. What changed on the page is shown with its secret fields
        hidden, in every mode, and with the value of every field Dot filled. From the first value on, what Dot does by
        what a value is (keeping it out of the addresses a page sends, marking a field again) is done to the
        documents of the origin of the field it was typed into, in this tab, alone (:class:`HeldValue`): no script of
        a run runs in the tab while it is open, and no other tab is held to the value."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            await self._enforce_landing(tab)
            self._refuse_fill(tab.page.url, fills_on, [fill.kind for fill in fills])
            if thread.scripted:
                # A script may have left a listener that copies what is typed: in its page, in a tab it opened, in a
                # page Chrome brings back from its cache.
                raise BrowserError(
                    "a script of this run ran in a tab of this chat: Dot fills a login only where none ran. Close the "
                    "chat's tabs (browser_close_tab), then open the page again",
                    {"url": tab.page.url, "tabs": len(thread.tabs)},
                )
            for fill in fills:
                value: str = fill.value.get_secret_value()

                def typing(document: str, typed: str = value, kind: VaultFieldKind = fill.kind) -> None:
                    # From here the value may be in the page: masked, no script runs while the tab is open, and no
                    # request a page sends carries a password or a code in its address. It is scoped to the origin of
                    # the document that holds the field it goes into, not to every site the login names, and to this
                    # tab: what Dot does by what it is, it does to the documents of that origin in the tab where no
                    # script of the run runs, and to no other (any other tab, of this chat or another, may run one).
                    scope: SameOrigin | None = SameOrigin.of(document)
                    if scope is None:
                        raise BrowserError("the field is in a document that is no site's", {"url": tab.page.url})
                    held: HeldValue = HeldValue(typed, scope)
                    if kind in _MASKED_PARTS:
                        self._vault_values.setdefault(thread_id, set()).add(typed)
                    if kind in _KEPT_OUT_OF_ADDRESSES:
                        now: float = time.monotonic()
                        until: float = now + _CODE_HELD_S if kind in _KEPT_OUT_FOR_A_WHILE else math.inf
                        # Named in a refusal or a log line as a mask, whatever tab: nothing is decided by this list.
                        self._vault_secrets = {one: ends for one, ends in self._vault_secrets.items() if ends > now}
                        self._vault_secrets[typed] = max(until, self._vault_secrets.get(typed, until))
                        # Judged in this tab alone.
                        kept: dict[HeldValue, float] = {
                            one: ends for one, ends in self._kept_out.get(tab.page, {}).items() if ends > now
                        }
                        kept[held] = max(until, kept.get(held, until))
                        self._kept_out[tab.page] = kept
                    if kind in _REMARKED_PARTS:
                        tab.filled_values.add(held)
                    if self._config.mode == "own":
                        self._filled_profiles.add(self._profile)
                    tab.vault_held = True

                await fill_login_field(
                    tab.page, fill.ref, fill.kind, value, fills_on, typing, _ms(self._config.action_timeout_s)
                )
            await self._settle(tab)
            await self._enforce_landing(tab)
            return await self._changes_view(thread, tab, True)

    def vault_values(self, thread_id: str) -> frozenset[str]:
        """The values filled into the thread's pages from the password manager (item 493), which no result of the
        browser tools shows."""
        return frozenset(self._vault_values.get(thread_id, ()))

    def held_back(self, thread_id: str, text: str) -> str:
        """``text`` without what no result of the thread's browser tools shows: the values filled into its pages from
        the password manager (item 493), and the cookies of the sites its scripts ran in, long enough to be a session
        (item 533): a script that read them, or wrote one into a page or an address, may not hand them to the model.
        Everything the tools hand to a model, or to a judge, goes through here."""
        return masked(
            masked(text, self._vault_values.get(thread_id, ())), self._cookie_values.get(thread_id, ()), HIDDEN_VALUE
        )

    def holds_back(self, thread_id: str) -> bool:
        """Whether anything is held back from the thread's results (:meth:`held_back`)."""
        return bool(self._vault_values.get(thread_id) or self._cookie_values.get(thread_id))

    def mask_held(self, thread_id: str, error: DotError) -> None:
        """Take what :meth:`held_back` holds back out of ``error``'s words and context, which the model reads."""
        mask_error(error, self._vault_values.get(thread_id, ()))
        mask_error(error, self._cookie_values.get(thread_id, ()), HIDDEN_VALUE)

    async def run_script(
        self, thread_id: str, script: str, timeout_s: float, hide_secrets: bool, refs: Sequence[str] = ()
    ) -> ScriptRun:
        """Run ``script`` in the page of the thread's current tab and wait at most ``timeout_s`` for its value (item
        263), with the elements of ``refs`` as its arguments when they are given (item 738). What the script led to
        is waited for as after a step (a tab it opened, a dialog), and the page is settled
        after every script as after a step (item 270), so that a navigation it started is let load, also one that
        commits after its value came back; the address rules then hold as after a step: a tab that ended on an address
        they forbid is reset to a blank page and the call fails. After a script still running when the wait ended, only
        a navigation under way is let commit: the page cannot tell its DOM is quiet while the script holds it. With
        ``hide_secrets`` (every mode but yolo, item 533) it is refused on a page that holds a password, one-time-code or
        card field, which a script reads whole, an autofilled value too, and what it gives back shows the cookies of the
        site as ``[hidden]``."""
        async with self._locked(thread_id) as thread:
            # A script can read a field a login was filled into, and send it anywhere; a tab of the same thread can
            # reach that page as its opener does (item 493).
            if any(one.vault_held for one in thread.tabs):
                raise BrowserError(
                    "a login from the password manager was filled into a tab of this chat: scripts stay off in its "
                    "tabs while that tab is open. Close it (browser_close_tab) and go on in a new one, where the "
                    "sign-in holds",
                    {"tabs": len([one for one in thread.tabs if one.vault_held])},
                )
            tab: Tab = await self._current_tab(thread)
            # No script runs in a page on a URL the rules forbid (item 554): it would read the page, and act in it.
            await self._enforce_landing(tab)
            if hide_secrets and await holds_secret_field(tab.page):
                raise BrowserError(
                    "a script stays off a page that holds a password, one-time-code or card field: it could read what "
                    "the field holds, a value Chrome filled in too. Use the other browser tools, which hide such "
                    "values, or ask the user to take over the browser",
                    {"url": tab.page.url},
                )
            thread.scripted = True
            address: str = tab.page.url
            # What the page sends while the script runs and the page settles (item 741): the run's record and the
            # value say where a script may have sent something; nothing is held back for it.
            sent: Counter[str] = Counter()

            def seen(request: Request) -> None:
                if request.method.upper() not in _READ_METHODS:
                    parts = urlsplit(request.url)
                    sent[f"{request.method.upper()} {parts.hostname or ''}{parts.path}"[:_SENT_CHARS]] += 1

            if hide_secrets:
                # The cookies of the page the script runs in: held before it runs as well as after, so that one it
                # deletes or overwrites is hidden too, and for the rest of the chat (item 533).
                self._cookie_values.setdefault(thread_id, set()).update(await cookie_secrets(tab.page, address))
            tab.page.on("request", seen)
            try:
                before: _Watch = self._watch(thread, tab)
                started: float = time.monotonic()
                end, text = await run_script(tab.page, script, timeout_s, refs)
                if hide_secrets:
                    self._cookie_values.setdefault(thread_id, set()).update(await cookie_secrets(tab.page, address))
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
            except DotError as error:
                # What the script led the tab to may be an address that carries a cookie: the refusal names it.
                self.mask_held(thread_id, error)
                raise
            finally:
                tab.page.remove_listener("request", seen)
            told: str | None = _TAB_OPENING if surprise is None and opening else surprise
            lines: tuple[str, ...] = tuple(
                self.held_back(thread_id, line if count == 1 else f"{line} (×{count})")
                for line, count in sent.most_common(_SENT_LINES)
            )
            if sent:
                _log.info(
                    "browser_script_sent",
                    requests=sum(sent.values()),
                    hosts=", ".join(sorted({line.split(" ", 1)[1].split("/", 1)[0] for line in sent})),
                )
            return ScriptRun(
                end=end,
                text=self.held_back(thread_id, text),
                url=self.held_back(thread_id, current.page.url),
                surprise=None if told is None else self.held_back(thread_id, told),
                seconds=seconds,
                sent=lines,
            )

    async def back(self, thread_id: str, hide_secrets: bool) -> PageView:
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            tab.status = await go_back(tab.page, _ms(self._config.navigation_timeout_s))
            await self._settle(tab)
            await self._enforce_landing(tab)
            capture: _Capture = await self._capture_loaded(tab, hide_secrets)
            return await self._assemble(thread, tab, capture, capture.rendered, "full")

    async def switch_tab(self, thread_id: str, index: int, hide_secrets: bool) -> PageView:
        async with self._locked(thread_id) as thread:
            self._require_tab_index(thread, index)
            thread.active = index - 1
            tab: Tab = thread.tabs[thread.active]
            # The tab is made the window's visible one only when the window may be raised (item 97).
            if self._config.raise_window:
                await bring_to_front(tab.page)
            return await self._full_view(thread, tab, hide_secrets)

    async def close_tab(self, thread_id: str, index: int | None, hide_secrets: bool) -> PageView:
        """Close the chat's tab ``index``, or every tab of the chat but its current one when ``index`` is ``None``
        (item 696), and show the tab that is current then. Only the chat's own tabs are counted and closed."""
        async with self._locked(thread_id) as thread:
            if index is None:
                current: Tab = await self._current_tab(thread)
                for other in [tab for tab in thread.tabs if tab is not current]:
                    await self._close_tab_object(thread, other)
            else:
                self._require_tab_index(thread, index)
                await self._close_tab_object(thread, thread.tabs[index - 1])
            tab: Tab = await self._current_tab(thread)
            return await self._full_view(thread, tab, hide_secrets)

    def _require_tab_index(self, thread: ThreadTabs, index: int) -> None:
        if not 1 <= index <= len(thread.tabs):
            raise BrowserError("there is no such tab", {"index": index, "open_tabs": len(thread.tabs)})

    async def upload(self, thread_id: str, ref: str, paths: Sequence[str], hide_secrets: bool) -> PageView:
        """Give ``paths`` to the file field of the upload control ``ref``, or to the file chooser a click on it opens
        (:func:`upload_files`)."""
        async with self._locked(thread_id) as thread:
            tab: Tab = await self._current_tab(thread)
            await self._enforce_landing(tab)
            tab.expect_chooser = True
            try:
                await upload_files(tab.page, ref, list(paths), _ms(self._config.action_timeout_s))
            finally:
                tab.expect_chooser = False
            await self._settle(tab)
            return await self._changes_view(thread, tab, hide_secrets)

    # ------------------------------------------------------------------ reads without browser access

    def peek_line(self, thread_id: str, ref: str) -> str | None:
        """The snapshot line of ``ref`` in the thread's current tab, for judging a step before it runs."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return None
        return line_for_ref(thread.tabs[thread.active].lines, ref)

    def peek_near(self, thread_id: str, ref: str) -> tuple[str, ...]:
        """The snapshot lines of the buttons near the field ``ref`` in the thread's current tab, which Enter in it may
        press (:func:`dotagent.browser.snapshot.controls_near`), for judging a step before it runs (item 682)."""
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return ()
        return controls_near(thread.tabs[thread.active].lines, ref)

    def current_url(self, thread_id: str) -> str | None:
        thread: ThreadTabs | None = self._threads.get(thread_id)
        if thread is None or not thread.tabs:
            return None
        return thread.tabs[thread.active].page.url
