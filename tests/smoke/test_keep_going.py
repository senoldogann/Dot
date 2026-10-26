"""A run asked to keep going in the real loop (item 132): the words turn goal mode on whatever was chosen, the goal
checker is told of them, and a finish that stops short of the goal is sent back to work.

No network and no model service is used: the scripted model plays the run and answers for the review model too.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from test_run_limits import Played, RecordingProvider, _call, _config, _finish, _played, _session, _turn

from dotagent.agent.review import GOAL_TOOL
from dotagent.config import load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

ASKED: str = "Gather ten facts about the moon, and do not stop until all ten are gathered."
NOT_ASKED: str = "Gather ten facts about the moon."
# What the checker's question holds when the goal asks to keep going (see ``review._goal_prompt``).
ASKED_PART: str = "The goal asks the agent to keep going until it is done."
SHORT: str = "Four facts are gathered. Shall I go on with the other six?"
WHOLE: str = "All ten facts are gathered."
MISSING: str = "Six of the ten facts are not gathered."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def _play(
    login_env: dict[str, str], goal: str, goal_mode: bool, turns: Sequence[ScriptedTurn], approvals: str = "ask"
) -> Played:
    """``goal`` as a task with goal mode chosen as ``goal_mode``, played by the scripted model in a throw-away home."""
    model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
    async with _session(_config(0.0, 0, False), login_env, model) as (runtime, manager):
        run: RunRecord = await manager.start(
            goal, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, approvals, goal_mode, ()
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            record: RunRecord = await manager.wait(run.id)
        return await _played(runtime, model, record)


def _verdict(decision: str, reason: str) -> ScriptedTurn:
    return _turn(_call(GOAL_TOOL, {"decision": decision, "reason": reason, "unproven_steps": []}))


def _asked_with(played: Played, part: str) -> list[bool]:
    """For each goal check of the run, whether its question held ``part``."""
    return [any(part in message.content for message in check.messages) for check in played.checks]


def _told(played: Played) -> list[bool]:
    """For each goal check of the run, whether its question held the part that says the goal asks to keep going."""
    return [any(ASKED_PART in message.content for message in check.messages) for check in played.checks]


async def test_a_task_asked_not_to_stop_runs_in_goal_mode_and_its_short_finish_is_sent_back(
    login_env: dict[str, str],
) -> None:
    played: Played = await _play(
        login_env,
        ASKED,
        False,
        [
            _finish(SHORT, True),
            _verdict("continue", MISSING),
            _finish(WHOLE, True),
            _verdict("achieved", "All ten facts are in the answer."),
        ],
    )
    assert played.record.goal_mode, played.record
    assert (played.record.status, played.record.result) == ("done", WHOLE), played.record
    assert [(check["decision"], check["final"]) for check in played.data("goal_check")] == [
        ("continue", False),
        ("achieved", True),
    ]
    assert _told(played) == [True, True]
    assert len(played.steps) == 2
    assert any(MISSING in message.content for message in played.steps[1].messages)


async def test_a_task_not_asked_keeps_the_goal_mode_chosen_and_its_checker_is_not_told(
    login_env: dict[str, str],
) -> None:
    off: Played = await _play(login_env, NOT_ASKED, False, [_finish(SHORT, True)])
    assert not off.record.goal_mode, off.record
    assert (off.record.status, off.record.result) == ("done", SHORT), off.record
    assert off.data("goal_check") == ()
    on: Played = await _play(
        login_env, NOT_ASKED, True, [_finish(WHOLE, True), _verdict("achieved", "All ten facts are in the answer.")]
    )
    assert on.record.goal_mode, on.record
    assert [check["decision"] for check in on.data("goal_check")] == ["achieved"]
    assert _told(on) == [False]


WAITS: str = "The posts are out; the replies come in over the next hours."
FOLLOW_UPS_PART: str = "FOLLOW-UPS (scheduled runs in this chat, from the harness)"
# A day ahead, so that the schedule is still to come when the goal check reads it.
FOLLOW_UP: JsonObject = {
    "name": "Read the replies",
    "goal": "Read the replies to the posts and answer each one.",
    "at": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
}


async def test_a_rest_that_waits_on_time_with_no_run_scheduled_is_sent_back_to_schedule_one(
    login_env: dict[str, str],
) -> None:
    """Item 675: the checker's "scheduled" holds only when the harness finds a run scheduled in the chat."""
    played: Played = await _play(
        login_env,
        ASKED,
        False,
        [
            _finish(WAITS, True),
            _verdict("scheduled", "The replies are read later."),
            _finish(WHOLE, True),
            _verdict("achieved", "All ten facts are in the answer."),
        ],
    )
    assert (played.record.status, played.record.result) == ("done", WHOLE), played.record
    assert [check["decision"] for check in played.data("goal_check")] == ["continue", "achieved"]
    assert _asked_with(played, FOLLOW_UPS_PART) == [False, False]
    assert any("schedule_create" in message.content for message in played.steps[1].messages)


async def test_a_rest_that_waits_on_a_run_scheduled_in_the_chat_ends_the_run_as_done(
    login_env: dict[str, str],
) -> None:
    """Item 675: the run schedules the follow-up, the checker is shown it and the run ends done, not failed."""
    played: Played = await _play(
        login_env,
        ASKED,
        False,
        [
            _turn(_call("schedule_create", FOLLOW_UP)),
            _finish(WAITS, True),
            _verdict("scheduled", "The replies are read by the run scheduled tonight."),
        ],
        approvals="yolo",
    )
    assert played.record.status == "done", played.record
    assert played.record.result.startswith(WAITS), played.record
    assert "a scheduled run picks it up" in played.record.result
    assert [(check["decision"], check["final"]) for check in played.data("goal_check")] == [("scheduled", True)]
    assert _asked_with(played, FOLLOW_UPS_PART) == [True]
    assert _asked_with(played, "- Read the replies: next run ") == [True]
