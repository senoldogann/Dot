"""A plan step marked done is judged by the run's record, not by its words (item 1745), and the run's account counts
what the run did from its tool results (item 1746). A scripted model through the real loop, tools and store."""

from pathlib import Path

import pytest

from dotagent.agent.account import ResultFact, opened_after, result_fact, run_account, step_proof
from dotagent.agent.manager import RunManager
from dotagent.agent.plan import PLAN_EVENT, PlanItem, PlanPayload, plan_from_event
from dotagent.config import AppConfig, load_default_config
from dotagent.core.outcomes import StepTally
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


_PLAN: ScriptedTurn = _call(
    "update_plan",
    steps=[{"text": "Look at the folder", "status": "pending"}, {"text": "Say what is in it", "status": "pending"}],
)


async def _play(turns: list[ScriptedTurn]) -> list[EventRecord]:
    recording: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(update={"shell": base.shell.model_copy(update={"sandbox": False})})
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, recording)
            run: RunRecord = await manager.start(
                "Look at the folder.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            await manager.wait(run.id)
            return await runtime.store.list_events(run.id, 0, 10_000)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


def _last_plan(events: list[EventRecord]) -> PlanPayload:
    return plan_from_event([event for event in events if event.kind == PLAN_EVENT][-1])


def _results(events: list[EventRecord], tool: str) -> list[EventRecord]:
    return [event for event in events if event.kind == "tool_result" and event.data["tool"] == tool]


@pytest.mark.asyncio
async def test_a_step_a_tool_result_shows_is_shown_by_it_and_one_none_shows_is_not_verified(tmp_path: Path) -> None:
    events: list[EventRecord] = await _play(
        [
            _PLAN,
            _call("list_dir", path=str(tmp_path)),
            _call("mark_plan_step", step=1, status="done", evidence="list_dir listed the folder"),
            # Nothing ran between the two marks: the second step was open from the start, so the folder's listing
            # shows it too. A third, written done when nothing ran after the plan, is shown by nothing.
            _call("mark_plan_step", step=2, status="done", evidence="said it"),
            _call(
                "update_plan",
                steps=[
                    {"text": "Look at the folder", "status": "done"},
                    {"text": "Say what is in it", "status": "done"},
                    {"text": "Send the list", "status": "pending"},
                ],
            ),
            _call("mark_plan_step", step=3, status="done", evidence="I sent it"),
            _call("finish", answer="Done.", success=True),
        ]
    )
    listed: str = str(_results(events, "list_dir")[0].data["call_id"])
    plan: PlanPayload = _last_plan(events)
    assert [(step.proof, step.shown_by) for step in plan.steps] == [
        ("shown", (listed,)),
        ("shown", (listed,)),
        ("none", ()),
    ]
    marks: list[EventRecord] = _results(events, "mark_plan_step")
    assert "not verified" not in str(marks[0].data["text"])
    assert "Step 3 is recorded done, but no tool result" in str(marks[-1].data["text"])
    assert "3. [done, not verified] Send the list" in str(marks[-1].data["text"])
    facts: list[ResultFact] = [result_fact(event) for event in events if event.kind == "tool_result"]
    account = run_account(facts, plan)
    assert (account.plan_done, account.unshown, account.open_steps) == (3, ("3. Send the list",), ())


def _fact(event_id: int, tool: str, kind: str, tally: StepTally | None) -> ResultFact:
    return ResultFact(
        event_id=event_id, call_id=f"c{event_id}", tool=tool, kind=kind, ok=True, flags=(), target="", summary="",
        tally=tally,
    )  # fmt: skip


_UNCHECKED: StepTally = StepTally(verified=0, contradicted=0, uncertain=0, not_checked=1, failed=0)
_SEEN: StepTally = StepTally(verified=2, contradicted=0, uncertain=0, not_checked=0, failed=0)


def test_an_act_not_seen_to_hold_leaves_its_step_unverified_until_something_looks() -> None:
    act: ResultFact = _fact(1, "browser_act", "browser_click", _UNCHECKED)
    assert step_proof([act]).kind == "unverified"
    assert step_proof([act, _fact(2, "update_plan", "meta", None)]).kind == "unverified"
    assert step_proof([act, _fact(2, "browser_snapshot", "browser_read", None)]).kind == "shown"
    assert step_proof([_fact(1, "browser_act", "browser_click", _SEEN)]).kind == "shown"
    assert step_proof([_fact(1, "update_plan", "meta", None)]).kind == "none"


def test_a_step_is_open_from_the_first_plan_of_its_newest_open_stretch() -> None:
    def plan(status: str) -> PlanPayload:
        return PlanPayload(steps=(PlanItem(text="Book it", status=status, evidence="x" if status == "done" else ""),))

    assert opened_after([(5, plan("pending")), (9, plan("in_progress"))], "book it") == 5
    assert opened_after([(5, plan("pending")), (7, plan("done")), (9, plan("pending"))], "Book it") == 9
    assert opened_after([(5, PlanPayload(steps=()))], "Book it") == 0
