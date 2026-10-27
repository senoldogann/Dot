"""Steps of a batch written as the model meant them, where that is plain (item 755): in the owner's runs of a month,
batches failed whole on a click written with x and y, on a step that only looked (`snapshot`), and on an `upload` step,
each costing a model round trip to write the batch again."""

import pytest
from pydantic import ValidationError

from dotagent.browser.types import Step
from dotagent.tools.browser import ActArgs


def test_a_click_at_a_point_with_no_ref_is_a_click_at() -> None:
    step: Step = Step.model_validate({"action": "click", "x": 1510, "y": 784})
    assert (step.action, step.x, step.y, step.ref) == ("click_at", 1510, 784, None)
    # A click with a ref keeps meaning the element; with a ref and a point it still fails, as it is not plain.
    assert Step.model_validate({"action": "click", "ref": "e3"}).action == "click"
    with pytest.raises(ValidationError, match="does not use x/y"):
        Step.model_validate({"action": "click", "ref": "e3", "x": 1, "y": 2})


def test_a_step_that_only_looks_is_left_out_of_the_batch() -> None:
    args: ActArgs = ActArgs.model_validate({"steps": [{"action": "click", "ref": "f5e31"}, {"action": "snapshot"}]})
    assert [step.action for step in args.steps] == ["click"]
    # A batch of nothing but looks is not made empty: it fails as before, naming the actions.
    with pytest.raises(ValidationError):
        ActArgs.model_validate({"steps": [{"action": "snapshot"}]})


def test_an_upload_step_names_the_tool_that_uploads() -> None:
    with pytest.raises(ValidationError, match="call browser_upload"):
        ActArgs.model_validate({"steps": [{"action": "upload", "ref": "e362", "paths": ["/tmp/cv.pdf"]}]})
