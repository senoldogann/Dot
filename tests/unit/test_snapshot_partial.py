"""A page too heavy for a snapshot of the whole gives a snapshot of a part of it, and says so (item 743).

The page is a stand-in: the whole snapshot times out as Playwright's does, the part is read through a locator.
"""

from dataclasses import dataclass, field

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from dotagent.browser.driver import capture_page
from dotagent.browser.snapshot import render_lines
from dotagent.core.errors import BrowserError

_PART: str = '- list [ref=e9] [box=0,0,100,50]:\n  - link "Inbox" [ref=e10] [box=0,0,50,20]\n'
_TIMEOUT: str = "Page.aria_snapshot: Timeout 8000ms exceeded."


@dataclass
class _Part:
    snapshot: str
    asked: list[str]

    async def aria_snapshot(self, mode: str, boxes: bool, **options: float) -> str:
        self.asked.append(f"part {mode} {boxes} {options['timeout']:g}")
        return self.snapshot


@dataclass
class _HeavyPage:
    """A page whose whole snapshot fails as ``whole_error`` says; ``root`` is what the script naming a part gives."""

    root: object
    whole_error: str = _TIMEOUT
    asked: list[str] = field(default_factory=list)
    url: str = "https://slack.example/client"

    async def aria_snapshot(self, mode: str, boxes: bool, **options: float) -> str:
        self.asked.append(f"whole {mode} {boxes} {options['timeout']:g}")
        if "Timeout" in self.whole_error:
            raise PlaywrightTimeoutError(self.whole_error)
        raise PlaywrightError(self.whole_error)

    async def evaluate(self, script: str, budget: int) -> object:
        self.asked.append(f"root {budget}")
        return self.root

    def locator(self, selector: str) -> _Part:
        self.asked.append(f"locator {selector}")
        return _Part(_PART, self.asked)


async def test_a_page_too_heavy_for_the_snapshot_gives_the_part_around_the_middle_and_says_so() -> None:
    root: dict[str, object] = {"selector": "html > body > div:nth-child(2)", "elements": 2400, "tag": "div"}
    page: _HeavyPage = _HeavyPage(root=root)
    read = await capture_page(page, 8000, True)  # type: ignore[arg-type]
    assert render_lines(read.lines) == '- link "Inbox" [ref=e10]'
    assert read.partial.startswith("Partial view: the page was too heavy to read whole in 8 s")
    assert "a <div> of 2400 elements" in read.partial
    assert page.asked == [
        "whole ai True 8000",
        "root 3000",
        "locator html > body > div:nth-child(2)",
        "part ai True 8000",
    ]


@pytest.mark.parametrize("root", [None, {"selector": 3}, "body"])
async def test_a_page_with_no_part_smaller_than_itself_fails_as_it_did(root: object) -> None:
    page: _HeavyPage = _HeavyPage(root=root)
    with pytest.raises(BrowserError, match="taking the page snapshot failed"):
        await capture_page(page, 8000, True)  # type: ignore[arg-type]


async def test_a_failure_that_is_not_a_timeout_is_not_made_a_partial_view() -> None:
    root: dict[str, object] = {"selector": "div", "elements": 5, "tag": "div"}
    page: _HeavyPage = _HeavyPage(root=root, whole_error="Target closed")
    with pytest.raises(BrowserError, match="taking the page snapshot failed"):
        await capture_page(page, 8000, True)  # type: ignore[arg-type]
    assert page.asked == ["whole ai True 8000"]
