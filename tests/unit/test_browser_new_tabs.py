"""The review of every module (2026-10-28): a new tab is told by the id Chrome gave it, not by an address that may not
have committed yet, and two downloads of one name at once each get a file of their own."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from dotagent.browser import pages, session
from dotagent.browser.pages import _drop_empty, unique_download_path
from dotagent.browser.session import BrowserSession
from dotagent.core.errors import BrowserError

_MARK: str = "about:blank#dot-mark"


def _page(url: str, target: str) -> Page:
    return cast(Page, SimpleNamespace(url=url, target=target, is_closed=lambda: False))


@pytest.fixture
def ids(monkeypatch: pytest.MonkeyPatch) -> None:
    async def target_of(page: Page) -> str:
        return str(cast(Any, page).target)

    monkeypatch.setattr(session, "page_target_id", target_of)


async def _matched(arrivals: list[Page], made: str | None) -> Page:
    queue: asyncio.Queue[Page] = asyncio.Queue()
    for page in arrivals:
        queue.put_nowait(page)
    async with asyncio.timeout(1):
        return await BrowserSession._page_of_target(queue, _MARK, made)


async def test_a_new_tab_reported_before_its_address_committed_is_found_by_its_id(ids: None) -> None:
    other: Page = _page("about:blank", "T-other")
    ours: Page = _page("", "T-ours")
    assert await _matched([other, ours], "T-ours") is ours


async def test_a_new_tab_whose_address_committed_is_found_by_it(ids: None) -> None:
    ours: Page = _page(_MARK, "T-ours")
    assert await _matched([_page("", "T-other"), ours], None) is ours


async def test_another_calls_tab_is_never_taken(ids: None) -> None:
    with pytest.raises(TimeoutError):
        await _matched([_page("", "T-other"), _page("about:blank", "T-third")], "T-ours")


def test_target_id_of_reads_chromes_answer() -> None:
    assert pages.target_id_of({"targetId": "T1"}) == "T1"
    assert pages.target_id_of({}) is None and pages.target_id_of("nope") is None


def test_two_downloads_of_one_name_each_take_a_file(tmp_path: Path) -> None:
    first: Path = unique_download_path(tmp_path, "report.pdf")
    second: Path = unique_download_path(tmp_path, "report.pdf")
    assert first.name == "report.pdf" and second.name == "report-1.pdf"
    assert first.exists() and second.exists()


def test_a_failed_download_leaves_no_empty_file_and_keeps_a_written_one(tmp_path: Path) -> None:
    empty: Path = unique_download_path(tmp_path, "a.zip")
    _drop_empty(empty)
    assert not empty.exists()
    written: Path = unique_download_path(tmp_path, "b.zip")
    written.write_bytes(b"part")
    _drop_empty(written)
    assert written.exists()


def _info(target: str, url: str, attached: bool, context: str) -> dict[str, object]:
    return {"targetId": target, "type": "page", "url": url, "attached": attached, "browserContextId": context}


class _Cdp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send(self, method: str, params: object = None) -> dict[str, object]:
        self.sent.append((method, params))
        if method == "Target.getTargets":
            return {
                "targetInfos": [_info("T-ours", "about:blank", False, "C-other"), _info("T-1", "https://a", True, "C")]
            }
        return {"targetId": "T-ours"} if method == "Target.createTarget" else {}

    async def detach(self) -> None:
        return None


class _Context:
    """A context whose browser makes the tab ``page``, reported at once unless ``report`` is false."""

    def __init__(self, page: object, report: bool = True) -> None:
        self.cdp: _Cdp = _Cdp()
        self.page: object = page
        self.report: bool = report
        self.listeners: list[Any] = []
        self.browser: object = SimpleNamespace(new_browser_cdp_session=self._session)

    async def _session(self) -> _Cdp:
        return self.cdp

    def on(self, event: str, listener: Any) -> None:
        self.listeners.append(listener)
        if self.report:
            listener(self.page)

    def remove_listener(self, event: str, listener: Any) -> None:
        self.listeners.remove(listener)


def _tab_session(mode: str = "launch", timeout_s: float = 0.2) -> BrowserSession:
    browser: BrowserSession = object.__new__(BrowserSession)
    cast(Any, browser)._config = SimpleNamespace(mode=mode, action_timeout_s=timeout_s, appearance="dark")
    return browser


def _closed(context: _Context) -> bool:
    return ("Target.closeTarget", {"targetId": "T-ours"}) in context.cdp.sent


class _Blank:
    url: str = ""
    closed: bool = False

    def __init__(self, goto_fails: bool = False) -> None:
        self.goto_fails: bool = goto_fails
        self.target: str = "T-ours"

    def is_closed(self) -> bool:
        return self.closed

    async def goto(self, url: str, wait_until: str) -> None:
        if self.goto_fails:
            raise PlaywrightError("Timeout 8000ms exceeded")

    async def emulate_media(self, color_scheme: str) -> None:
        raise PlaywrightError("Target closed")

    async def close(self) -> None:
        self.closed = True


async def test_a_new_tab_that_cannot_be_made_blank_is_closed(ids: None) -> None:
    context: _Context = _Context(_Blank(goto_fails=True))
    with pytest.raises(PlaywrightError):
        await _tab_session()._background_tab(cast(Any, context))
    assert _closed(context) and context.listeners == []


async def test_a_new_tab_chrome_does_not_hand_over_is_closed_and_what_chrome_made_of_it_is_told(ids: None) -> None:
    context: _Context = _Context(_Blank(), report=False)
    with pytest.raises(BrowserError, match="opening a tab failed") as failed:
        await _tab_session()._background_tab(cast(Any, context))
    assert _closed(context) and context.listeners == []
    assert failed.value.context["chrome"] == (
        "Chrome lists the tab at 'about:blank', not attached, in a browser context no other tab is in"
    )


def test_what_chrome_lists_of_a_tab_it_did_not_hand_over() -> None:
    """Item 1002: the tab's address, whether Playwright attached to it and whether it is in the other tabs' context."""
    mark: dict[str, object] = _info("T-ours", _MARK, True, "C")
    others: list[object] = [_info("T-1", "https://a", True, "C"), {"targetId": "W", "type": "service_worker"}]
    assert pages.unhanded_tab("T-ours", {"targetInfos": [mark, *others]}) == (
        f"Chrome lists the tab at '{_MARK}', attached, in the browser context of the other tabs"
    )
    assert pages.unhanded_tab("T-ours", {"targetInfos": [mark]}).endswith(
        "in no other tab to compare its browser context with"
    )
    assert pages.unhanded_tab("T-ours", {"targetInfos": others}) == (
        "Chrome lists no such tab any more; it lists 1 other tabs"
    )
    assert pages.unhanded_tab("T-ours", "nope") == "Chrome lists no such tab any more; it lists 0 other tabs"


async def test_a_run_stopped_while_its_new_tab_comes_closes_the_tab(ids: None) -> None:
    context: _Context = _Context(_Blank(), report=False)
    # The wait's own time is far off, so a slow runner cannot end it before the stop does.
    making: asyncio.Task[Page] = asyncio.create_task(_tab_session(timeout_s=30.0)._background_tab(cast(Any, context)))
    await asyncio.sleep(0.05)
    making.cancel()
    with pytest.raises(asyncio.CancelledError):
        await making
    assert _closed(context) and context.listeners == []


async def test_a_new_tab_in_the_owners_chrome_that_cannot_be_dressed_is_closed() -> None:
    page: _Blank = _Blank()
    with pytest.raises(PlaywrightError):
        await _tab_session("attach")._dressed(cast(Any, page))
    assert page.closed


async def test_a_new_tab_that_is_handed_over_is_returned_and_stays_open(ids: None) -> None:
    page: _Blank = _Blank()
    context: _Context = _Context(page)
    assert await _tab_session()._background_tab(cast(Any, context)) is cast(Any, page)
    assert not _closed(context) and context.listeners == []
