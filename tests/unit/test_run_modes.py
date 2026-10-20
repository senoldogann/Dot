"""The three switches of a run: who answers the questions of the policy, what a verdict of the goal check makes of
the end of a run, what a database from before the switches gets, and what the model is told about them."""

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

import pytest

from dotagent.agent.compact import drop_old_images
from dotagent.agent.loop import RunOutcome, SendBack, _after_verdict, _since_goal
from dotagent.agent.prompt import (
    build_system_prompt,
    clock_note,
    collect_prompt_inputs,
    forgotten_unnamed,
    handed_note_ids,
    notes_missing,
    recalled_again,
    recalled_note,
    turn_note,
)
from dotagent.agent.review import GoalVerdict
from dotagent.config import load_config
from dotagent.core.errors import ConfigError
from dotagent.harness.components import NO_HARNESS
from dotagent.llm.types import ImageData, Message, assistant_message, user_message
from dotagent.policy.engine import route_for
from dotagent.policy.types import ApprovalMode, Decision, DecisionSource, QuestionMode, Route
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.skills.types import Skill
from dotagent.store.db import Store
from dotagent.store.models import RunMode, RunRecord
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL

PATIENCE: int = 4
ENDED: RunOutcome = RunOutcome(status="done", result="the first file is written", needs="")


@pytest.mark.parametrize(
    ("verdict", "source", "mode", "route"),
    [
        ("ask", "supervised", "supervised", "user"),
        ("ask", "preset", "supervised", "user"),
        ("ask", "preset", "ask", "user"),
        ("ask", "preset", "auto", "reviewer"),
        ("ask", "preset", "yolo", "waived"),
        # A change of Dot's own files is the user's, never the review model's (item 398); a yolo run waives it first.
        ("ask", "own_files", "ask", "user"),
        ("ask", "own_files", "auto", "user"),
        ("handoff", "hard_stop", "ask", "user"),
        ("handoff", "hard_stop", "auto", "user"),
        ("handoff", "hard_stop", "yolo", "user"),
    ],
)
def test_a_question_goes_where_the_mode_sends_it(
    verdict: Literal["ask", "handoff"], source: DecisionSource, mode: ApprovalMode, route: Route
) -> None:
    assert route_for(Decision(verdict=verdict, source=source, reason="", rule_id=None), mode) == route


def _verdict(decision: Literal["achieved", "continue", "blocked"], reason: str) -> GoalVerdict:
    return GoalVerdict(decision=decision, reason=reason, unproven_steps=())


def test_an_achieved_goal_ends_the_run_as_done_with_its_answer() -> None:
    failed_claim: RunOutcome = RunOutcome(status="failed", result="all files are written", needs="a review of them")
    outcome: RunOutcome | SendBack = _after_verdict(_verdict("achieved", "all files exist"), failed_claim, 0, PATIENCE)
    assert outcome == RunOutcome(status="done", result="all files are written", needs="a review of them")


def test_a_blocked_goal_fails_the_run_and_keeps_its_answer() -> None:
    outcome: RunOutcome | SendBack = _after_verdict(_verdict("blocked", "it needs a sign-in"), ENDED, 0, PATIENCE)
    assert isinstance(outcome, RunOutcome)
    assert outcome.status == "failed"
    assert outcome.result.startswith(ENDED.result)
    assert "cannot be reached" in outcome.result
    assert "it needs a sign-in" in outcome.result


def test_a_goal_that_is_not_reached_sends_the_run_back_to_work() -> None:
    missing: GoalVerdict = _verdict("continue", "the second file is missing")
    for sent_back in range(PATIENCE):
        outcome: RunOutcome | SendBack = _after_verdict(missing, ENDED, sent_back, PATIENCE)
        assert isinstance(outcome, SendBack), sent_back
        assert "the second file is missing" in outcome.note
        assert "never for anything else" in outcome.note


