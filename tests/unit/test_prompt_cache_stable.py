"""The system prompt stays the same while the iPhone locks and unlocks (item 1217): a changed prefix is read again at
full price instead of from the provider's cache, and the phone's state words made 8 of 26 cold first steps within an
hour on 2026-11-17."""

from pathlib import Path

from dotagent.agent.prompt import PromptInputs, build_system_prompt, collect_prompt_inputs, with_phone
from dotagent.harness.components import NO_HARNESS


def _inputs() -> PromptInputs:
    return collect_prompt_inputs(
        "task", "team", "aggressive", "yolo", "wait", False, False, Path("/tmp/work"), [], 0,
        ["shell", "phone_status"], [], 0, None, [], None, (), (), None, "full", NO_HARNESS,
    )  # fmt: skip


def test_the_phone_is_named_but_its_state_never_reaches_the_system_prompt() -> None:
    prompt: str = build_system_prompt(with_phone(_inputs()))
    assert "phone_status says whether it is reachable and whether it is locked" in prompt
    for word in ("reachable but locked", "out of reach", "not checked yet", "at the last check"):
        assert word not in prompt
    assert build_system_prompt(with_phone(_inputs())) == prompt
