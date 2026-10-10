"""What a model reads when its tool arguments are refused, on the real argument model of the browser batch tool."""

import pytest
from pydantic import ValidationError

from dotagent.core.types import JsonObject
from dotagent.tools.base import describe_validation_error
from dotagent.tools.browser import ActArgs
from dotagent.tools.schema import tool_parameters_schema

_STEP_FIELDS: str = "Valid fields of steps[]: action, ref, to_ref, text, key, x, y."
_TOOL_PARAMETERS: str = "Valid parameters: steps, accept_dialogs, expect."


def refused(arguments: JsonObject) -> str:
    with pytest.raises(ValidationError) as caught:
        ActArgs.model_validate(arguments)
    return describe_validation_error("browser_act", caught.value, tool_parameters_schema(ActArgs))


def test_a_wrong_name_inside_a_step_is_answered_with_the_fields_of_a_step() -> None:
    message: str = refused({"steps": [{"action": "select", "ref": "e7", "value": "blue"}]})
    assert "steps.0.value: Extra inputs are not permitted" in message
    assert _STEP_FIELDS in message
    assert "Valid parameters" not in message
    assert message.endswith("Nothing was run: send the whole call again, corrected.")


def test_a_step_that_uses_its_action_as_a_key_is_told_that_action_is_a_field() -> None:
    message: str = refused({"steps": [{"fill": {"ref": "e5"}}]})
    assert "steps.0.action: Field required" in message
    assert _STEP_FIELDS in message


def test_a_check_of_a_whole_step_lists_the_fields_of_a_step() -> None:
    message: str = refused({"steps": [{"action": "press", "ref": "e10"}]})
    assert "press needs a key" in message
    assert _STEP_FIELDS in message


def test_a_wrong_top_level_name_lists_the_parameters_of_the_tool() -> None:
    message: str = refused({"steps": [{"action": "click", "ref": "e9"}], "dialogs": True})
    assert "dialogs: Extra inputs are not permitted" in message
    assert _TOOL_PARAMETERS in message
    assert "Valid fields of" not in message


def test_errors_at_both_levels_list_the_fields_of_both() -> None:
    message: str = refused({"steps": [{"action": "click", "ref": "e9", "value": "x"}], "dialogs": True})
    assert _TOOL_PARAMETERS in message
    assert _STEP_FIELDS in message


def test_a_drag_without_a_target_is_told_it_needs_to_ref() -> None:
    message: str = refused({"steps": [{"action": "drag", "ref": "e4"}]})
    assert "drag needs a to_ref" in message
    assert _STEP_FIELDS in message


def test_a_target_on_a_step_that_is_not_a_drag_is_refused() -> None:
    message: str = refused({"steps": [{"action": "click", "ref": "e4", "to_ref": "e9"}]})
    assert "click does not use to_ref" in message


def test_insert_takes_a_ref_and_text_and_drag_a_ref_and_a_target() -> None:
    assert "insert needs text" in refused({"steps": [{"action": "insert", "ref": "e4"}]})
    assert "insert needs a ref" in refused({"steps": [{"action": "insert", "text": "long text"}]})
    for action in ("insert", "type"):
        assert f"{action} needs text that is not empty" in refused(
            {"steps": [{"action": action, "ref": "e4", "text": ""}]}
        )
    accepted: ActArgs = ActArgs.model_validate(
        {
            "steps": [
                {"action": "insert", "ref": "e4", "text": "long text"},
                {"action": "drag", "ref": "e4", "to_ref": "e9"},
            ]
        }
    )
    assert [step.action for step in accepted.steps] == ["insert", "drag"]
