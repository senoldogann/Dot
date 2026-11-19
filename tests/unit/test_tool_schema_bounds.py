"""Tool schemas on the wire: a bound pydantic dropped into a field's own metadata is a keyword every provider knows, and
the Antigravity route keeps a parameter whose name looks like a keyword. The owner's chat met both at once: Gemini
answered "Unknown name \\"gt\\"" and then "required[0]: property is not defined" for a ``title`` argument."""

from typing import Annotated

from pydantic import Field

from dotagent.llm.antigravity import _schema
from dotagent.tools.base import ToolArgs, at_most
from dotagent.tools.schema import tool_parameters_schema


class _Args(ToolArgs):
    due_hours: Annotated[float, at_most(720.0)] | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=120)


def test_a_bound_in_the_fields_own_metadata_is_minimum_for_every_provider() -> None:
    schema = tool_parameters_schema(_Args)
    bound = schema["properties"]["due_hours"]  # type: ignore[index]
    assert "gt" not in bound
    assert bound["minimum"] == 0


def test_the_antigravity_route_keeps_a_parameter_called_title() -> None:
    sent = _schema(tool_parameters_schema(_Args))
    assert isinstance(sent, dict)
    properties = sent["properties"]
    assert "title" in properties
    assert set(sent["required"]) <= set(properties)