def test_a_run_that_used_up_its_patience_fails_and_keeps_its_answer() -> None:
    outcome: RunOutcome | SendBack = _after_verdict(
        _verdict("continue", "the second file is missing"), ENDED, PATIENCE, PATIENCE
    )
    assert isinstance(outcome, RunOutcome)
    assert outcome.status == "failed"
    assert outcome.result.startswith(ENDED.result)
    assert f"still not reached after {PATIENCE} rounds" in outcome.result
    assert "the second file is missing" in outcome.result


def test_the_conversation_of_a_run_starts_at_its_goal() -> None:
    earlier: Message = user_message("an earlier goal", ())
    goal: Message = user_message("the goal", ())
    reply: Message = assistant_message("a reply", None, ())
    assert _since_goal([earlier, reply, goal, reply], goal) == [goal, reply]
    assert _since_goal([goal, reply, goal, reply], goal) == [goal, reply]


def test_a_goal_whose_images_were_dropped_still_starts_its_run() -> None:
    """Item 351: once its picture was dropped into a note, the goal no longer equalled itself and the whole chat, its
    earlier runs too, went to the goal check."""
    earlier: Message = user_message("an earlier goal", ())
    goal: Message = user_message(
        "what is on this screenshot?", (ImageData(media_type="image/png", data_b64="iVBORw0K"),)
    )
    reply: Message = assistant_message("a reply", None, ())
    kept: list[Message] = drop_old_images([earlier, reply, goal, reply], 0)
    assert _since_goal(kept, goal) == kept[2:]


def test_a_conversation_whose_start_was_summarised_is_taken_whole() -> None:
    summary: Message = user_message("a summary of the start", ())
    goal: Message = user_message("the goal", ())
    reply: Message = assistant_message("a reply", None, ())
    assert _since_goal([summary, reply], goal) == [summary, reply]


def _prompt(approvals: ApprovalMode, goal_mode: bool) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "cli",
            "aggressive",
            approvals,
            "wait",
            goal_mode,
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


def test_the_prompt_tells_the_model_how_its_questions_are_answered() -> None:
    assert "Approvals:" not in _prompt("ask", False)
    assert "answered by a review model" in _prompt("auto", False)
    assert "what the policy flags runs at once" in _prompt("yolo", False)
    # Money, secrets, data sent out, messages and posts wait for the user in yolo too (item 427).
    assert "still wait for the user, as in every mode" in _prompt("yolo", False)
    assert "answered by a review model" not in _prompt("yolo", False)


def test_the_prompt_keeps_the_model_from_asking_permission_for_an_action() -> None:
    modes: tuple[ApprovalMode, ...] = ("ask", "auto", "yolo")
    for mode in modes:
        assert "Never use it to ask permission for an action" in _prompt(mode, False), mode


def test_a_run_that_hesitates_over_what_it_was_asked_says_so_at_once() -> None:
    """Item 327: a run that hesitates over the work says what holds it back and asks, instead of looking into it on its
    own first; in every kind of run and under every approval mode."""
    rule: str = "If you hesitate to do what was asked, or a part of it, say so at once instead of looking into it"
    kinds: tuple[RunMode, ...] = ("chat", "task", "research")
    for kind in kinds:
        assert rule in _in_mode(kind), kind
    modes: tuple[ApprovalMode, ...] = ("ask", "auto", "yolo")
    for mode in modes:
        assert rule in _prompt(mode, False), mode


def test_the_prompt_names_the_day_and_a_note_after_the_goal_the_time_so_the_prompt_stays_the_same_all_day() -> None:
    today: list[str] = [line for line in _prompt("ask", False).splitlines() if line.startswith("- Today:")]
    assert len(today) == 1 and re.search(r"\d{2}:\d{2}", today[0]) is None, today
    assert re.fullmatch(r"\[system note\] It is now \d{2}:\d{2} \S+\.", clock_note(datetime.now().astimezone()))


