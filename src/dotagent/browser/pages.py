"""Per-tab state and the page events that feed it: dialogs, downloads, file choosers and navigations.

Handlers never raise into Playwright's event loop: a failure to resolve a dialog or save a download is logged with
structured fields and shown to the model as a note.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from playwright.async_api import CDPSession, Dialog, Download, FileChooser, Page, Request, Response
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.safety import dialog_is_financial, safe_download_name
from dotagent.browser.snapshot import SnapshotLine
from dotagent.core.errors import BrowserError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle

_log = get_logger("dotagent.browser.pages")

_NOTE_LOG_CHARS: int = 300
_MAX_NOTES: int = 30


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
    nav_idle: asyncio.Event = field(default_factory=_set_event)
    status: int | None = None
    accept_confirms: bool = False
    expect_chooser: bool = False
    lines: tuple[SnapshotLine, ...] = ()
    rendered: str = ""
    rendered_url: str = ""
    # Chrome's id of the page, which a tab the page opens names as its opener, and the ids of the tabs Chrome made for
    # the page that its thread has not taken in yet (item 342; see :func:`watch_births`).
    target_id: str = ""
    born: set[str] = field(default_factory=set)
    born_changed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class ThreadTabs:
    """The tabs one conversation thread owns, in the order they were opened."""

    tabs: list[Tab] = field(default_factory=list)
    active: int = 0
    last_used: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def remove(self, tab: Tab) -> None:
        """Forget ``tab``; the active index keeps pointing at the same tab, or at its neighbour when it was active."""
        index: int = self.tabs.index(tab)
        self.tabs.remove(tab)
        if index < self.active:
            self.active -= 1
        self.active = max(0, min(self.active, len(self.tabs) - 1))


def add_note(tab: Tab, text: str) -> None:
    tab.notes.append(text)
    if len(tab.notes) > _MAX_NOTES:
        del tab.notes[: len(tab.notes) - _MAX_NOTES]


def unique_download_path(directory: Path, name: str) -> Path:
    """``directory/name``, or with a numeric suffix when a file of that name exists already."""
    candidate: Path = directory / name
    counter: int = 1
    while candidate.exists():
        candidate = directory / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter += 1
    return candidate


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


async def _on_download(tab: Tab, directory: Path, download: Download) -> None:
    tab.events += 1
    tab.nav_inflight = 0  # a download is not a navigation, so nothing is left to wait for
    tab.nav_idle.set()
    name: str = safe_download_name(download.suggested_filename)
    target: Path = await asyncio.to_thread(unique_download_path, directory, name)
    add_note(tab, f"download started: {target.name}")
    try:
        await download.save_as(target)
        failure: str | None = await download.failure()
    except PlaywrightError as error:
        _log.warning("browser_download_failed", file=target.name, error=truncate_middle(str(error), _NOTE_LOG_CHARS))
        add_note(tab, f"download of {target.name} failed: {truncate_middle(str(error), _NOTE_LOG_CHARS)}")
        return
    if failure is not None:
        add_note(tab, f"download of {target.name} failed: {failure}")
        return
    size: int = await asyncio.to_thread(lambda: target.stat().st_size)
    add_note(tab, f"download saved to {target} ({size} bytes); it was not opened")


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


def _on_request(tab: Tab, request: Request) -> None:
    if _is_main_navigation(tab, request):
        tab.nav_inflight += 1
        tab.nav_idle.clear()


def _on_request_end(tab: Tab, request: Request) -> None:
    if _is_main_navigation(tab, request):
        tab.nav_inflight = max(0, tab.nav_inflight - 1)
        if tab.nav_inflight == 0:
            tab.nav_idle.set()


def _on_response(tab: Tab, response: Response) -> None:
    if _is_main_navigation(tab, response.request):
        tab.status = response.status


def _text(payload: object, key: str) -> str | None:
    value: object = payload.get(key) if isinstance(payload, Mapping) else None
    return value if isinstance(value, str) and value else None


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


def attach_handlers(tab: Tab, downloads: Path) -> None:
    """Wire the page events of ``tab``. Async handlers are scheduled by Playwright; the sync ones only count."""
    page: Page = tab.page
    page.on("dialog", partial(_on_dialog, tab))
    page.on("download", partial(_on_download, tab, downloads))
    page.on("filechooser", partial(_on_file_chooser, tab))
    page.on("request", partial(_on_request, tab))
    page.on("requestfinished", partial(_on_request_end, tab))
    page.on("requestfailed", partial(_on_request_end, tab))
    page.on("response", partial(_on_response, tab))
