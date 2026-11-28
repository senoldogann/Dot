"""A tab that answers nothing is told apart from a page still loading (item 753). A Slack tab of the owner's that had
stopped answering was reported as a document not yet parsed after 30 s, and every later read of it waited as long
again. The page is a stand-in: no browser runs."""

import asyncio
import time
from pathlib import Path
from typing import cast

import pytest
from playwright.async_api import Page

from dotagent.browser import driver
from dotagent.browser.pages import Tab
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.config import BrowserConfig, load_default_config
from dotagent.core.errors import BrowserError, TabSilentError


class _Stuck:
    url: str = "https://app.slack.com/client"

    async def evaluate(self, script: str, *args: object) -> object:
        await asyncio.sleep(60)
        return None

    def is_closed(self) -> bool:
        return False


async def test_a_tab_that_answers_nothing_is_told_apart_from_a_page_still_loading() -> None:
    with pytest.raises(TabSilentError, match=r"the tab did not answer within 0\.2 s: its page is stuck"):
        await driver.wait_dom_quiet(cast(Page, _Stuck()), 60, 500, 200)


async def test_a_page_that_answers_and_is_still_parsing_is_no_silent_tab() -> None:
    """Item 954: an open shows a page that answers and is still being parsed, and fails only for one that answers
    nothing, so the two fail with errors of their own kinds."""

    class _Parsing(_Stuck):
        async def evaluate(self, script: str, *args: object) -> object:
            if script == "1":
                return 1
            await asyncio.sleep(60)
            return None

    with pytest.raises(BrowserError, match="the document was not parsed within 200 ms") as raised:
        await driver.wait_dom_quiet(cast(Page, _Parsing()), 60, 500, 200)
    assert not isinstance(raised.value, TabSilentError)


async def test_a_page_busy_for_a_while_still_has_the_whole_time_to_answer() -> None:
    """The code review of item 753: a heavy app starting on a loaded Mac keeps its page busy for seconds, and is no
    stuck tab; it has the whole bound to answer, as before, and the parse what is left of it."""

    class _Busy(_Stuck):
        async def evaluate(self, script: str, *args: object) -> object:
            if script == "1":
                await asyncio.sleep(0.3)
            return True

    await driver.wait_dom_quiet(cast(Page, _Busy()), 60, 500, 2_000)


async def test_a_stuck_tab_with_a_navigation_under_way_is_said_after_one_bound(tmp_path: Path) -> None:
    """Item 2079: a tab whose page answers nothing while a navigation is under way waited a bound for the commit and
    another for the page to answer, so ``browser_snapshot`` was cut by its own 45 s with no word of what was wrong."""
    config = load_default_config()
    browser: BrowserConfig = config.browser.model_copy(update={"navigation_timeout_s": 1.0})
    session = BrowserSession(browser, config.server, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work)
    tab = Tab(page=cast(Page, _Stuck()))
    tab.nav_idle.clear()
    started: float = time.monotonic()
    with pytest.raises(TabSilentError, match=r"the tab did not answer within 1 s"):
        await session._settle(tab)
    assert time.monotonic() - started < 1.6
