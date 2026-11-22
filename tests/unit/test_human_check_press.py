"""The human check's press, without a browser (item 1295): a page that cannot be read is never a page that passed, and
a press goes only to a pixel that is the check's own.

The page and its frames are stand-ins that answer the probe from a script of readings, so that what is measured is
what ``pass_human_check`` makes of them: what it presses, where, and what it tells the run.
"""

import time
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser import human_check
from dotagent.browser.human_check import _PROBE, _UNDER, _unreadable, find_checks, pass_human_check
from dotagent.core.errors import ToolExecutionError

_CHECKBOX = {"kind": "human_check", "role": "checkbox", "box": [100, 100, 30, 30], "text": "I'm not a robot"}


def _reading(*findings: dict[str, object]) -> dict[str, object]:
    return {"vw": 1280, "vh": 800, "findings": list(findings)}


def _moved(left: float) -> dict[str, object]:
    return _reading({**_CHECKBOX, "box": [left, 100, 30, 30]})


class _Frame:
    """A frame that answers the probe from a script: a reading, or an error to raise. The last one repeats."""

    def __init__(
        self, script: list[object], url: str = "https://site.example/", parent: "_Frame | None" = None
    ) -> None:
        self.script = script
        self.url = url
        self.parent_frame = parent
        self.under = ""
        self.probes = 0

    async def evaluate(self, script: str, arg: object = None) -> object:
        if script == _UNDER:
            return self.under
        assert script == _PROBE
        step: object = self.script[min(self.probes, len(self.script) - 1)]
        self.probes += 1
        if isinstance(step, BaseException):
            raise step
        return step

    async def frame_element(self) -> object:
        raise PlaywrightError("Frame was detached")


class _Mouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, float]] = []

    async def click(self, x: float, y: float) -> None:
        self.calls.append(("click", x, y))

    async def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    async def down(self) -> None:
        self.calls.append(("down", 0, 0))

    async def up(self) -> None:
        self.calls.append(("up", 0, 0))


class _Page:
    def __init__(self, main: _Frame, *others: _Frame) -> None:
        self.main_frame = main
        self.frames = [main, *others]
        self.mouse = _Mouse()

    async def screenshot(self, **_: object) -> bytes:
        raise PlaywrightError("no picture in a test")


def _page_of(main: _Frame, *others: _Frame) -> Any:
    return cast(Any, _Page(main, *others))


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(human_check, "_POLL_S", 0.001)
    monkeypatch.setattr(human_check, "_GONE_SETTLED_S", 0.001)


async def test_a_main_frame_that_times_out_is_an_unreadable_page_not_an_empty_one() -> None:
    page = _page_of(_Frame([TimeoutError()]))

    reading = await find_checks(page)

    assert reading.unreadable
    assert reading.checks == ()


async def test_a_captcha_frame_that_cannot_be_read_makes_the_page_unreadable_and_an_ad_frame_does_not() -> None:
    main = _Frame([_reading()])
    widget = _Frame([PlaywrightError("Execution context was destroyed")], url="https://newassets.hcaptcha.com/x")
    ad = _Frame([TimeoutError()], url="https://ads.example/frame")

    assert (await find_checks(_page_of(main, widget))).unreadable
    assert not (await find_checks(_page_of(main, ad))).unreadable


async def test_a_press_after_which_the_page_cannot_be_read_is_unfinished_never_passed() -> None:
    main = _Frame([_reading(_CHECKBOX), _reading(_CHECKBOX), PlaywrightError("Execution context was destroyed")])
    page = _page_of(main)

    outcome = await pass_human_check(page, 0.05)

    assert outcome.result == "unfinished"
    assert outcome.unreadable
    assert "could not read the page" in outcome.note
    assert page.mouse.calls == [("click", 115.0, 115.0)]


def test_the_note_of_an_unreadable_page_after_something_was_done_is_one_sentence_that_reads() -> None:
    """A hold or a press that was done and then could not be followed said "...and the page could not read the page"."""
    outcome = _unreadable(None, time.monotonic(), ("held it 2.0 s",), "the hold was released")

    assert outcome.note == "the hold was released; then the page could not be read, look at it"
    assert "page could not read" not in outcome.note
    assert _unreadable(None, time.monotonic(), ()).note == "could not read the page, look at it"


async def test_a_page_that_is_unreadable_for_a_moment_after_a_press_still_passes() -> None:
    """The legitimate twin: a frame being rebuilt answers late once, and the next read shows the check gone."""
    main = _Frame([_reading(_CHECKBOX), _reading(_CHECKBOX), TimeoutError(), _reading()])

    outcome = await pass_human_check(_page_of(main), 5.0)

    assert outcome.result == "passed", outcome
    assert not outcome.unreadable
    assert outcome.note == "it cleared"


