"""A page that never answers a count (item 471): a frame whose script holds its main thread. The secret field probe
then hides the field, an action's ref fails with a word of what happened, and the cookie banner is taken as still
there; none of them waits for good."""

import asyncio
import time
from dataclasses import dataclass

import pytest
from structlog.testing import capture_logs

from dotagent.browser import driver
from dotagent.core.errors import BrowserError


@dataclass
class _Stuck:
    """A locator of a page that never answers."""

    async def count(self) -> int:
        await asyncio.Event().wait()
        return 0

    async def evaluate(self, expression: str, **options: float) -> object:
        await asyncio.Event().wait()
        return None


@dataclass
class _Page:
    url: str = "https://example.com/stuck"

    def locator(self, selector: str) -> _Stuck:
        return _Stuck()


async def test_a_page_that_never_answers_a_count_holds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "_COUNT_TIMEOUT_S", 0.05)
    async with asyncio.timeout(5):
        assert await driver._is_secret(_Page(), "e5") is True  # type: ignore[arg-type]
        with pytest.raises(BrowserError, match="did not answer in time"):
            await driver.live_locator(_Page(), "e5", "click")  # type: ignore[arg-type]
        assert await driver._banner_uncounted(_Stuck()) == "the page did not answer the count in time"  # type: ignore[arg-type]
    # The field the probe could not ask about says so in the log, with why.
    with capture_logs() as logs:
        await driver._is_secret(_Page(), "e7")  # type: ignore[arg-type]
    assert {
        "event": "browser_field_unreadable",
        "ref": "e7",
        "error": "the page did not answer in time",
    }.items() <= logs[0].items(), logs


class _Frame:
    """The frame a slow hand-over returns."""


class _Handle:
    def __init__(self, frame: _Frame, delay: float) -> None:
        self._frame: _Frame = frame
        self._delay: float = delay

    async def owner_frame(self) -> _Frame:
        await asyncio.sleep(self._delay)
        return self._frame


class _SlowLocator:
    """A page that answers a count at once and is slow to hand over the element and its frame."""

    def __init__(self, delay: float) -> None:
        self.delay: float = delay
        self.frame: _Frame = _Frame()

    async def count(self) -> int:
        return 1

    async def element_handle(self, **_options: object) -> _Handle:
        await asyncio.sleep(self.delay)
        return _Handle(self.frame, self.delay)


class _SlowPage:
    url: str = "http://127.0.0.1/stubborn"

    def __init__(self, delay: float) -> None:
        self._locator: _SlowLocator = _SlowLocator(delay)

    def locator(self, selector: str) -> _SlowLocator:
        return self._locator


class _HungAfterCount:
    """A page that answers the count and then never hands the element over."""

    async def count(self) -> int:
        return 1

    async def element_handle(self, **_options: object) -> None:
        await asyncio.Event().wait()


class _HungPage:
    url: str = "http://127.0.0.1/stubborn"

    def locator(self, selector: str) -> _HungAfterCount:
        return _HungAfterCount()


async def test_a_page_that_never_answers_still_fails_a_script_lookup_within_the_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page a script holds still fails the lookup on the count, not after the longer hand-over."""
    monkeypatch.setattr(driver, "_COUNT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(driver, "_ELEMENT_TIMEOUT_S", 5.0)
    async with asyncio.timeout(1):
        with pytest.raises(BrowserError, match="did not answer in time"):
            await driver._elements_of(_Page(), ("e4",))  # type: ignore[arg-type]


async def test_a_page_that_answered_is_not_cancelled_while_it_hands_over_an_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each hand-over has its own bound. One clock for the element and its frame cancelled the second call once the
    first had used the count's two seconds, which a loaded runner did to a page that was not stuck."""
    monkeypatch.setattr(driver, "_COUNT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(driver, "_ELEMENT_TIMEOUT_S", 0.2)
    page = _SlowPage(0.12)
    frame, handles = await driver._elements_of(page, ("e1", "e2"))  # type: ignore[arg-type]
    assert len(handles) == 2 and frame is page._locator.frame


async def test_a_handoff_that_never_comes_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "_ELEMENT_TIMEOUT_S", 0.05)
    started: float = time.monotonic()
    async with asyncio.timeout(1):
        with pytest.raises(BrowserError, match="did not answer in time") as raised:
            await driver._elements_of(_HungPage(), ("e3",))  # type: ignore[arg-type]
    assert raised.value.context["waited_s"] == 0.05
    assert time.monotonic() - started < 1
