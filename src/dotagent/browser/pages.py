"""Per-tab state and the page events that feed it: dialogs, downloads, file choosers and navigations.

Handlers never raise into Playwright's event loop: a failure to resolve a dialog or save a download is logged with
structured fields and shown to the model as a note.
"""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from playwright.async_api import CDPSession, Dialog, Download, FileChooser, Page, Request, Response, WebSocket
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.console import KEPT_MESSAGES, ConsoleEntry, note_message, note_page_error
from dotagent.browser.network import KEPT_CALLS, Fetched, note_call
from dotagent.browser.safety import dialog_is_financial, safe_download_name
from dotagent.browser.snapshot import SnapshotLine
from dotagent.browser.types import HeldValue, Viewport
from dotagent.core.errors import BrowserError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle

_log = get_logger("dotagent.browser.pages")

_NOTE_LOG_CHARS: int = 300
_MAX_NOTES: int = 30
# How many of the tabs that closed under a thread it remembers, newest last (item 1209).
_KEPT_CLOSED: int = 6
_CLOSED_URL_CHARS: int = 200


def _set_event() -> asyncio.Event:
    event: asyncio.Event = asyncio.Event()
    event.set()
    return event


@dataclass(slots=True)
class Tab:
    """One page owned by a thread, with what happened to it since the model last looked."""

    page: Page
    notes: list[str] = field(default_factory=list)
    events: int = 0  # dialogs, downloads and file choosers; a change during a step means the page surprised us
    nav_inflight: int = 0  # main-frame navigation requests that have started and not finished
    # The page's own requests under way (fetch, XHR, an EventSource's stream) and its WebSockets open: a page waiting on
    # its server (item 774).
    fetches: int = 0
    sockets: int = 0
    # What the tab's DevTools session heard of its downloads (item 1298): the address each was begun for, by the id
    # Chrome gave it, the ids already given to a download Dot watches, and the watches under way.
    downloads_cdp: CDPSession | None = None
    download_begun: dict[str, str] = field(default_factory=dict)
    download_ids: set[str] = field(default_factory=set)
    download_watches: list["_Watch"] = field(default_factory=list)
    nav_idle: asyncio.Event = field(default_factory=_set_event)
    status: int | None = None
    accept_confirms: bool = False
    expect_chooser: bool = False
    lines: tuple[SnapshotLine, ...] = ()
    rendered: str = ""
    rendered_url: str = ""
    # Whether the last view was of a part of the page (item 743): the next one is not compared with it.
    rendered_partial: bool = False
    # Chrome's id of the page, which a tab the page opens names as its opener, and the ids of the tabs Chrome made for
    # the page that its thread has not taken in yet (item 342; see :func:`watch_births`).
    target_id: str = ""
    born: set[str] = field(default_factory=set)
    born_changed: asyncio.Event = field(default_factory=asyncio.Event)
    # A login of the password manager was filled into a page of the tab (item 493): no script runs in the tabs of its
    # thread while the tab is open. Going on to another page does not end it: going back can bring the filled page
    # back whole, from Chrome's cache of pages, without loading it again.
    vault_held: bool = False
    # What was filled into the tab that is secret and can be told from other text (item 621): a field of a later
    # document of the tab that holds one of them is marked again, as the field it was filled into (``remark_held``),
    # in the documents of the origin it was filled into and in no other. The same values are kept out of the addresses
    # this tab's pages send by the session, which holds them by tab (``BrowserSession._kept_out``).
    filled_values: set[HeldValue] = field(default_factory=set)
    # When a call of the thread last ended with this tab current (``time.monotonic``): a tab in the background unused
    # for ``[browser] background_tab_idle_s`` is closed (item 696).
    last_used: float = field(default_factory=time.monotonic)
    # The calls its page's scripts made in the background, newest last (item 714): what browser_requests lists.
    calls: deque[Fetched] = field(default_factory=lambda: deque(maxlen=KEPT_CALLS))
    # How many requests the page has started, of any kind: a step that started one did something (item 739).
    requests: int = 0
    # What its page wrote to the console and the errors it did not catch, since the page was loaded, newest last
    # (item 746): what browser_console lists.
    console: deque[ConsoleEntry] = field(default_factory=lambda: deque(maxlen=KEPT_MESSAGES))
    # The size the run gave the tab (``browser_viewport``, item 746), and the DevTools session that lays it over the
    # tab's own; both None while the tab has the size of every tab.
    viewport: Viewport | None = None
    sizing: CDPSession | None = None


