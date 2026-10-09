"""The three switches of a run: who answers the questions of the policy, what a verdict of the goal check makes of the end
of a run, what a database from before the switches gets, and what the model is told about them."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from dotagent.agent.loop import RunOutcome, SendBack, _after_verdict, _since_goal
from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.agent.review import GoalVerdict
from dotagent.config import load_config
from dotagent.core.errors import ConfigError
from dotagent.llm.types import Message, assistant_message, user_message
from dotagent.policy.engine import route_for
from dotagent.policy.types import ApprovalMode, QuestionMode, Route
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import RunRecord
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL

PATIENCE: int = 4
ENDED: RunOutcome = RunOutcome(status="done", result="the first file is written")


@pytest.mark.parametrize(
    ("verdict", "mode", "route"),
    [
        ("ask", "ask", "user"),
        ("ask", "auto", "reviewer"),
        ("ask", "yolo", "waived"),
        ("handoff", "ask", "user"),
        ("handoff", "auto", "user"),
        ("handoff", "yolo", "user"),
    ],
)
def test_a_question_goes_where_the_mode_sends_it(
    verdict: Literal["ask", "handoff"], mode: ApprovalMode, route: Route
) -> None:
    assert route_for(verdict, mode) == route


def _verdict(decision: Literal["achieved", "continue", "blocked"], reason: str) -> GoalVerdict:
    return GoalVerdict(decision=decision, reason=reason)


def test_an_achieved_goal_ends_the_run_as_done_with_its_answer() -> None:
    failed_claim: RunOutcome = RunOutcome(status="failed", result="all files are written")
    outcome: RunOutcome | SendBack = _after_verdict(_verdict("achieved", "all files exist"), failed_claim, 0, PATIENCE)
    assert outcome == RunOutcome(status="done", result="all files are written")


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


def test_a_conversation_whose_start_was_summarised_is_taken_whole() -> None:
    summary: Message = user_message("a summary of the start", ())
    goal: Message = user_message("the goal", ())
    reply: Message = assistant_message("a reply", None, ())
    assert _since_goal([summary, reply], goal) == [summary, reply]


def _prompt(approvals: ApprovalMode, goal_mode: bool) -> str:
    return build_system_prompt(
        collect_prompt_inputs("task", "cli", "aggressive", approvals, "wait", goal_mode, Path("/tmp/work"), [], [], [])
    )


def test_the_prompt_tells_the_model_how_its_questions_are_answered() -> None:
    assert "Approvals:" not in _prompt("ask", False)
    assert "answered by a review model" in _prompt("auto", False)
    assert "nothing waits for the user" in _prompt("yolo", False)
    assert "answered by a review model" not in _prompt("yolo", False)


def test_the_prompt_keeps_the_model_from_asking_permission_for_an_action() -> None:
    modes: tuple[ApprovalMode, ...] = ("ask", "auto", "yolo")
    for mode in modes:
        assert "Never use it to ask permission for an action" in _prompt(mode, False), mode


def _lines(approvals: ApprovalMode) -> set[str]:
    """The lines of a prompt but the clock, which may turn between two prompts."""
    return {line for line in _prompt(approvals, False).splitlines() if not line.startswith("- Local time:")}


def test_yolo_changes_only_the_secrets_line_and_the_approvals_line_of_the_prompt() -> None:
    ask: set[str] = _lines("ask")
    yolo: set[str] = _lines("yolo")
    removed: list[str] = sorted(ask - yolo)
    added: list[str] = sorted(yolo - ask)
    assert len(removed) == 1 and removed[0].startswith(
        "- Passwords, payment details and one-time codes are never typed"
    )
    assert len(added) == 2, added
    assert added[0].startswith("- Approvals: yolo: nothing waits for the user")
    assert added[1].startswith("- Passwords, one-time codes and payment details: enter them when you can read them")
    assert _lines("auto") - _lines("ask") == {line for line in _lines("auto") if line.startswith("- Approvals: auto")}


def _asking(source: str, questions: QuestionMode, tool_names: list[str]) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task", source, "aggressive", "ask", questions, False, Path("/tmp/work"), [], tool_names, []
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
            assert (old.approvals, old.goal_mode, old.effort, old.result) == ("ask", False, None, "an old result")
        finally:
            await store.close()
    store = await Store.open(database, tmp_path / "images")
    try:
        created: RunRecord = await store.create_run(
            "thread_new", "a new goal", "task", "ollama:x", None, "cli", "medium", "yolo", True
        )
        reread: RunRecord = await store.get_run(created.id)
        assert (reread.approvals, reread.goal_mode) == ("yolo", True)
    finally:
        await store.close()
