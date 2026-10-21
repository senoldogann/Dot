"""A page that never answers a count (item 471): a frame whose script holds its main thread. The secret field probe
then hides the field, an action's ref fails with a word of what happened, and the cookie banner is taken as still
there; none of them waits for good."""

import asyncio
from dataclasses import dataclass

import pytest

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
        assert await driver._banner_gone(_Stuck()) is False  # type: ignore[arg-type]