@dataclass(slots=True)
class ThreadTabs:
    """The tabs one conversation thread owns, in the order they were opened."""

    tabs: list[Tab] = field(default_factory=list)
    active: int = 0
    last_used: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # A script of the run ran in a tab of the thread (item 493): no login is filled into any of its tabs while one is
    # open, since the script may have left a listener that copies what is typed, in its page, in a tab it opened, or
    # in a page Chrome brings back from its cache. Once the thread has no tab left, nothing it touched is left either.
    scripted: bool = False
    # The tabs that closed without the run closing them, newest last, each as its address and what closed it (item
    # 1209): a run that asks for a tab by a number it saw before is told what became of it.
    closed: deque[str] = field(default_factory=lambda: deque(maxlen=_KEPT_CLOSED))
    # Chrome quit or crashed under tabs the thread held (item 1297): every one of them, and the refs the run took from
    # them, are gone. The first browser call afterwards says so instead of working in a new blank tab.
    tabs_lost: bool = False
    # When the last tab of the thread went away without Dot closing it (its page, or Chrome): Chrome's crash closes the
    # pages one by one before it tells the context is gone, so a thread left with no tab a moment before that was
    # emptied by it (``monotonic`` time, 0 when never).
    emptied_at: float = 0.0

    def remove(self, tab: Tab) -> None:
        """Forget ``tab``; the active index keeps pointing at the same tab, or at its neighbour when it was active."""
        index: int = self.tabs.index(tab)
        self.tabs.remove(tab)
        if index < self.active:
            self.active -= 1
        self.active = max(0, min(self.active, len(self.tabs) - 1))
        if not self.tabs:
            self.scripted = False


def note_closed(thread: ThreadTabs, url: str, why: str) -> None:
    """Remember that the tab at ``url`` of ``thread`` closed, and what closed it (item 1209)."""
    thread.closed.append(f"{truncate_middle(url, _CLOSED_URL_CHARS)} ({why})")


def missing_tab(index: int, thread: ThreadTabs) -> str:
    """Why a run cannot have tab ``index`` of ``thread`` (item 1209): the tabs the thread has now, by number and
    address (the page's title is left out, which a page writes as it likes), and the tabs that closed under it. A
    run that saw a tab numbered in an earlier view, before Dot tidied the chat's tabs at the end of a run or the page
    closed itself, otherwise had only the count to go on."""
    listed: str = (
        "no tab open"
        if not thread.tabs
        else "these tabs open: "
        + "; ".join(
            f"[{number}]{'*' if number - 1 == thread.active else ''} {truncate_middle(tab.page.url, _CLOSED_URL_CHARS)}"
            for number, tab in enumerate(thread.tabs, start=1)
        )
    )
    parts: list[str] = [f"there is no tab {index}: the chat has {listed}"]
    if thread.closed:
        parts.append(f"closed lately: {'; '.join(thread.closed)}")
    parts.append("work in a tab listed here, or open a closed page again with browser_new_tab")
    return ". ".join(parts)


def add_note(tab: Tab, text: str) -> None:
    tab.notes.append(text)
    if len(tab.notes) > _MAX_NOTES:
        del tab.notes[: len(tab.notes) - _MAX_NOTES]


def unique_download_path(directory: Path, name: str) -> Path:
    """``directory/name``, or with a numeric suffix when a file of that name exists already, made empty here so that
    it is taken: two downloads of one name at once each get a file of their own, where both were handed the free name
    and the second overwrote the first."""
    candidate: Path = directory / name
    counter: int = 1
    while True:
        try:
            candidate.touch(exist_ok=False)
        except FileExistsError:
            candidate = directory / f"{Path(name).stem}-{counter}{Path(name).suffix}"
            counter += 1
        else:
            return candidate


