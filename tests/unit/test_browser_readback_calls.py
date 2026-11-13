"""How many times a step that wrote into a field asks its page (item 1151): once before it and once after it, where
each used to be two or three. A stand-in plays the element, so each call is counted and no browser runs."""

import asyncio
import time
from typing import cast

import pytest
from playwright.async_api import Locator

from dotagent.browser import driver
from dotagent.browser.driver import FieldReading
from dotagent.core.errors import StepNotSent

_HELD: dict[str, str] = {"kind": "value", "value": "Ada", "label": ""}


class _Page:
    url: str = "http://127.0.0.1:1/form"


class _Element:
    """An element that answers each question with the next of its answers, and keeps what it was asked."""

    def __init__(self, answers: list[object]) -> None:
        self.page: _Page = _Page()
        self.asked: list[str] = []
        self._answers: list[object] = answers

    async def evaluate(self, script: str, *given: object, **options: object) -> object:
        """Playwright's ``evaluate``, with whatever argument and time it is given."""
        del given, options
        self.asked.append(script)
        return self._answers[len(self.asked) - 1]

    async def is_checked(self, **options: object) -> bool:
        del options
        self.asked.append("is_checked")
        return True


class _Frozen(_Element):
    """An element of a page that never answers: its frame is frozen, or held by work of its own."""

    def __init__(self) -> None:
        super().__init__([])

    async def evaluate(self, script: str, *given: object, **options: object) -> object:
        del given, options
        self.asked.append(script)
        await asyncio.Event().wait()
        return None


def _locator(element: _Element) -> Locator:
    return cast(Locator, element)


async def test_the_marking_and_what_the_field_holds_come_in_one_answer() -> None:
    element: _Element = _Element([{"marking": "email", "reading": _HELD}])
    marking, held = await driver._marked_reading(_locator(element), 8000)
    assert (marking, held) == ("email", FieldReading(kind="value", value="Ada", label=""))
    assert element.asked == [driver._MARKED_READ_SCRIPT]


async def test_a_reading_that_failed_leaves_the_marking_the_secret_check_reads() -> None:
    # The script answers null for a reading that threw: the field's marking is still what the page said of it.
    element: _Element = _Element([{"marking": "password current-password", "reading": None}])
    marking, held = await driver._marked_reading(_locator(element), 8000)
    assert marking == "password current-password" and held.kind == "unreadable"
    # A field that is no form field has no marking, which is an answer; an answer without one is none to go by.
    plain: _Element = _Element([{"marking": None, "reading": {"kind": "none"}}])
    assert await driver._marked_reading(_locator(plain), 8000) == (None, FieldReading(kind="none", value="", label=""))
    with pytest.raises(StepNotSent, match="could not be read before the step"):
        await driver._marked_reading(_locator(_Element([None])), 8000)


async def test_a_page_that_never_answers_ends_the_readback_at_the_steps_time() -> None:
    """The review of item 1151: Playwright's own time on an ``evaluate`` is for finding the element, so a script that
    waits for a turn the page never takes waited without end. Each question has the step's time and no more."""
    started: float = time.monotonic()
    before: FieldReading = FieldReading(kind="value", value="Ada", label="")
    assert (await driver._read_after(_locator(_Frozen()), before, 50, driver._never)).kind == "unreadable"
    box = await driver._ticked(_locator(_Frozen()), True, 50)
    assert [check.status for check in box.checks] == ["uncertain"]
    assert (await driver._read_field(_locator(_Frozen()))).kind == "unreadable"
    # Before the step nothing went to the page: the step is refused, not left waiting.
    with pytest.raises(StepNotSent, match="did not answer in time"):
        await driver._marked_reading(_locator(_Frozen()), 50)
    assert time.monotonic() - started < 5.0


async def test_the_turn_and_the_reading_after_a_step_are_one_question_when_the_field_changed() -> None:
    element: _Element = _Element([{"kind": "value", "value": "Grace", "label": ""}])
    before: FieldReading = FieldReading(kind="value", value="Ada", label="")
    after: FieldReading = await driver._read_after(_locator(element), before, 8000, driver._never)
    assert after.value == "Grace"
    assert element.asked == [driver._TURN_READ_SCRIPT]


async def test_a_field_that_holds_what_was_asked_is_not_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(driver.asyncio, "sleep", no_wait)
    before: FieldReading = FieldReading(kind="value", value="Ada", label="")
    held: _Element = _Element([_HELD])
    await driver._read_after(_locator(held), before, 8000, lambda reading: reading.value == "Ada")
    assert len(held.asked) == 1
    # One that is as it was and does not hold it is read again, until its short wait is over.
    stuck: _Element = _Element([_HELD] * 20)
    await driver._read_after(_locator(stuck), before, 8000, driver._never)
    assert len(stuck.asked) > 1 and stuck.asked[1] == driver._FIELD_READ_SCRIPT


async def test_a_box_is_read_with_its_turn_and_playwright_is_asked_only_for_one_the_script_does_not_know() -> None:
    ticked: _Element = _Element([True])
    result = await driver._ticked(_locator(ticked), True, 8000)
    assert [check.status for check in result.checks] == ["verified"]
    assert ticked.asked == [driver._TURN_TICKED_SCRIPT]
    cleared: _Element = _Element([False])
    assert [check.status for check in (await driver._ticked(_locator(cleared), True, 8000)).checks] == ["contradicted"]
    unknown: _Element = _Element([None])
    await driver._ticked(_locator(unknown), True, 8000)
    assert unknown.asked == [driver._TURN_TICKED_SCRIPT, "is_checked"]
