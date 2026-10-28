"""A page that does not settle after an action that went through (the review of every module, 2026-10-28): the call
says so in the page's notes instead of failing, since a run told only that it failed did it again (a submit, a post)."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.browser.pages import Tab
from dotagent.browser.session import _UNSETTLED, BrowserSession
from dotagent.core.errors import BrowserError, ForbiddenUrlError


def _session(raised: Exception | None) -> BrowserSession:
    session: BrowserSession = object.__new__(BrowserSession)

    async def settle(tab: Tab) -> None:
        if raised is not None:
            raise raised

    cast(Any, session)._settle = settle
    return session


def _tab() -> Tab:
    return cast(Tab, SimpleNamespace(page=SimpleNamespace(url="https://example.com/sent")))


async def test_a_page_that_does_not_settle_after_an_action_is_said_not_raised() -> None:
    assert await _session(None)._settle_acted(_tab()) is None
    said = await _session(BrowserError("waiting for the page to settle failed", {}))._settle_acted(_tab())
    assert said == _UNSETTLED and "do not repeat it" in said


async def test_a_forbidden_address_still_raises() -> None:
    with pytest.raises(ForbiddenUrlError):
        await _session(
            ForbiddenUrlError("the address is not allowed", "https://x.example/", "blocked", "document")
        )._settle_acted(_tab())