def _drop_empty(target: Path) -> None:
    """Remove the file :func:`unique_download_path` took for a download that failed, when nothing was written to it."""
    with contextlib.suppress(OSError):
        if target.stat().st_size == 0:
            target.unlink()


async def _on_dialog(tab: Tab, dialog: Dialog) -> None:
    tab.events += 1
    kind: str = dialog.type
    message: str = dialog.message
    accept: bool = kind in ("alert", "beforeunload") or (
        kind == "confirm" and tab.accept_confirms and not dialog_is_financial(message)
    )
    try:
        if accept:
            await dialog.accept()
        else:
            await dialog.dismiss()
    except PlaywrightError as error:
        _log.warning(
            "browser_dialog_unresolved",
            kind=kind,
            message=truncate_middle(message, _NOTE_LOG_CHARS),
            error=truncate_middle(str(error), _NOTE_LOG_CHARS),
        )
        return
    verdict: str = "accepted" if accept else "dismissed"
    hint: str = ""
    if not accept and kind == "confirm":
        hint = " (to accept confirm dialogs, repeat the batch with accept_dialogs=true)"
    elif kind == "prompt":
        hint = " (prompt dialogs cannot be answered; tell the user if the input is needed)"
    add_note(tab, f"{kind} dialog {verdict}: {message}{hint}")


_MEGABYTE: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DownloadLimits:
    """What a download may take (item 1298): the largest file, and the most the downloads folder may hold, in bytes; 0
    is no limit."""

    max_bytes: int = 0
    quota_bytes: int = 0


def _megabytes(size: int) -> str:
    return f"{size / _MEGABYTE:.0f} MB" if size >= _MEGABYTE else f"{size} bytes"


def folder_bytes(directory: Path, leave: Path | None = None) -> int:
    """What the files under ``directory`` hold, ``leave`` apart (the file a download has just taken). A file that goes
    while the folder is walked has nothing to count."""
    total: int = 0
    for path in directory.rglob("*"):
        if path == leave:
            continue
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _room(limits: DownloadLimits, held: int) -> tuple[int | None, str]:
    """How many bytes one more download may take, and which limit sets that: ``None`` when nothing limits it."""
    rooms: list[tuple[int, str]] = []
    if limits.max_bytes > 0:
        rooms.append((limits.max_bytes, f"download_max_mb ({_megabytes(limits.max_bytes)})"))
    if limits.quota_bytes > 0:
        rooms.append(
            (
                max(0, limits.quota_bytes - held),
                f"download_quota_mb (the folder holds {_megabytes(held)} of {_megabytes(limits.quota_bytes)})",
            )
        )
    if not rooms:
        return (None, "")
    return min(rooms, key=lambda room: room[0])


class _Watch:
    """The bytes a download has taken so far, from Chrome's own progress events for the tab, and the cancel of a
    download that passes its room. Chrome names a download by an id that Playwright's ``Download`` does not carry, so
    the id is the one begun for the same address that no other watch has taken."""

    def __init__(self, tab: Tab, download: Download, room: int | None, why: str) -> None:
        self.tab = tab
        self.download = download
        self.room = room
        self.why = why
        self.guid: str | None = None
        self.exceeded: bool = False
        self.cancelling: asyncio.Task[None] | None = None

    def begun(self, guid: str, url: str) -> None:
        if self.guid is None and url == self.download.url and guid not in self.tab.download_ids:
            self.guid = guid
            self.tab.download_ids.add(guid)

    def progress(self, event: Mapping[str, object]) -> None:
        if self.room is None or self.exceeded or event.get("guid") != self.guid or self.guid is None:
            return
        sizes: list[int] = [
            size
            for size in (event.get("receivedBytes"), event.get("totalBytes"))
            if isinstance(size, int) and not isinstance(size, bool)
        ]
        if sizes and max(sizes) > self.room:
            self.exceeded = True
            self.cancelling = asyncio.create_task(self.download.cancel())


def _on_download_begun(tab: Tab, event: Mapping[str, object]) -> None:
    guid: object = event.get("guid")
    url: object = event.get("url")
    if isinstance(guid, str) and isinstance(url, str):
        tab.download_begun[guid] = url
        for watch in list(tab.download_watches):
            watch.begun(guid, url)


