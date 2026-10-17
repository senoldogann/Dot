"""Whole runs against a real model: the chosen model does every step and every compaction, a compacted run goes on
where it was without repeating or losing steps, the plan the UI paints ends in step with the work, a note the user
adds while a run works reaches that run, and a side question (/btw) is answered while the run goes on without it.

The runs use the real local Ollama server and the cloud model the user chose in the UI; the tests are skipped when it is
not there. ``[models] main`` names another model and ``fast`` a model that does not exist, so a step or a summary that
used either would show (or fail).
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.aside import AsideAnswer, answer_aside
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import NotFoundError
from dotagent.llm.types import Message
from dotagent.memory.service import save_memory
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

OLLAMA_ROOT: str = "http://localhost:11434"
CHOSEN: str = "deepseek-v4.1-flash:cloud"
CHOSEN_REF: str = f"ollama:{CHOSEN}"
MAIN_REF: str = "ollama:minimax-m2.7:cloud"
MISSING_REF: str = "ollama:no-such-model:cloud"
RUN_TIMEOUT_S: float = 600.0
COLOUR: str = "teal"


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == CHOSEN for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {CHOSEN}")


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    # The system prompt and the tool specs are about 18k tokens; 24k leaves room for one big output only, so the run
    # below has to compact whether or not old tool results were trimmed first.
    (path / "config.toml").write_text(
        f'[models]\nmain = "{MAIN_REF}"\nfast = "{MISSING_REF}"\nreview = "{MAIN_REF}"\n\n'
        '[agent]\ncontext_tokens = 24000\n\n[policy]\napprovals = "yolo"\n',
        encoding="utf-8",
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


async def _events(runtime: Runtime, run_id: str, kind: str) -> list[EventRecord]:
    return [event for event in await runtime.store.list_events(run_id, 0, 5000) if event.kind == kind]


async def _start_and_wait_until_running(runtime: Runtime, manager: RunManager, goal: str) -> RunRecord:
    """Start a run on the chosen model and return once its first step is under way."""
    async with runtime.bus.subscribe(None) as subscription, asyncio.timeout(RUN_TIMEOUT_S):
        run: RunRecord = await manager.start(
            goal, "task", CHOSEN_REF, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        while True:
            event: EventRecord = await subscription.queue.get()
            if event.run_id == run.id and event.kind == "run_started":
                return run


GOAL: str = (
    "Work through these six steps strictly in order, one step per reply, and keep a plan of exactly these six steps "
    "with update_plan at the start, marking each step done with mark_plan_step as you finish it.\n"
    "1. Run the shell command `seq 1 4000` and look at the output.\n"
    "2. Append the line `step2` to the file progress.txt in the working directory (shell: echo step2 >> "
    "progress.txt).\n"
    "3. Run the shell command `seq 4001 8000`.\n"
    "4. Append the line `step4` to progress.txt.\n"
    "5. Run the shell command `seq 8001 12000`.\n"
    "6. Append the line `step6` to progress.txt.\n"
    "Then finish. The final answer must contain the exact content of progress.txt and, if a memory note tells you "
    "the user's favourite colour, that colour."
)


@requires_model
async def test_a_compacted_run_continues_on_the_chosen_model_without_repeating_or_losing_steps(
    runtime: Runtime, manager: RunManager
) -> None:
    run: RunRecord = await _start_and_wait_until_running(runtime, manager, GOAL)
    # A note the user writes while the run works (Settings › Memory) reaches the run at its next step.
    await save_memory(runtime.memory, f"The user's favourite colour is {COLOUR}.", ["aboutme"], None, None, None)
    async with asyncio.timeout(RUN_TIMEOUT_S):
        finished: RunRecord = await manager.wait(run.id)
    assert finished.status == "done", finished.result
    assert finished.model == CHOSEN_REF

    # Model consistency: every compaction ran on the chosen model and was written by it.
    compactions: list[EventRecord] = await _events(runtime, run.id, "compacted")
    assert compactions, "the run never compacted; lower context_tokens"
    assert {event.data["model"] for event in compactions} == {CHOSEN_REF}
    assert {event.data["method"] for event in compactions} == {"model"}
    queued: EventRecord = (await _events(runtime, run.id, "run_queued"))[0]
    assert queued.data["models"] == {
        "steps": CHOSEN_REF,
        "compaction": CHOSEN_REF,
        "review": MAIN_REF,
        "vision": runtime.config.models.vision,
    }

    # Continuity: each append happened exactly once and in order, whatever was compacted in between.
    progress: str = (runtime.workspace / "progress.txt").read_text(encoding="utf-8")
    assert progress.split() == ["step2", "step4", "step6"]
    history: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    assert any(message.content.startswith("[Continuity note") and GOAL in message.content for message in history)

    # The plan: recorded, updated step by step, and closed when the run ended.
    plans: list[EventRecord] = await _events(runtime, run.id, "plan")
    assert len(plans) >= 3
    final_steps = plans[-1].data["steps"]
    assert isinstance(final_steps, list)
    assert all(isinstance(step, dict) and step["status"] in {"done", "skipped"} for step in final_steps), final_steps

    # The note written during the run reached it and was used.
    live_notes: list[EventRecord] = [
        event for event in await _events(runtime, run.id, "memories") if event.data.get("live") is True
    ]
    assert live_notes
    assert COLOUR in finished.result.lower()


ASIDE_GOAL: str = (
    "Do these three steps in order, one per reply, with the shell tool.\n"
    "1. Run `sleep 4; echo alpha > aside.txt` in the working directory.\n"
    "2. Run `sleep 4; echo beta >> aside.txt`.\n"
    "3. Run `cat aside.txt`.\n"
    "Then finish with the exact content of aside.txt."
)
MARKER: str = "zebra-41"


@requires_model
async def test_a_side_question_is_answered_while_the_run_goes_on_without_seeing_it(
    runtime: Runtime, manager: RunManager
) -> None:
    run: RunRecord = await _start_and_wait_until_running(runtime, manager, ASIDE_GOAL)
    first, second = await asyncio.gather(
        answer_aside(
            runtime, run.id, f"({MARKER}) Which file is this task writing to? Answer with the file name only."
        ),
        answer_aside(runtime, run.id, f"({MARKER}) How many steps does the task have? Answer with a number only."),
    )
    assert "aside.txt" in first.answer, first
    assert "3" in second.answer, second
    assert first.model == second.model == CHOSEN_REF
    async with asyncio.timeout(RUN_TIMEOUT_S):
        finished: RunRecord = await manager.wait(run.id)

    # The run went on by itself and did its whole task.
    assert finished.status == "done", finished.result
    assert (runtime.workspace / "aside.txt").read_text(encoding="utf-8").split() == ["alpha", "beta"]
    # The side questions are events of the run, never messages of its thread.
    history: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    assert not any(MARKER in message.content for message in history)
    answers: list[EventRecord] = await _events(runtime, run.id, "aside_answer")
    assert {event.data["id"] for event in answers} == {first.id, second.id}
    assert len(await _events(runtime, run.id, "aside_question")) == 2


async def test_a_side_question_about_an_unknown_run_is_not_found(runtime: Runtime) -> None:
    with pytest.raises(NotFoundError):
        answer: AsideAnswer = await answer_aside(runtime, "run_missing0_0000", "anything?")
        raise AssertionError(answer)
