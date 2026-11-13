"""What a desktop step may expect to see, and which steps wait for a look (item 1146): the rules of the browser's
steps (item 1145), on the desktop's own actions."""

import pytest
from pydantic import ValidationError

from dotagent.desktop.types import Step
from dotagent.tools.desktop import ActArgs


def test_a_desktop_step_takes_exact_words_to_expect_and_none_the_batch_writes() -> None:
    step: Step = Step.model_validate({"action": "press", "ref": "a7", "expect_text": "Saved to notes.md"})
    assert step.expect_text == "Saved to notes.md"
    with pytest.raises(ValidationError, match="a step takes expect_text"):
        Step.model_validate({"action": "press", "ref": "a7", "expect": "it is saved"})
    # A view of a window shows 80 characters of a value: longer words could never be found.
    with pytest.raises(ValidationError):
        Step.model_validate({"action": "press", "ref": "a7", "expect_text": "x" * 81})
    with pytest.raises(ValidationError, match="within a text this batch writes"):
        ActArgs.model_validate(
            {
                "steps": [
                    {"action": "type", "text": "notes.md"},
                    {"action": "press", "ref": "a7", "expect_text": "notes"},
                ]
            }
        )
    # The name of the application an activate step brings forward is not text that is written into a field.
    assert ActArgs.model_validate(
        {
            "steps": [
                {"action": "activate", "text": "Notes"},
                {"action": "press", "ref": "a7", "expect_text": "Notes saved"},
            ]
        }
    )


def test_which_desktop_steps_wait_for_a_look_after_a_check_that_is_not_verified() -> None:
    writing: list[Step] = [
        Step(action="type", text="Ada"),
        Step(action="paste", text="Ada"),
        Step(action="focus", ref="a3"),
        Step(action="scroll", direction="down"),
        # The whole value of a field is set at once: a line break in it presses no key.
        Step(action="set_value", ref="a3", text="line\nbreak"),
    ]
    assert not any(step.acts_on_what_was_left() for step in writing)
    acting: list[Step] = [
        Step(action="press", ref="a7"),
        Step(action="click", ref="a7"),
        Step(action="key", key="return"),
        Step(action="menu", path=["File", "Save"]),
        Step(action="type", text="Ada\t"),
        Step(action="paste", text="Ada\n"),
    ]
    assert all(step.acts_on_what_was_left() for step in acting)
