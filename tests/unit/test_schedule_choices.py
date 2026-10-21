"""A schedule made from a chat starts its runs as that chat does (item 291)."""

from dotagent.store.models import ChatChoices, RunRecord, ScheduleOptions
from dotagent.tools.schedule import chat_options, schedule_mode

_RUN: RunRecord = RunRecord(
    id="run-1",
    thread_id="thr-1",
    goal="Plan the week",
    mode="chat",
    model="opencode_go:deepseek-v4.1-flash",
    status="running",
    created_at=1_760_000_000.0,
    finished_at=None,
    result="",
    steps=0,
    input_tokens=0,
    output_tokens=0,
    parent_run_id=None,
    source="ui",
    effort="max",
    speed="fast",
    once=None,
    approvals="yolo",
    goal_mode=True,
    campaign_id=None,
    agent_id=None,
    rewindable=False,
)


def test_the_chat_choices_win_over_the_run_that_made_the_schedule() -> None:
    choices: ChatChoices = ChatChoices(
        mode="research", model="ollama:qwen3:8b", effort=None, speed="standard", approvals="ask", goal_mode=False
    )
    assert chat_options(_RUN, choices) == ScheduleOptions(
        model="ollama:qwen3:8b",
        effort=None,
        speed="standard",
        approvals="ask",
        goal_mode=False,
        timezone=None,
        history_runs=None,
        same_chat=True,
        stop_after_failures=None,
    )


def test_a_chat_without_kept_choices_schedules_as_its_run_started() -> None:
    assert chat_options(_RUN, None) == ScheduleOptions(
        model="opencode_go:deepseek-v4.1-flash",
        effort="max",
        speed="fast",
        approvals="yolo",
        goal_mode=True,
        timezone=None,
        history_runs=None,
        same_chat=True,
        stop_after_failures=None,
    )


def test_the_mode_asked_wins_and_a_talking_chat_schedules_tasks() -> None:
    assert schedule_mode("task", "research") == "task"
    assert schedule_mode(None, "research") == "research"
    assert schedule_mode(None, "chat") == "task"
