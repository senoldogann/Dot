"""What a tool that makes further calls is handed by the executor (item 1501).

``code_run`` is a tool whose code calls other tools. Those calls must be the run's own: the same validation and
classification, the same gate with the owner's rules, the preset, the taint and the approvals, the same masking of every
secret, the same events and the same undo journal as a call the model makes. That path is
:func:`dotagent.agent.executor._execute_one`, which needs the run's registry, its context and its real gate, none of
which a tool's own context holds (its ``gate`` is only ``authorize``, and wrapped so that the tool's own clock stands
still while the owner decides). So the executor, not the tool, makes the object that runs a nested call, and hands it
to the tool in ``ToolContext.nested``: no second path is written, and a tool that is not handed one cannot make a call.

This module is only the shape of that hand-over, so that the tool and the executor need not import each other.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData, ToolSpec

# The tools that nest calls: the executor hands a :class:`NestedCalls` to these and to no other. ``code_run``'s code
# calls the run's tools; ``campaign_achieved`` runs the campaign's proof checks as shell calls (item 1853).
CODE_RUN_TOOL: str = "code_run"
CAMPAIGN_ACHIEVED_TOOL: str = "campaign_achieved"


@dataclass(frozen=True)
class NestedResult:
    """What one nested call came to: the text the tool answered (its error's words when ``is_error``), its pictures,
    whether the run is to count it as content third parties can author (``untrusted``), and whether the call was
    ``refused`` by the run's rules, the owner or the review (``is_error`` is then true as well). ``seconds`` is how long
    the call took in all, ``waited_s`` how much of that it spent waiting for an answer to a question. ``reached_tool``
    is false for a call that never got to its tool (an unknown tool, arguments its tool refused, a refusal)."""

    text: str
    images: tuple[ImageData, ...]
    is_error: bool
    untrusted: bool
    refused: bool
    seconds: float
    waited_s: float
    reached_tool: bool


class NestedCalls(Protocol):
    """How a tool runs one more call of the run's tools as the loop runs the model's."""

    def specs(self) -> Mapping[str, ToolSpec]:
        """The tools of the run that may be called in its mode, by name, with their definitions: the tools the run was
        given. What a tool lets its caller reach of them is its own to decide."""
        ...

    async def call(self, name: str, arguments: JsonObject) -> NestedResult:
        """Make one call, from the run's own step, and answer what it came to. A stop of the run raises, as it does
        out of a call of the model's; every other failure is a result."""
        ...
