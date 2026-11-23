"""wait_until reads the names a model on the Claude plan gives its arguments before it looked the tool up (item 1340):
``condition`` for ``command``, and ``timeout_s`` in seconds for ``within_minutes``. On the owner's Mac each was a call
refused and sent again, six times in two days."""

import pytest
from pydantic import ValidationError

from dotagent.tools.base import tool_parameters_schema
from dotagent.tools.waits import WaitArgs


def test_a_condition_is_the_command_and_timeout_s_the_minutes_it_waits() -> None:
    args: WaitArgs = WaitArgs.model_validate({"condition": "test -f out.zip", "timeout_s": "1500"})
    assert (args.command, args.within_minutes) == ("test -f out.zip", 25.0)


def test_timeout_s_beside_within_minutes_or_past_an_hour_is_refused() -> None:
    with pytest.raises(ValidationError, match="timeout_s"):
        WaitArgs.model_validate({"command": "true", "timeout_s": 60, "within_minutes": 5})
    with pytest.raises(ValidationError, match="within_minutes"):
        WaitArgs.model_validate({"command": "true", "timeout_s": 7200})
    with pytest.raises(ValidationError, match="timeout_s"):
        WaitArgs.model_validate({"command": "true", "timeout_s": "soon"})


def test_the_schema_offers_only_the_tools_own_names() -> None:
    properties: object = tool_parameters_schema(WaitArgs)["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"command", "url", "file", "contains", "every_s", "within_minutes"}
