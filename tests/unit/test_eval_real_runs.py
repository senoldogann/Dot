"""The suite of real runs: the owner's own goals read from the record (those that went wrong first, a trajectory only
from a run that went right), mixed with golden and owner-written cases by share, and an attempt graded on its tools'
order and its ending."""

import time
from pathlib import Path

from dotagent.config import load_default_config
from dotagent.evals.catalog import select_tasks
from dotagent.evals.real_runs import (
    GOLDEN_TASKS,
    OwnerCase,
    RealCase,
    Suite,
    golden_cases,
    grade,
    mix_suite,
    order_followed,
    owner_cases,
    read_owner_cases,
    read_real_cases,
    task_of,
    trajectory_of,
)
from dotagent.evals.results import RunMetrics, TaskOutcome, TraceStep
from dotagent.evals.runner import eval_config, held_to_workspace
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, RunStatus


def test_a_trajectory_keeps_the_acting_tools_in_order_and_an_attempt_follows_a_share_of_it() -> None:
    assert trajectory_of(["read_file", "read_file", "memory_search", "edit_file", "finish", "shell"]) == (
        "read_file",
        "edit_file",
        "shell",
    )
    assert order_followed(("read_file", "edit_file", "shell"), ("read_file", "shell")) == (2 / 3, ("edit_file",))
    assert order_followed(("shell", "read_file"), ("read_file", "shell")) == (0.5, ())
    assert order_followed((), ("shell",)) == (1.0, ())


def _case(origin: str, number: int) -> RealCase:
    return RealCase.model_validate(
        {
            "id": f"{origin}-{number}",
            "origin": origin,
            "goal": "g",
            "mode": "task",
            "run_id": None,
            "reason": "done" if origin == "real" else origin,
            "tools": [],
            "skills": [],
        }
    )


def test_a_suite_mixes_five_eighty_fifteen_and_a_short_kind_gives_what_it_has() -> None:
    golden = [_case("golden", n) for n in range(10)]
    real = [_case("real", n) for n in range(100)]
    owner = [_case("owner", n) for n in range(10)]
    suite: Suite = mix_suite(golden, real, owner, 40, 0.0)
    assert [case.origin for case in suite.cases].count("golden") == 2
    assert [case.origin for case in suite.cases].count("real") == 32
    assert [case.origin for case in suite.cases].count("owner") == 6
    short: Suite = mix_suite(golden, real[:5], (), 40, 0.0)
    assert [case.origin for case in short.cases] == ["golden", "golden", *["real"] * 5]


async def _run(store: Store, source: str, status: RunStatus, tools: list[tuple[str, str]], goal: str) -> RunRecord:
    thread = await store.create_thread("A chat", "ui")
    run: RunRecord = await store.create_run(
        thread.id, goal, "task", "ollama:m", None, source, None, "standard", None, "ask", False, None, None, None
    )
    for tool, name in tools:
        await store.add_event(run.id, "tool_call", {"tool": tool, "args": {"name": name}})
    await store.finish_run(run.id, status, "ok")
    return run


async def test_the_owners_runs_are_read_read_only_those_that_went_wrong_first(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    store: Store = await Store.open(database, tmp_path / "screenshots")
    try:
        good: RunRecord = await _run(
            store, "ui", "done", [("read_file", ""), ("skill", "memo"), ("edit_file", "")], "a"
        )
        failed: RunRecord = await _run(store, "schedule", "failed", [("shell", "")], "b")
        marked: RunRecord = await _run(store, "ui", "done", [("web_fetch", "")], "c")
        await store.put_reaction(marked.id, "m1", "wrong", marked.thread_id, time.time())
        # Not the owner's words: a code review's diff, an evaluation, a reply to someone else.
        for source in ("review", "eval", "reply"):
            await _run(store, source, "failed", [("shell", "")], "d")
    finally:
        await store.close()
    cases: tuple[RealCase, ...] = read_real_cases(database, 0.0)
    assert [(case.run_id, case.reason) for case in cases] == [
        (failed.id, "failed"),
        (marked.id, "corrected"),
        (good.id, "done"),
    ]
    assert [case.tools for case in cases] == [(), (), ("read_file", "edit_file")]
    assert cases[2].skills == ("memo",)
    assert task_of(cases[2], ()).checks[0].model_dump() == {"kind": "skill_read", "name": "memo"}


def test_the_owners_cases_are_read_from_their_file_and_none_without_one(tmp_path: Path) -> None:
    path: Path = tmp_path / "owner.json"
    assert read_owner_cases(path) == ()
    path.write_text('[{"goal": "Sum the column", "mode": "task", "tools": ["read_file"], "skills": []}]')
    written: tuple[OwnerCase, ...] = read_owner_cases(path)
    assert [(case.id, case.origin, case.tools) for case in owner_cases(written)] == [
        ("owner-1", "owner", ("read_file",))
    ]


def test_an_attempt_passes_when_it_ends_well_and_follows_the_order() -> None:
    case: RealCase = _case("real", 1).model_copy(update={"tools": ("read_file", "edit_file", "shell")})
    steps: tuple[TraceStep, ...] = tuple(
        TraceStep(kind="tool", name=name, seconds=0.1, ok=True, input_tokens=0, output_tokens=0, note="")
        for name in ("read_file", "edit_file", "shell")
    )
    metrics: RunMetrics = RunMetrics(
        steps=2, input_tokens=10, output_tokens=5, tool_calls=2, tool_errors=0, invalid_arguments=0, stalls=0,
        compactions=0, unanswered_questions=0,
    )  # fmt: skip
    outcome: TaskOutcome = TaskOutcome(
        task_id=case.id, category="multi_step", attempt=1, passed=True, status="done", cut="none", answer="ok",
        checks=(), metrics=metrics, seconds=1.0, trace=steps,
    )  # fmt: skip
    assert grade(case, outcome).passed
    assert not grade(case, outcome.model_copy(update={"trace": steps[2:]})).passed
    assert not grade(case, outcome.model_copy(update={"passed": False})).passed


def test_a_real_case_is_held_to_its_workspace_and_the_golden_tasks_are_in_the_catalog() -> None:
    config = eval_config(load_default_config())
    aggressive = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "aggressive"})})
    for case_id in ("real-mvtsvirm-4bb461", "owner-1"):
        task = task_of(_case("real", 1).model_copy(update={"id": case_id}), ())
        assert held_to_workspace(aggressive, task).policy.preset == "balanced"
    assert len(golden_cases(select_tasks(GOLDEN_TASKS))) == len(GOLDEN_TASKS)
