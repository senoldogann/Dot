"""A tab that answers nothing is told apart from a page still loading (item 753). A Slack tab of the owner's that had
stopped answering was reported as a document not yet parsed after 30 s, and every later read of it waited as long
again. The page is a stand-in: no browser runs."""

import asyncio
from typing import cast

import pytest
from playwright.async_api import Page

from dotagent.browser import driver
from dotagent.core.errors import BrowserError


class _Stuck:
    url: str = "https://app.slack.com/client"

    async def evaluate(self, script: str, *args: object) -> object:
        await asyncio.sleep(60)
        return None

    def is_closed(self) -> bool:
        return False


async def test_a_tab_that_answers_nothing_is_told_apart_from_a_page_still_loading() -> None:
    with pytest.raises(BrowserError, match=r"the tab did not answer within 0\.2 s: its page is stuck"):
        await driver.wait_dom_quiet(cast(Page, _Stuck()), 60, 500, 200)


async def test_a_page_busy_for_a_while_still_has_the_whole_time_to_answer() -> None:
    """The code review of item 753: a heavy app starting on a loaded Mac keeps its page busy for seconds, and is no
    stuck tab; it has the whole bound to answer, as before, and the parse what is left of it."""

    class _Busy(_Stuck):
        async def evaluate(self, script: str, *args: object) -> object:
            if script == "1":
                await asyncio.sleep(0.3)
            return True

    await driver.wait_dom_quiet(cast(Page, _Busy()), 60, 500, 2_000)
