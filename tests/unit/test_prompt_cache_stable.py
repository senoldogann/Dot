"""The system prompt stays the same while the iPhone locks and unlocks (item 1217): a changed prefix is read again at
full price instead of from the provider's cache, and the phone's state words made 8 of 26 cold first steps within an
hour on 2026-11-17."""

from pathlib import Path

from dotagent.agent.prompt import PromptInputs, build_system_prompt, collect_prompt_inputs, started_note, with_phone
from dotagent.harness.components import NO_HARNESS
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.tools.context import AskMode
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL
from dotagent.tools.replies import PASS_REPLY_TOOL


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


def _from(source: str, questions: AskMode) -> PromptInputs:
    return collect_prompt_inputs(
        "task", source, "aggressive", "yolo", questions, False, False, Path("/tmp/work"), [], 0,
        ["shell", ASK_USER_TOOL, PASS_REPLY_TOOL], [], 0, None, [], None, (), (), None, "full", NO_HARNESS,
    )  # fmt: skip


def test_the_system_prompt_is_the_same_whoever_started_the_run() -> None:
    """Item 1219: a chat whose runs come from the app, a schedule and a phone keeps one system prompt; how each run was
    started is told in the note stored under its goal."""
    runs: list[PromptInputs] = [
        _from("ui", "choose"),
        _from(SCHEDULE_SOURCE, "leave"),
        _from("imessage", "wait"),
        _from(DELEGATE_SOURCE, "wait"),
    ]
    assert len({build_system_prompt(inputs) for inputs in runs}) == 1
    notes: list[str] = [started_note(inputs) for inputs in runs]
    assert len(set(notes)) == len(runs)
    assert all(note.startswith("[system note] How this run was started") for note in notes)
    assert "started by the user's own message" in notes[0]
    assert "nobody is watching" in notes[1] and "the user's own message" not in notes[1]