def test_the_note_under_a_message_carries_what_changes_from_message_to_message(tmp_path: Path) -> None:
    """The time, the notes recalled for the message and the skills that fit it go under the message (item 404), not
    into the instructions, which stay the same bytes from message to message so the provider's cache holds; a part with
    nothing to say is left out."""
    now: datetime = datetime.now().astimezone()
    alone: str = turn_note(now, [], 0, [], [])
    assert alone == clock_note(now)
    skill: Skill = Skill(
        name="invoice-pdf", description="Fill in an invoice.", root=tmp_path, body="x", source="home", icon="file"
    )
    full: str = turn_note(now, ["[mem_1] The rent is due on the 3rd (the user wrote it, 2026-10-01)"], 1, [skill], [])
    lines: list[str] = full.splitlines()
    assert lines[0] == clock_note(now)
    assert "Notes recalled for this message" in full and "- [mem_1] The rent is due on the 3rd" in full
    assert "never override what the user says now" in full and "stand above" in full
    assert "1 note was left out: saved while reading outside content" in full
    assert full.rstrip().endswith("- invoice-pdf: Fill in an invoice.")
    assert "Skills that fit this message, besides those your instructions list" in full
    # What is handed again after a compaction folds the note away: the same without the time, "" when nothing was.
    assert recalled_note([], 0, [], []) == ""
    recalled: str = recalled_note(
        ["[mem_1] The rent is due on the 3rd (the user wrote it, 2026-10-01)"], 1, [skill], []
    )
    assert full == f"{clock_note(now)}\n\n{recalled}"
    # A note forgotten since an earlier message was handed it is named, as its old text stays in the chat.
    gone: str = recalled_note([], 0, [], ["mem_7"])
    assert gone.startswith("Notes you were handed earlier in this chat that were forgotten since")
    assert "- forgotten: mem_7" in gone
    # The ids a chat's messages carry: handed notes, not the ones named as forgotten, which are named once.
    assert handed_note_ids(f"x [mem_1] y\n[mem_2b] z [not one] [mem_1]\n{gone}") == {"mem_1", "mem_2b"}
    assert forgotten_unnamed(["mem_7", "mem_8"], gone) == ["mem_8"]
    # What a context no longer carries with the same text is handed again: changed, or folded into a summary.
    note: str = "[mem_1] The rent is due on the 3rd (the user wrote it, 2026-10-01)"
    changed: str = "[mem_1] The rent is due on the 5th (the user wrote it, 2026-10-19)"
    assert notes_missing([note, changed], f"earlier\n- {note}\n") == [changed]
    # After a compaction, only what the context lost comes back: nothing, while it carries the whole note still.
    assert recalled_again([note], 1, [skill], ["mem_7"], f"{full}\n{gone}") == ""
    # Stored as a user message, it bears the head every system note does, so the goal's review reads it as data.
    assert (
        recalled_again([note], 1, [skill], ["mem_7"], "")
        == f"[system note] {recalled_note([note], 1, [skill], ['mem_7'])}"
    )


def _lines(approvals: ApprovalMode) -> set[str]:
    """The lines of a prompt but the date, which may turn between two prompts."""
    return {line for line in _prompt(approvals, False).splitlines() if not line.startswith("- Today:")}


def test_yolo_changes_only_the_approvals_line_of_the_prompt() -> None:
    ask: set[str] = _lines("ask")
    yolo: set[str] = _lines("yolo")
    # Secrets are the user's in yolo too (item 427): the line that says so is the same in every mode.
    assert sorted(ask - yolo) == []
    added: list[str] = sorted(yolo - ask)
    assert len(added) == 1, added
    assert added[0].startswith("- Approvals: yolo: what the policy flags runs at once")
    assert _lines("auto") - _lines("ask") == {line for line in _lines("auto") if line.startswith("- Approvals: auto")}


def _asking(source: str, questions: QuestionMode, tool_names: list[str]) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            source,
            "aggressive",
            "ask",
            questions,
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            tool_names,
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


