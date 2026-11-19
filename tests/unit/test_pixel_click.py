"""A click or a drag at a pixel is judged by the elements of the snapshot under it, as one on its ref is (B2-01 of the
code review of 2026-11-19): before, a ``click_at`` named no element and the policy judged it by nothing, so a run held
back from ``click e3 "Pay now"`` could press the same button by its pixel."""

import pytest

from dotagent.browser.safety import assess_step, parse_element
from dotagent.browser.snapshot import line_for_ref, parse_snapshot, refs_at_point
from dotagent.browser.types import Step
from dotagent.core.errors import ToolInputError
from dotagent.policy.elements import combine_assessments
from dotagent.tools.browser import _pixels_judged_on_the_snapshot

PAGE: str = """- main [ref=e1] [box=0,0,800,600]:
  - form "Checkout" [ref=e2] [box=100,100,400,300]:
    - button "Pay now" [ref=e3] [box=150,300,120,40]
    - button "Place order" [ref=e5] [box=300,300,150,40]:
      - img [ref=e6] [box=310,310,20,20]
  - link "Help" [ref=e7] [box=600,10,40,20]
  - button "Buy now" [ref=e8] [box=580,0,200,100]
  - iframe [ref=e4] [box=0,450,400,100]:
    - button "Send message" [ref=f1e2] [box=10,10,100,30]"""


def _flags_at(x: float, y: float) -> tuple[str, ...]:
    lines = parse_snapshot(PAGE)
    step: Step = Step(action="click_at", x=x, y=y)
    found = [
        assess_step(step, parse_element(line), None)
        for ref in refs_at_point(lines, x, y)
        if (line := line_for_ref(lines, ref)) is not None
    ]
    return combine_assessments([assess_step(step, None, None), *found]).flags


def test_the_smallest_box_under_the_pixel_is_the_element_clicked() -> None:
    lines = parse_snapshot(PAGE)
    assert refs_at_point(lines, 160, 310) == ("e3",)
    assert refs_at_point(lines, 120, 120) == ("e2",)
    assert refs_at_point(lines, 900, 900) == ()


def test_a_part_of_a_button_presses_the_button() -> None:
    assert refs_at_point(parse_snapshot(PAGE), 315, 315) == ("e5",)
    assert "financial" in _flags_at(315, 315)


def test_boxes_that_overlap_are_all_judged() -> None:
    assert set(refs_at_point(parse_snapshot(PAGE), 610, 15)) == {"e7", "e8"}
    assert "financial" in _flags_at(610, 15)


def test_an_iframes_boxes_count_from_where_the_frame_stands() -> None:
    lines = parse_snapshot(PAGE)
    assert refs_at_point(lines, 20, 470) == ("f1e2",)


def test_a_click_at_a_paying_buttons_pixel_is_a_payment() -> None:
    assert "financial" in _flags_at(160, 310)


def test_a_click_where_nothing_is_drawn_is_judged_as_before() -> None:
    assert "financial" not in _flags_at(900, 900)


def test_a_pixel_step_after_one_that_may_move_the_page_is_sent_back() -> None:
    _pixels_judged_on_the_snapshot([Step(action="click_at", x=1, y=1), Step(action="click_at", x=2, y=2)])
    with pytest.raises(ToolInputError):
        _pixels_judged_on_the_snapshot([Step(action="scroll_into_view", ref="e3"), Step(action="click_at", x=1, y=1)])
