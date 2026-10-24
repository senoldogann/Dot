"""A run started by a message on a channel answers on the owner's phone, as plain text: it is told to write for a
phone's screen (item 627)."""

from pathlib import Path

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.harness.components import NO_HARNESS
from dotagent.scheduler import SCHEDULE_SOURCE


def _prompt(source: str) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            source,
            "aggressive",
            "ask",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            [],
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            None,
            "full",
            NO_HARNESS,
        )
    )


def test_a_run_from_imessage_is_told_its_answer_goes_to_a_phone_as_plain_text() -> None:
    told: str = _prompt("imessage")
    assert "started by the user's message in iMessage, on their phone" in told
    assert "no Markdown is drawn" in told
    assert "short paragraphs" in told


def test_a_run_from_anywhere_else_is_not() -> None:
    for source in ("cli", "web", SCHEDULE_SOURCE):
        assert "on their phone" not in _prompt(source)