def test_the_prompt_says_that_questions_are_left_only_in_a_run_that_leaves_them() -> None:
    left: str = "ask_user leaves your question for the user"
    assert left in _asking("cli", "leave", [ASK_USER_TOOL])
    assert left not in _asking("cli", "wait", [ASK_USER_TOOL])
    # A delegated run has no ask_user, so it has nothing to leave.
    assert left not in _asking(DELEGATE_SOURCE, "leave", [])
    assert "Do not ask questions" not in _asking(SCHEDULE_SOURCE, "leave", [ASK_USER_TOOL])
    assert "Do not ask questions" in _asking(SCHEDULE_SOURCE, "wait", [ASK_USER_TOOL])


def test_the_prompt_mentions_goal_mode_only_when_it_is_on() -> None:
    assert "Goal mode is on" in _prompt("ask", True)
    assert "Goal mode is on" not in _prompt("ask", False)


def _in_mode(mode: RunMode) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            mode,
            "cli",
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


def test_open_ended_goals_are_held_to_their_targets_in_task_and_research_runs_only() -> None:
    section: str = "# Open-ended goals"
    assert section in _in_mode("task")
    assert section in _in_mode("research")
    # A chat turn ends with a reply and has no finish to hold back.
    assert section not in _in_mode("chat")


def test_task_and_research_runs_keep_acting_and_take_an_ask_to_keep_going_at_its_word() -> None:
    """Item 132: every model is held to the work and to the user's words; a chat turn ends with its reply, and a
    campaign's session with its own part of the work."""
    rules: tuple[str, ...] = (
        "Do not stop to report progress or to offer what the goal asks for",
        "take it at its word",
    )
    session: str = build_system_prompt(
        collect_prompt_inputs(
            "task",
            "cli",
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
            2,
            [],
            None,
            (),
            (),
            None,
            "full",
            NO_HARNESS,
        )
    )
    for rule in rules:
        assert rule in _in_mode("task")
        assert rule in _in_mode("research")
        assert rule not in _in_mode("chat")
        assert rule not in session


def test_a_negative_or_endless_limit_of_a_run_is_refused(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    for limit in ("run_max_tokens = -1", "run_max_minutes = -0.5", "run_max_minutes = inf", "run_max_minutes = nan"):
        path.write_text(f"[agent]\n{limit}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path)


def test_a_step_limit_left_in_the_configuration_is_refused(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text("[agent]\nmax_steps = 40\n", encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        load_config(path)
    assert "max_steps" in str(refused.value.context) + refused.value.message


OLD_RUNS: str = """CREATE TABLE runs (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, goal TEXT NOT NULL, mode TEXT NOT NULL,
    model TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
    result TEXT NOT NULL, steps INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL, parent_run_id TEXT, source TEXT NOT NULL
)"""


def _write_old_database(path: Path) -> None:
    """A database as the first version made it: runs without the columns of the reasoning level and the two switches."""
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute(OLD_RUNS)
        connection.execute(
            "INSERT INTO runs VALUES ('run_old', 'thread_old', 'an old goal', 'task', 'ollama:x', 'done', 1.0, 2.0, "
            "'an old result', 3, 4, 5, NULL, 'cli')"
        )
        connection.commit()
    finally:
        connection.close()


async def test_a_database_from_before_the_switches_gets_their_defaults(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    await asyncio.to_thread(_write_old_database, database)
    for _ in range(2):
        store: Store = await Store.open(database, tmp_path / "images")
        try:
            old: RunRecord = await store.get_run("run_old")
            assert (old.approvals, old.goal_mode, old.effort, old.once, old.result) == (
                "ask",
                False,
                None,
                None,
                "an old result",
            )
        finally:
            await store.close()
    store = await Store.open(database, tmp_path / "images")
    try:
        created: RunRecord = await store.create_run(
            "thread_new",
            "a new goal",
            "task",
            "ollama:x",
            None,
            "cli",
            "medium",
            "standard",
            None,
            "yolo",
            True,
            None,
            None,
            None,
        )
        reread: RunRecord = await store.get_run(created.id)
        assert (reread.approvals, reread.goal_mode) == ("yolo", True)
    finally:
        await store.close()