def _on_download_progress(tab: Tab, event: Mapping[str, object]) -> None:
    for watch in list(tab.download_watches):
        watch.progress(event)


def _forget_unclaimed(tab: Tab, url: str) -> None:
    """Drop what the tab heard of a download of ``url`` that no watch took (one turned away at the quota, or whose
    events a watch never matched): left in ``download_begun``, its id would be handed to the next download of the same
    address, whose own progress events would then be ignored and its size judged only when it had finished."""
    for guid, begun_url in list(tab.download_begun.items()):
        if begun_url == url and guid not in tab.download_ids:
            del tab.download_begun[guid]


async def watch_downloads(tab: Tab) -> None:
    """Have the tab's downloads watched for their size while they come in (item 1298): Chrome reports the bytes taken
    only to a DevTools session that was enabled before the download began, so the tab gets one when it is registered.
    A session that cannot be made is logged: the size is then judged when the download has finished."""
    try:
        cdp: CDPSession = await tab.page.context.new_cdp_session(tab.page)
        cdp.on("Page.downloadWillBegin", partial(_on_download_begun, tab))
        cdp.on("Page.downloadProgress", partial(_on_download_progress, tab))
        await cdp.send("Page.enable")
    except PlaywrightError as error:
        _log.warning("browser_downloads_unwatched", error=truncate_middle(str(error), _NOTE_LOG_CHARS))
        return
    tab.downloads_cdp = cdp


async def _on_download(tab: Tab, directory: Path, limits: DownloadLimits, download: Download) -> None:
    tab.events += 1
    tab.nav_inflight = 0  # a download is not a navigation, so nothing is left to wait for
    tab.nav_idle.set()
    name: str = safe_download_name(download.suggested_filename)
    target: Path = await asyncio.to_thread(unique_download_path, directory, name)
    held: int = await asyncio.to_thread(folder_bytes, directory, target) if limits.quota_bytes > 0 else 0
    room, why = _room(limits, held)
    if room is not None and room <= 0:
        await _refuse(tab, download, target, f"the downloads folder is at its limit, {why}")
        _forget_unclaimed(tab, download.url)
        return
    add_note(tab, f"download started: {target.name}")
    watch: _Watch = _Watch(tab, download, room, why)
    for guid, url in list(tab.download_begun.items()):
        watch.begun(guid, url)
    tab.download_watches.append(watch)
    try:
        await download.save_as(target)
        failure: str | None = await download.failure()
    except PlaywrightError as error:
        await _ended_badly(tab, target, watch, error)
        return
    finally:
        tab.download_watches.remove(watch)
        if watch.guid is not None:
            tab.download_begun.pop(watch.guid, None)
            tab.download_ids.discard(watch.guid)
        else:
            _forget_unclaimed(tab, download.url)
        if watch.cancelling is not None:
            with contextlib.suppress(PlaywrightError):
                await watch.cancelling
    if watch.exceeded:
        await _discard(tab, target, f"it passed {why}")
        return
    if failure is not None:
        add_note(tab, f"download of {target.name} failed: {failure}")
        await asyncio.to_thread(_drop_empty, target)
        return
    size: int = await asyncio.to_thread(lambda: target.stat().st_size)
    if room is not None and size > room:
        # Finished before a progress event said how large it was, or the watch could not be made.
        await _discard(tab, target, f"it was {_megabytes(size)}, which passes {why}")
        return
    add_note(tab, f"download saved to {target} ({size} bytes); it was not opened")


async def _discard(tab: Tab, target: Path, reason: str) -> None:
    """Delete what a download that passed its limit left, and tell the run."""
    with contextlib.suppress(FileNotFoundError):
        await asyncio.to_thread(target.unlink)
    _log.warning("browser_download_over_limit", file=target.name, reason=reason)
    add_note(tab, f"download of {target.name} was cancelled and deleted: {reason}")


async def _refuse(tab: Tab, download: Download, target: Path, reason: str) -> None:
    """Turn a download away before any of it is saved."""
    with contextlib.suppress(PlaywrightError):
        await download.cancel()
    await _discard(tab, target, reason)


