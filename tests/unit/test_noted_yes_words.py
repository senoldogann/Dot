"""What the model reads of a step the owner said yes to with a note (item 1140): the step waits for the note, and its
words ask the run to do it again if the note still calls for it, never the "do not retry" of a refusal."""

from dotagent.agent.declined import noted_text
from dotagent.agent.executor import _describe_failure
from dotagent.core.errors import ApprovalNoted, ApprovalRejected
from dotagent.tools.base import RegisteredTool
from dotagent.tools.meta import build_meta_tools


def test_a_yes_with_a_note_is_no_refusal_to_the_model() -> None:
    tool: RegisteredTool = build_meta_tools()[0]
    noted: str = _describe_failure(ApprovalNoted(noted_text("send nothing"), {}), tool)
    assert noted.startswith("Not done yet: the user said yes with a note"), noted
    assert "their note comes first: “send nothing”" in noted
    assert "do it again exactly so" in noted
    assert "Do not retry" not in noted
    refused: str = _describe_failure(ApprovalRejected("the user did not approve this action (denied)", {}), tool)
    assert "Do not retry" in refused
