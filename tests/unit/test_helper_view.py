"""A helper as the phone's list of helpers shows it (item 1686): a title from its goal and what it does now."""

import time

from dotagent.server.thread_sync import helper_title, step_of
from dotagent.store.models import EventRecord


def test_a_title_is_the_goals_first_sentence_with_no_record_ids_cut_at_a_word() -> None:
    assert helper_title("Check the build of thr_mwqbll8g_9bbfb2. Then report.") == "Check the build of."
    assert helper_title("Read the_values file\nand more") == "Read the_values file"
    long: str = helper_title("Compare " + "every page of the docs " * 10)
    assert len(long) <= 80 and long.endswith("…") and not long.endswith(" …")


def test_a_label_the_goal_opens_with_is_left_out_of_its_title() -> None:
    goal: str = "Görev: hafıza notlarını birleştirmek.\n\nGirdi dosyası: `/Users/x/C8.md`"
    assert helper_title(goal) == "Hafıza notlarını birleştirmek."
    assert helper_title("Task: check the build") == "Check the build"
    assert helper_title("Fix this: the build fails") == "Fix this: the build fails"


def test_a_step_names_the_tool_a_short_target_and_the_plain_line() -> None:
    called: EventRecord = EventRecord(
        id=3,
        run_id="run_a",
        ts=time.time(),
        kind="tool_call",
        data={"tool": "shell", "args": {"command": "pytest   -q\n tests/unit"}},
    )
    step = step_of(called)
    assert (step.tool, step.target, step.line) == ("shell", "pytest -q tests/unit", "Running a command")
    reply: EventRecord = EventRecord(id=4, run_id="run_a", ts=1.0, kind="assistant", data={"tool_calls": []})
    assert (step_of(reply).tool, step_of(reply).target, step_of(reply).line) == ("", "", "Writing the answer")