async def test_a_press_after_which_the_check_is_gone_on_a_whole_read_passes_as_before() -> None:
    main = _Frame([_reading(_CHECKBOX), _reading(_CHECKBOX), _reading()])

    outcome = await pass_human_check(_page_of(main), 5.0)

    assert outcome.result == "passed", outcome


async def test_a_page_that_cannot_be_read_at_all_is_not_told_to_have_no_check() -> None:
    outcome = await pass_human_check(_page_of(_Frame([TimeoutError()])), 2.0)

    assert outcome.result == "unfinished"
    assert outcome.unreadable
    assert outcome.did == ()


async def test_a_page_with_no_check_still_says_none() -> None:
    outcome = await pass_human_check(_page_of(_Frame([_reading()])), 2.0)

    assert outcome.result == "none"


async def test_a_press_goes_to_where_the_check_stopped_and_the_guard_weighs_that_pixel() -> None:
    """The check slid 200 pixels between the read and the press and then stood still: it is pressed there."""
    main = _Frame([_reading(_CHECKBOX), _moved(300), _moved(300), _reading()])
    page = _page_of(main)
    weighed: list[tuple[float, float]] = []

    async def guard(x: float, y: float) -> None:
        weighed.append((x, y))

    outcome = await pass_human_check(page, 5.0, guard)

    assert outcome.result == "passed", outcome
    assert page.mouse.calls == [("click", 315.0, 115.0)]
    assert weighed == [(315.0, 115.0)]


async def test_a_check_that_never_stops_moving_is_not_pressed() -> None:
    main = _Frame([_reading(_CHECKBOX)] + [_moved(100 + 40 * step) for step in range(1, 12)])
    page = _page_of(main)

    outcome = await pass_human_check(page, 5.0)

    assert outcome.result == "unfinished"
    assert outcome.did == ()
    assert "kept moving" in outcome.note
    assert page.mouse.calls == []


async def test_a_pixel_with_another_element_over_it_is_not_pressed_and_is_not_weighed() -> None:
    main = _Frame([_reading(_CHECKBOX)])
    main.under = "div#cookiewall We use cookies"
    page = _page_of(main)
    weighed: list[tuple[float, float]] = []

    async def guard(x: float, y: float) -> None:
        weighed.append((x, y))

    outcome = await pass_human_check(page, 5.0, guard)

    assert outcome.result == "unfinished"
    assert outcome.did == ()
    assert "cookiewall" in outcome.note
    assert page.mouse.calls == []
    assert weighed == []


async def test_a_check_gone_between_the_read_and_the_press_is_not_pressed_at_its_old_pixel() -> None:
    main = _Frame([_reading(_CHECKBOX), _reading()])
    page = _page_of(main)

    outcome = await pass_human_check(page, 5.0)

    assert outcome.result == "passed"
    assert outcome.did == ()
    assert "before it was pressed" in outcome.note
    assert page.mouse.calls == []


async def test_a_refusal_of_the_guard_stops_the_press() -> None:
    page = _page_of(_Frame([_reading(_CHECKBOX)]))

    async def refuse(x: float, y: float) -> None:
        raise ToolExecutionError("Nothing was pressed: a button that pays is there.", {"x": x, "y": y})

    with pytest.raises(ToolExecutionError):
        await pass_human_check(page, 5.0, refuse)

    assert page.mouse.calls == []


_HOLD = {"kind": "human_check", "role": "hold", "box": [100, 100, 200, 48], "text": "Press & Hold"}


async def test_a_hold_whose_page_stops_answering_is_not_ended_as_a_button_that_went_away() -> None:
    main = _Frame([_reading(_HOLD), _reading(_HOLD), TimeoutError(), TimeoutError(), TimeoutError()])
    page = _page_of(main)

    outcome = await pass_human_check(page, 0.4)

    assert outcome.result == "unfinished"
    assert outcome.unreadable
    assert [call[0] for call in page.mouse.calls][:2] == ["move", "down"]
    assert page.mouse.calls[-1][0] == "up"


async def test_a_hold_whose_button_goes_away_on_a_whole_read_still_passes() -> None:
    main = _Frame([_reading(_HOLD), _reading(_HOLD), TimeoutError(), _reading()])
    page = _page_of(main)

    outcome = await pass_human_check(page, 5.0)

    assert outcome.result == "passed", outcome
    assert outcome.note == "the button went away"
