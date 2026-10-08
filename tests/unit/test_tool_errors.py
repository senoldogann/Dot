"""What a model reads when its tool arguments are refused, on the real argument model of the browser batch tool."""

import pytest
from pydantic import ValidationError

from dotagent.core.types import JsonObject
from dotagent.tools.base import describe_validation_error
from dotagent.tools.browser import ActArgs
from dotagent.tools.schema import tool_parameters_schema

_STEP_FIELDS: str = "Valid fields of steps[]: action, ref, text, key, x, y."
_TOOL_PARAMETERS: str = "Valid parameters: steps, accept_dialogs."


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
