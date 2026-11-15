"""Steps of a batch written as the model meant them, where that is plain (item 755): in the owner's runs of a month,
batches failed whole on a click written with x and y, on a step that only looked (`snapshot`), and on an `upload` step,
each costing a model round trip to write the batch again."""

import pytest
from pydantic import ValidationError

from dotagent.browser.render import describe_step
from dotagent.browser.types import Step
from dotagent.tools.browser import ActArgs
from dotagent.tools.desktop import ActArgs as DesktopActArgs


def test_a_click_at_a_point_with_no_ref_is_a_click_at() -> None:
    step: Step = Step.model_validate({"action": "click", "x": 1510, "y": 784})
    assert (step.action, step.x, step.y, step.ref) == ("click_at", 1510, 784, None)
    # A click with a ref keeps meaning the element; with a ref and a point it still fails, as it is not plain.
    assert Step.model_validate({"action": "click", "ref": "e3"}).action == "click"
    with pytest.raises(ValidationError, match="does not use x/y"):
        Step.model_validate({"action": "click", "ref": "e3", "x": 1, "y": 2})


def test_a_drag_from_a_point_to_a_point_is_a_drag_at() -> None:
    step: Step = Step(action="drag_at", x=38, y=38, to_x=220, to_y=120)
    assert (step.action, step.to_x, step.to_y) == ("drag_at", 220, 120)
    assert describe_step(step) == "drag_at (38, 38) to (220, 120)"
    with pytest.raises(ValidationError, match="drag_at needs"):
        Step(action="drag_at", x=38, y=38)
    with pytest.raises(ValidationError, match="does not use to_x/to_y"):
        Step(action="click_at", x=38, y=38, to_x=220)


def test_a_step_that_only_looks_is_left_out_of_the_batch() -> None:
    args: ActArgs = ActArgs.model_validate({"steps": [{"action": "click", "ref": "f5e31"}, {"action": "snapshot"}]})
    assert [step.action for step in args.steps] == ["click"]
    # A batch of nothing but looks is not made empty: it fails as before, naming the actions.
    with pytest.raises(ValidationError):
        ActArgs.model_validate({"steps": [{"action": "snapshot"}]})


def test_a_bare_wait_is_left_out_of_the_batch_as_a_look_is() -> None:
    # The batch already waits for the page to settle after its steps (run_mvsnlh3f wrote one after a click).
    args: ActArgs = ActArgs.model_validate({"steps": [{"action": "click", "ref": "f1e133"}, {"action": "wait"}]})
    assert [step.action for step in args.steps] == ["click"]


def test_text_with_no_field_says_where_the_ref_of_the_field_comes_from() -> None:
    # Item 1133: runs clicked a field by its pixel and typed with no ref; the refusal named no way to the ref, and a
    # run filled the form with a script instead, past the checks a step of a batch has at each field.
    for action in ("type", "insert", "fill"):
        with pytest.raises(ValidationError) as refused:
            ActArgs.model_validate(
                {"steps": [{"action": "click_at", "x": 643, "y": 402}, {"action": action, "text": "Ada"}]}
            )
        message: str = str(refused.value)
        assert f"{action} needs a ref" in message
        assert "browser_find" in message
        assert "click_at" in message
    # A step that writes no text is told only that it needs a ref.
    with pytest.raises(ValidationError) as clicked:
        ActArgs.model_validate({"steps": [{"action": "hover"}]})
    assert "browser_find" not in str(clicked.value)


def test_an_upload_step_names_the_tool_that_uploads() -> None:
    with pytest.raises(ValidationError, match="call browser_upload"):
        ActArgs.model_validate({"steps": [{"action": "upload", "ref": "e362", "paths": ["/tmp/cv.pdf"]}]})


def test_an_expectation_beside_the_steps_is_the_last_steps() -> None:
    """Item 1161: expect_text or expect_url written beside the steps of a call (two speed-set attempts in fifteen did,
    each refused and written again) is the last step's, as the words the page is to show after the batch."""
    args = ActArgs.model_validate(
        {
            "steps": [{"action": "fill", "ref": "e1", "text": "Ada"}, {"action": "click", "ref": "e2"}],
            "expect_text": "Saved",
        }
    )
    assert (args.steps[0].expect_text, args.steps[1].expect_text) == (None, "Saved")
    after_a_look = ActArgs.model_validate(
        {"steps": [{"action": "click", "ref": "e9"}, {"action": "snapshot"}], "expect_text": "Thank you"}
    )
    assert [(step.action, step.expect_text) for step in after_a_look.steps] == [("click", "Thank you")]
    to_url = ActArgs.model_validate({"steps": [{"action": "click", "ref": "e2"}], "expect_url": "/done"})
    assert to_url.steps[0].expect_url == "/done"
    # The last step's own is not merged with another: the call is refused for the field it does not take, as before.
    with pytest.raises(ValidationError, match="expect_text"):
        ActArgs.model_validate(
            {"steps": [{"action": "click", "ref": "e2", "expect_text": "Saved"}], "expect_text": "Thank you"}
        )


def test_a_desktop_expectation_beside_the_steps_is_the_last_steps() -> None:
    args = DesktopActArgs.model_validate({"steps": [{"action": "press", "ref": "a7"}], "expect_text": "Saved to notes"})
    assert args.steps[0].expect_text == "Saved to notes"
    # A desktop step takes no address: the call is refused for its own field, as before.
    with pytest.raises(ValidationError, match="expect_url"):
        DesktopActArgs.model_validate({"steps": [{"action": "press", "ref": "a7"}], "expect_url": "/done"})
