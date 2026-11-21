"""The human check without a browser: what the probe's reading means, which part of it stands in front, and what the
model is told (item 1288)."""

from io import BytesIO

import pytest
from PIL import Image

from dotagent.browser.human_check import Check, _box_of, _cleared, _finding, _front, _passed
from dotagent.browser.types import CheckBox, HumanCheckOutcome
from dotagent.core.errors import ToolExecutionError
from dotagent.core.images import crop_scaled
from dotagent.tools.browser import render_human_check


def _check(role: str, state: str = "", kind: str = "recaptcha") -> Check:
    return Check(kind=kind, role=role, box=CheckBox(x=10, y=20, width=100, height=50), state=state)


def _picture(width: int, height: int) -> bytes:
    room: BytesIO = BytesIO()
    Image.new("RGB", (width, height), (0, 0, 0)).save(room, "PNG")
    return room.getvalue()


def test_a_box_out_of_the_probe_is_read_in_floats_and_a_broken_one_is_no_box() -> None:
    assert _box_of([10, 20, 100, 50]) == CheckBox(x=10, y=20, width=100, height=50)
    assert _box_of([10, 20, 0, 50]) is None
    assert _box_of("not a box") is None
    assert _box_of([1, 2]) is None


def test_a_finding_is_put_where_the_frame_sits_in_the_page() -> None:
    found = _finding(
        {"kind": "hcaptcha", "role": "checkbox", "box": [4, 5, 30, 30], "text": "I'm not a robot", "state": "checked"},
        offset_x=400.0,
        offset_y=300.0,
    )
    assert found is not None
    assert found.box == CheckBox(x=404, y=305, width=30, height=30)
    assert found.passed
    assert _finding({"role": "checkbox"}, 0.0, 0.0) is None


def test_what_stands_in_front_is_a_challenge_then_a_button_then_a_box() -> None:
    widget = _check("widget")
    assert _front(()) is None
    assert _front((widget,)) is not None
    assert _front((widget, _check("checkbox"))) is not None
    front = _front((widget, _check("checkbox"), _check("hold"), _check("challenge")))
    assert front is not None and front.role == "challenge"
    front = _front((widget, _check("checkbox"), _check("hold")))
    assert front is not None and front.role == "hold"
    # A box that is already checked is no check to press: it comes back as the one that passed.
    front = _front((widget, _check("checkbox", "checked")))
    assert front is not None and front.passed


def test_a_widget_someone_else_draws_comes_before_a_page_own_words() -> None:
    """A page holding a real hCaptcha and its own "Press & Hold" somewhere: the widget is what stands in front."""
    hold = _check("hold", kind="human_check")
    widget = _check("checkbox", kind="hcaptcha")
    front = _front((hold, widget))

    assert front is widget


def test_a_check_that_passed_is_passed_and_one_with_a_widget_left_is_not_cleared() -> None:
    assert _passed((_check("checkbox", "checked"),))
    assert _passed((_check("token", "set"),))
    assert not _passed((_check("checkbox"),))
    assert _cleared(())
    assert _cleared((_check("verify"),))
    assert not _cleared((_check("widget"),))
    assert not _cleared((_check("checkbox"),))
    assert _cleared((_check("checkbox", "checked"),))


def test_the_model_is_told_what_it_was_what_it_took_and_what_to_do_next() -> None:
    none = HumanCheckOutcome(result="none", kind="", role="", box=None, text="", seconds=0.01)
    assert render_human_check(none).startswith("No human check is in front of the page")
    assert "screenshot" in render_human_check(none)

    passed = HumanCheckOutcome(
        result="passed",
        kind="human_check",
        role="hold",
        box=CheckBox(x=500, y=700, width=200, height=48),
        text="Press & Hold to confirm you are a human",
        seconds=1.6,
        did=("held it 1.5 s at 500,700 200x48",),
        note="the button went away",
    )
    told: str = render_human_check(passed)
    assert "a Press & Hold button at 500,700 200x48" in told
    assert "held it 1.5 s" in told and "1.6 s" in told

    challenge = HumanCheckOutcome(
        result="challenge",
        kind="recaptcha",
        role="challenge",
        box=CheckBox(x=300, y=200, width=300, height=300),
        text="Select all images with traffic lights",
        seconds=0.05,
        cols=3,
        rows=3,
        verify=CheckBox(x=420, y=520, width=96, height=36),
    )
    told = render_human_check(challenge)
    assert "reCAPTCHA at 300,200 300x300 needs eyes, 3 by 3 tiles" in told
    assert "traffic lights" in told
    assert "420,520 96x36" in told
    assert "twice its size" in told

    unfinished = HumanCheckOutcome(
        result="unfinished",
        kind="turnstile",
        role="widget",
        box=CheckBox(x=10, y=10, width=300, height=65),
        text="",
        seconds=8.0,
        did=("pressed the check at 40,42",),
        note="it did not clear",
    )
    told = render_human_check(unfinished)
    assert "Cloudflare Turnstile at 10,10 300x65 did not clear" in told
    assert "pressed the check at 40,42" in told


def test_a_crop_is_the_box_at_twice_its_size_and_clipped_to_the_picture() -> None:
    picture: bytes = _picture(100, 100)
    with Image.open(BytesIO(crop_scaled(picture, 100, 100, (10, 10, 20, 20), 2.0, 80))) as drawn:
        assert drawn.size == (40, 40)

    with Image.open(BytesIO(crop_scaled(picture, 100, 100, (90, 90, 40, 40), 2.0, 80))) as drawn:
        assert drawn.size == (20, 20)

    with pytest.raises(ToolExecutionError):
        crop_scaled(picture, 100, 100, (200, 200, 20, 20), 2.0, 80)
    with pytest.raises(ToolExecutionError):
        crop_scaled(b"not a picture", 100, 100, (0, 0, 2, 2), 2.0, 80)