async def _ended_badly(tab: Tab, target: Path, watch: _Watch, error: PlaywrightError) -> None:
    if watch.exceeded:
        await _discard(tab, target, f"it passed {watch.why}")
        return
    _log.warning("browser_download_failed", file=target.name, error=truncate_middle(str(error), _NOTE_LOG_CHARS))
    add_note(tab, f"download of {target.name} failed: {truncate_middle(str(error), _NOTE_LOG_CHARS)}")
    await asyncio.to_thread(_drop_empty, target)


async def _on_file_chooser(tab: Tab, chooser: FileChooser) -> None:
    if tab.expect_chooser:
        return
    tab.events += 1
    add_note(
        tab,
        "a file chooser tried to open and was cancelled; use browser_upload with the ref of the upload button or input",
    )


def _is_main_navigation(tab: Tab, request: Request) -> bool:
    return request.is_navigation_request() and request.frame == tab.page.main_frame


_FETCHES: frozenset[str] = frozenset({"fetch", "xhr", "eventsource"})


def _on_request(tab: Tab, request: Request) -> None:
    tab.requests += 1
    if request.resource_type in _FETCHES:
        tab.fetches += 1
    if _is_main_navigation(tab, request):
        tab.nav_inflight += 1
        tab.nav_idle.clear()


def _on_request_end(tab: Tab, request: Request) -> None:
    if request.resource_type in _FETCHES:
        tab.fetches = max(0, tab.fetches - 1)
    if _is_main_navigation(tab, request):
        tab.nav_inflight = max(0, tab.nav_inflight - 1)
        if tab.nav_inflight == 0:
            tab.nav_idle.set()


def _on_websocket(tab: Tab, socket: WebSocket) -> None:
    tab.sockets += 1
    socket.on("close", partial(_on_websocket_close, tab))


def _on_websocket_close(tab: Tab, socket: WebSocket) -> None:
    tab.sockets = max(0, tab.sockets - 1)


def _on_response(tab: Tab, response: Response) -> None:
    if _is_main_navigation(tab, response.request):
        tab.status = response.status
        # A new document is on its way: the console of the page starts again (item 746). A route of a page's own
        # scripts (pushState, a fragment) makes no request, and keeps what the page wrote before it.
        tab.console.clear()
    note_call(tab.calls, response)


def _text(payload: object, key: str) -> str | None:
    value: object = payload.get(key) if isinstance(payload, Mapping) else None
    return value if isinstance(value, str) and value else None


def target_id_of(answer: object) -> str | None:
    """The tab id in Chrome's answer to ``Target.createTarget``."""
    return _text(answer, "targetId")


def chrome_lists(made: str, answer: object) -> bool:
    """Whether Chrome's answer to ``Target.getTargets`` lists the tab ``made``: one it does not list has no window
    to ask for."""
    infos: object = answer.get("targetInfos") if isinstance(answer, Mapping) else None
    return any(
        isinstance(info, Mapping) and info.get("type") == "page" and _text(info, "targetId") == made
        for info in (infos if isinstance(infos, list) else [])
    )


def unhanded_tab(made: str, answer: object) -> str:
    """What Chrome's answer to ``Target.getTargets`` says of the tab ``made`` that Playwright never reported (item
    1002): gone, or its address (still ``about:blank`` without the mark: its first page never committed), whether a
    client is attached to it (Playwright attaches to every tab of its context) and whether it is in the browser context
    of the other tabs. The handover takes 0.01 to 0.15 s, also with every core busy (measured 2026-11-06), and the cause
    of the times it did not come within 8 s could not be read from the record."""
    infos: object = answer.get("targetInfos") if isinstance(answer, Mapping) else None
    tabs: list[Mapping[str, object]] = [
        info
        for info in (infos if isinstance(infos, list) else [])
        if isinstance(info, Mapping) and info.get("type") == "page"
    ]
    ours: Mapping[str, object] | None = next((info for info in tabs if _text(info, "targetId") == made), None)
    if ours is None:
        return f"Chrome lists no such tab any more; it lists {len(tabs)} other tabs"
    others: set[str] = {
        theirs for info in tabs if info is not ours and (theirs := _text(info, "browserContextId")) is not None
    }
    context: str | None = _text(ours, "browserContextId")
    where: str = (
        "no other tab to compare its browser context with"
        if not others
        else "the browser context of the other tabs"
        if context in others
        else "a browser context no other tab is in"
    )
    attached: str = "attached" if ours.get("attached") is True else "not attached"
    return f"Chrome lists the tab at {_text(ours, 'url') or 'no address'!r}, {attached}, in {where}"


