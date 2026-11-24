"""The system prompt stays the same while the iPhone locks and unlocks (item 1217): a changed prefix is read again at
full price instead of from the provider's cache, and the phone's state words made 8 of 26 cold first steps within an
hour on 2026-11-17."""

from pathlib import Path

from dotagent.agent.prompt import PromptInputs, build_system_prompt, collect_prompt_inputs, started_note, with_phone
from dotagent.agent.resume import RESUME_SOURCE
from dotagent.evals import EVAL_SOURCE
from dotagent.harness.components import NO_HARNESS
from dotagent.proactive import NOTICE_SOURCE
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import CLI_SOURCE, UI_SOURCE
from dotagent.tools.context import AskMode
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL
from dotagent.tools.replies import PASS_REPLY_TOOL
from dotagent.tools.team import TEAM_SOURCE


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


def test_only_a_message_of_the_users_is_said_to_have_started_the_run() -> None:
    """A run Dot started (a notice, a resumed run) is not told the user's message started it: under the lead, that
    would stand in place of a schedule's "nobody is watching" said earlier in the chat."""
    for source in (RESUME_SOURCE, NOTICE_SOURCE, "background", TEAM_SOURCE):
        note: str = started_note(_from(source, "wait"))
        assert "the user's own message" not in note
        assert f"started by Dot ({source})" in note
    for source in (UI_SOURCE, CLI_SOURCE, EVAL_SOURCE):
        assert "the user's own message" in started_note(_from(source, "wait"))


def _with_tools(*names: str) -> PromptInputs:
    return collect_prompt_inputs(
        "task", "ui", "aggressive", "ask", "wait", False, False, Path("/tmp/work"), [], 0,
        list(names), [], 0, None, [], None, (), (), None, "full", NO_HARNESS,
    )  # fmt: skip


def test_the_prompt_teaches_code_mode_in_the_same_few_lines_whenever_the_run_has_code_run() -> None:
    """Item 1501: when to write code and when to call a tool, in lines that hold no state of the run, so that the
    provider's cache of the instructions holds; a run without the tool is told nothing of it."""
    with_code: str = build_system_prompt(_with_tools("shell", "code_run"))
    without: str = build_system_prompt(_with_tools("shell"))
    assert "# Code\ncode_run runs Python in a sandbox" in with_code and "# Code" not in without
    for words in ("many calls, a lot of data, a loop or a poll", "at most 5 rows", "For one call"):
        assert words in with_code
    assert "the same approvals as one of yours" in with_code
    assert build_system_prompt(_with_tools("shell", "code_run")) == with_code
    # Nothing of the run's own state is in them: the same words whoever started the run and however it asks.
    other_inputs: PromptInputs = collect_prompt_inputs(
        "task", "team", "cautious", "yolo", "leave", True, True, Path("/elsewhere"), [], 0,
        ["code_run", "shell"], [], 0, None, [], None, (), (), None, "full", NO_HARNESS,
    )  # fmt: skip
    section: str = with_code[with_code.index("# Code") :].split("\n\n")[0]
    assert section in build_system_prompt(other_inputs)
