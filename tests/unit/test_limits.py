"""The limits of a run: which runs have them, what counts towards them, when one is reached, and what the run is told
and ends with."""

from pathlib import Path

from dotagent.agent.continuity import new_ledger
from dotagent.agent.limits import (
    LimitReached,
    RunLimits,
    limit_event_data,
    limit_note,
    limit_reached,
    limit_result,
    limits_of,
)
from dotagent.config import AgentConfig, AppConfig, load_default_config
from dotagent.evals.runner import eval_config
from dotagent.store.db import Store
from dotagent.store.endings import classify_ending
from dotagent.store.models import RunRecord, ThreadRecord


def _config(minutes: float, tokens: int) -> AgentConfig:
    return load_default_config().agent.model_copy(update={"run_max_minutes": minutes, "run_max_tokens": tokens})


def test_only_task_and_research_runs_with_a_limit_set_have_limits() -> None:
    assert limits_of(load_default_config().agent, "task") is None
    assert limits_of(_config(30, 0), "chat") is None
    assert limits_of(_config(30, 0), "task") == RunLimits(max_minutes=30, max_tokens=0)
    assert limits_of(_config(0, 200_000), "research") == RunLimits(max_minutes=0, max_tokens=200_000)


def test_eval_runs_are_cut_by_their_task_not_by_the_limits_of_the_owner() -> None:
    owner: AppConfig = load_default_config().model_copy(update={"agent": _config(30, 100_000)})
    assert limits_of(eval_config(owner).agent, "task") is None


async def test_the_tokens_of_a_run_count_the_runs_it_delegated_to(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        parent: RunRecord = await store.create_run(
            thread.id, "goal", "task", "ollama:a", None, "test", "medium", "ask", False
        )
        child: RunRecord = await store.create_run(
            thread.id, "part", "task", "ollama:a", parent.id, "delegate", "medium", "ask", False
        )
        other: RunRecord = await store.create_run(
            thread.id, "goal", "task", "ollama:a", None, "test", "medium", "ask", False
        )
        await store.add_run_tokens(parent.id, 1000, 50)
        await store.add_run_tokens(child.id, 300, 20)
        await store.add_run_tokens(other.id, 7000, 700)
        assert await store.family_tokens(parent.id) == 1370
        assert await store.family_tokens(child.id) == 320
    finally:
        await store.close()


def test_a_limit_is_reached_once_the_run_has_spent_all_of_it() -> None:
    both: RunLimits = RunLimits(max_minutes=2, max_tokens=10_000)
    assert limit_reached(both, 119.9, 9_999) is None
    assert limit_reached(both, 120.0, 0) == LimitReached(seconds=120.0, tokens=0, limits=both)
    assert limit_reached(both, 1.0, 10_000) == LimitReached(seconds=1.0, tokens=10_000, limits=both)
    # 0 is no limit.
    assert limit_reached(RunLimits(max_minutes=0, max_tokens=10_000), 86_400.0, 9_999) is None
    assert limit_reached(RunLimits(max_minutes=2, max_tokens=0), 119.9, 10**9) is None


def test_a_run_past_its_limit_is_told_what_it_spent_and_ends_with_its_own_ending() -> None:
    reached: LimitReached = LimitReached(seconds=3672.0, tokens=512_000, limits=RunLimits(max_minutes=60, max_tokens=0))
    note: str = limit_note(reached)
    assert note.startswith("[system note] The limit of this run is reached: it has used 61.2 of 60 minutes. Finish now")
    result: str = limit_result(reached, new_ledger("Grow the list"), "1. [in_progress] Collect\nTargets: 20 leads")
    assert result.startswith(
        "Stopped: the run reached its limit (61.2 of 60 minutes) and did not finish when it was told to.\n"
    )
    assert "Its plan:\n1. [in_progress] Collect\nTargets: 20 leads" in result
    assert classify_ending("failed", result) == "limit"
    tokens: LimitReached = LimitReached(
        seconds=5.0, tokens=512_000, limits=RunLimits(max_minutes=0, max_tokens=500_000)
    )
    assert "it has used 512,000 of 500,000 tokens." in limit_note(tokens)
    assert limit_event_data(tokens) == {"seconds": 5.0, "tokens": 512_000, "max_minutes": 0, "max_tokens": 500_000}
