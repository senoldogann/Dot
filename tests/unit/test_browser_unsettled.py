"""A page that does not settle after an action that went through (the review of every module, 2026-10-28): the call
says so in the page's notes instead of failing, since a run told only that it failed did it again (a submit, a post)."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.browser.pages import Tab
from dotagent.browser.session import _UNSETTLED, BrowserSession, _read_after, _steps_said
from dotagent.browser.types import PageView, StepReport
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


def _reports(*outcomes: str) -> list[StepReport]:
    return [
        StepReport(index=index, description=f"click e{index}", outcome=cast(Any, outcome), detail="")
        for index, outcome in enumerate(outcomes, start=1)
    ]


async def _failing_read() -> PageView:
    raise BrowserError("taking the page snapshot failed", {"url": "https://example.com/sent"})


async def test_a_read_that_fails_after_steps_went_through_says_which_did() -> None:
    with pytest.raises(BrowserError) as raised:
        await _read_after(_failing_read(), _steps_said(_reports("done", "failed", "skipped")))
    message: str = raised.value.message
    assert "step 1 click e1" in message and "e2" not in message
    assert "do not repeat what went through" in message and "taking the page snapshot failed" in message
    assert raised.value.context == {"url": "https://example.com/sent"}


async def test_a_read_that_fails_after_nothing_went_through_is_raised_as_it_is() -> None:
    assert _steps_said(_reports("failed", "skipped")) is None
    with pytest.raises(BrowserError) as raised:
        await _read_after(_failing_read(), None)
    assert raised.value.message == "taking the page snapshot failed"


async def test_a_forbidden_address_after_the_steps_is_raised_as_it_is() -> None:
    async def forbidden() -> PageView:
        raise ForbiddenUrlError("the address is not allowed", "https://x.example/", "blocked", "document")

    with pytest.raises(ForbiddenUrlError):
        await _read_after(forbidden(), "these steps went through: step 1 click e1")


def test_a_long_batch_names_its_first_steps_and_counts_the_rest() -> None:
    said: str | None = _steps_said(_reports(*["done"] * 13))
    assert said is not None and "step 10 click e10" in said and "e11" not in said and said.endswith("and 3 more")
