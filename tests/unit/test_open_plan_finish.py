"""An open plan step does not let finish end a task, even with goal mode off. Closing the steps does."""

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.endings import PREFIX_STALLED
from dotagent.store.models import EventRecord, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def _finish(answer: str) -> ScriptedTurn:
    return _call("finish", answer=answer, success=True)


_PLAN: ScriptedTurn = _call(
    "update_plan",
    steps=[{"text": "Write the note", "status": "pending"}],
)


async def _play(turns: list[ScriptedTurn], *, goal_mode: bool) -> tuple[RunRecord, list[EventRecord]]:
    recording: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(update={"shell": base.shell.model_copy(update={"sandbox": False})})
    assert config.agent.goal_mode is False
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, recording)
            run: RunRecord = await manager.start(
                "Write the note.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo",
                goal_mode, (),
            )  # fmt: skip
            ended: RunRecord = await manager.wait(run.id)
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 10_000)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    return ended, events


@pytest.mark.asyncio
async def test_open_steps_do_not_let_finish_end_the_run_until_they_are_closed() -> None:
    ended, events = await _play(
        [
            _PLAN,
            _finish("Too soon."),
            _finish("Still too soon."),
            _call("mark_plan_step", step=1, status="done", evidence="write_file wrote note.md"),
            _finish("The note is written."),
        ],
        goal_mode=False,
    )
    reminders: list[EventRecord] = [event for event in events if event.kind == "plan_reminder"]
    assert (ended.status, ended.result) == ("done", "The note is written.")
    assert [event.data["open_steps"] for event in reminders] == [[1], [1]]


@pytest.mark.asyncio
async def test_open_steps_stop_the_run_instead_of_finishing_once_the_reminders_are_spent() -> None:
    """Goal mode is off, so no checker can accept the finish. After stall_limit reminders the progress stop ends it."""
    ended, events = await _play(
        [_PLAN, *(_finish(f"Still open {n}.") for n in range(5))],
        goal_mode=False,
    )
    reminders: list[EventRecord] = [event for event in events if event.kind == "plan_reminder"]
    assert ended.status == "failed"
    assert ended.result.startswith(PREFIX_STALLED)
    assert "still open" in ended.result
    assert len(reminders) == 4