async def page_target_id(page: Page) -> str | None:
    """The id Chrome gives the tab of ``page``, ``None`` when the page cannot tell it (closed since)."""
    try:
        session: CDPSession = await page.context.new_cdp_session(page)
    except PlaywrightError:
        return None
    try:
        own: dict[str, object] = await session.send("Target.getTargetInfo")
    except PlaywrightError:
        return None
    finally:
        with contextlib.suppress(PlaywrightError):
            await session.detach()
    return _text(own.get("targetInfo"), "targetId")


def _on_target_created(tab: Tab, event: object) -> None:
    info: object = event.get("targetInfo") if isinstance(event, Mapping) else None
    target: str | None = _text(info, "targetId")
    if target is not None and _text(info, "openerId") == tab.target_id:
        tab.born.add(target)
        tab.born_changed.set()


def _on_target_destroyed(tab: Tab, event: object) -> None:
    target: str | None = _text(event, "targetId")
    if target in tab.born:
        tab.born.discard(target)
        tab.born_changed.set()


def taken_in(opener: Tab, tab: Tab) -> None:
    """``tab``, which Chrome made for the page of ``opener``, is in its thread now, or closed before it got there."""
    opener.born.discard(tab.target_id)
    opener.born_changed.set()


async def watch_births(tab: Tab) -> None:
    """Have ``tab`` told of every tab Chrome makes for its page (a popup, a link or a form that opens a new tab) the
    moment Chrome makes it, through a CDP session of the tab's own (item 342): Playwright reports such a tab only once
    its page has answered (411 ms after the click for a page that answers in 400 ms, measured), while Chrome tells of
    it before the click that made it returns, and of none it refused (a second popup of one click, which the Chrome of
    the user blocks)."""
    try:
        session: CDPSession = await tab.page.context.new_cdp_session(tab.page)
        own: dict[str, object] = await session.send("Target.getTargetInfo")
    except PlaywrightError as error:
        raise _watch_failure(tab, error) from error
    target: str | None = _text(own.get("targetInfo"), "targetId")
    if target is None:
        raise BrowserError("Chrome did not tell the id of the tab", {"url": tab.page.url, "answer": repr(own)[:200]})
    tab.target_id = target
    session.on("Target.targetCreated", partial(_on_target_created, tab))
    session.on("Target.targetDestroyed", partial(_on_target_destroyed, tab))
    try:
        await session.send("Target.setDiscoverTargets", {"discover": True, "filter": [{"type": "page"}]})
    except PlaywrightError as error:
        raise _watch_failure(tab, error) from error


def _watch_failure(tab: Tab, error: PlaywrightError) -> BrowserError:
    return BrowserError(
        "watching the tab for the tabs it opens failed",
        {"url": tab.page.url, "error": truncate_middle(str(error), _NOTE_LOG_CHARS)},
    )


def attach_handlers(tab: Tab, downloads: Path, limits: DownloadLimits) -> None:
    """Wire the page events of ``tab``. Async handlers are scheduled by Playwright; the sync ones only count."""
    page: Page = tab.page
    page.on("dialog", partial(_on_dialog, tab))
    page.on("download", partial(_on_download, tab, downloads, limits))
    page.on("filechooser", partial(_on_file_chooser, tab))
    page.on("request", partial(_on_request, tab))
    page.on("requestfinished", partial(_on_request_end, tab))
    page.on("requestfailed", partial(_on_request_end, tab))
    page.on("response", partial(_on_response, tab))
    page.on("websocket", partial(_on_websocket, tab))
    page.on("console", partial(note_message, tab.console))
    page.on("pageerror", partial(note_page_error, tab.console))
