"""Reads of a page that replaces its document several times by itself (item 752). A sign-in hops through several
pages (entra.microsoft.com, login.microsoftonline.com, its error page, back): the owner's runs failed ten times in a
month with "measuring the viewport failed ... Execution context was destroyed", whose read had no retry, and twice
where the settle's one retry met the next hop. The page is a stand-in: no browser runs."""

from typing import cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from dotagent.browser import driver
from dotagent.browser.types import Viewport
from dotagent.core.errors import BrowserError

_DESTROYED: str = "Page.evaluate: Execution context was destroyed, most likely because of a navigation"


class _Hopping:
    """A page whose document is replaced under the first ``hops`` reads."""

    def __init__(self, hops: int, answer: object) -> None:
        self.hops: int = hops
        self.answer: object = answer
        self.reads: int = 0
        self.waits: int = 0
        self.url: str = "https://login.example.com/"

    async def evaluate(self, script: str, *args: object) -> object:
        self.reads += 1
        if self.reads <= self.hops:
            raise PlaywrightError(_DESTROYED)
        return self.answer

    async def wait_for_load_state(self, state: str, timeout: float) -> None:  # noqa: ASYNC109 Playwright's signature
        self.waits += 1

    def is_closed(self) -> bool:
        return False


async def test_the_viewport_is_measured_on_the_page_a_chain_of_redirects_lands_on() -> None:
    page: _Hopping = _Hopping(3, [1280, 860])
    assert await driver.measure_viewport(cast(Page, page)) == Viewport(width=1280, height=860)
    assert (page.reads, page.waits) == (4, 3)


async def test_a_page_that_never_stops_replacing_its_document_fails_after_the_tries() -> None:
    page: _Hopping = _Hopping(100, [1280, 860])
    with pytest.raises(BrowserError, match="measuring the viewport failed") as failed:
        await driver.measure_viewport(cast(Page, page))
    assert failed.value.context["attempts"] == "5" and page.reads == 5


async def test_another_failure_is_not_tried_again() -> None:
    class _Broken(_Hopping):
        async def evaluate(self, script: str, *args: object) -> object:
            self.reads += 1
            raise PlaywrightError("Page.evaluate: Target page, context or browser has been closed")

    page: _Broken = _Broken(0, None)
    with pytest.raises(BrowserError):
        await driver.measure_viewport(cast(Page, page))
    assert page.reads == 1
