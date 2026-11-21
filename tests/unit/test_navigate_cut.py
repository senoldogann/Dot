"""``navigate`` when Chrome cuts a navigation off (``net::ERR_ABORTED``), on a stand-in tab (item 1296): a tab that
stands on a real page afterwards is a success that names the page, one that stands on none gets one more try, and the
ordinary failures are retried and raised as before."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.driver import Navigation, navigate
from dotagent.core.errors import BrowserError

_ABORT = "Page.goto: net::ERR_ABORTED at http://a.example/start"


class _Page:
    """A tab whose ``goto`` plays a script: each entry is what that call does (``Exception`` raised, or a status), and
    ``commits`` is the address the tab moves to during that call, if any."""

    def __init__(self, url: str, script: list[object], commits: dict[int, str] | None = None) -> None:
        self.url = url
        self.script = script
        self.commits = commits or {}
        self.calls = 0
        self.listeners: list[Callable[[Any], None]] = []

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        assert event == "framenavigated"
        self.listeners.append(handler)

    def remove_listener(self, event: str, handler: Callable[[Any], None]) -> None:
        self.listeners.remove(handler)

    async def goto(self, url: str, **_: object) -> object:
        step: object = self.script[self.calls]
        if self.calls in self.commits:
            self.url = self.commits[self.calls]
            for handler in list(self.listeners):
                handler(SimpleNamespace(parent_frame=None, url=self.url))
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(status=step)

    async def wait_for_load_state(self, state: str, **_: object) -> None:
        return None


async def _go(page: _Page, attempts: int = 3) -> Navigation:
    return await navigate(cast(Any, page), "http://a.example/start", 1000, attempts, 1)


async def test_a_cut_navigation_with_the_tab_on_a_real_page_is_a_success_that_names_it() -> None:
    page = _Page("https://old.example/", [PlaywrightError(_ABORT)], {0: "https://www.a.example/start"})

    result = await _go(page)

    assert result == Navigation(None, "https://www.a.example/start")
    assert page.calls == 1
    assert page.listeners == []


async def test_a_cut_navigation_that_lands_where_the_tab_already_was_is_still_a_page() -> None:
    """A redirect back to the page the tab was on commits a page, though its address is the one it had."""
    page = _Page("https://a.example/start", [PlaywrightError(_ABORT)], {0: "https://a.example/start"})

    assert (await _go(page)).redirected_to == "https://a.example/start"


async def test_a_cut_navigation_with_the_tab_on_no_page_is_tried_once_more_and_read_when_it_answers() -> None:
    page = _Page("about:blank", [PlaywrightError(_ABORT), 200])

    result = await _go(page, attempts=1)

    assert result == Navigation(200)
    assert page.calls == 2


async def test_a_tab_on_chrome_error_page_after_a_cut_is_no_page() -> None:
    page = _Page("https://old.example/", [PlaywrightError(_ABORT), 200], {0: "chrome-error://chromewebdata/"})

    assert await _go(page) == Navigation(200)
    assert page.calls == 2


async def test_two_cuts_with_no_page_in_between_raise_with_what_was_asked() -> None:
    page = _Page("about:blank", [PlaywrightError(_ABORT), PlaywrightError(_ABORT)])

    with pytest.raises(BrowserError) as failed:
        await _go(page)

    assert page.calls == 2
    assert "ERR_ABORTED" in str(failed.value.context)


async def test_a_page_the_tab_was_on_before_a_cut_that_commits_nothing_is_not_read_as_the_redirect() -> None:
    page = _Page("https://old.example/page", [PlaywrightError(_ABORT), 200])

    assert await _go(page) == Navigation(200)
    assert page.calls == 2


async def test_an_ordinary_transient_failure_is_retried_as_before_and_a_hard_one_raises() -> None:
    reset = PlaywrightError("Page.goto: net::ERR_CONNECTION_RESET at http://a.example/")
    page = _Page("about:blank", [reset, 200])
    assert await _go(page) == Navigation(200)
    assert page.calls == 2

    refused = _Page("about:blank", [PlaywrightError("Page.goto: net::ERR_NAME_NOT_RESOLVED at http://a.example/")])
    with pytest.raises(BrowserError):
        await _go(refused)
    assert refused.calls == 1


async def test_a_download_is_no_page_and_no_failure() -> None:
    page = _Page("about:blank", [PlaywrightError("Page.goto: Download is starting")])

    assert await _go(page) == Navigation(None)
