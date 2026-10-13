"""Notes that change while a run works, on a scripted model in a throw-away home: the run is handed the changed notes it
would be handed at its start, within the budget of its starting notes, and a retag is no change. No model is called.
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import REST_TAG, save_memory
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Read todo.txt and say what is on it."
TODO: FileSeed = FileSeed(path="todo.txt", content="Water the plants.\n")
TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="read_file", arguments={"path": TODO.path}),)),
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Water the plants.", "success": True}),)
    ),
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_LIVE_NOTE: str = "[system note] These notes were saved to memory or changed while you work"
OTHER_AGENT: str = "agt_printer"
OWNER_NOTE: str = "The owner keeps the spare house keys with the neighbour at number 12."
OLD_LESSON: str = "Run the backup script with --dry-run first to see which folders it would copy."
THEIR_LESSON: str = "Ask the printer for its status page before sending it a long print job."
THEIR_NEW_LESSON: str = "Print long documents in two halves, since the printer drops jobs of more than fifty pages."
FACTS: int = 40


def _fact(number: int) -> str:
    return (
        f"Fact {number} about the archive: the folder of the year {1980 + number} holds the scanned letters of that "
        "year, sorted first by sender and then by date, with a list of the senders in a text file at its top."
    )


class NotesChangeMidRun:
    """The scripted model, while notes change under the run: ``change`` runs as the first step is asked, so the
    loop finds the changes before the second step."""

    def __init__(self, script: ScriptedProvider, change: Callable[[], Awaitable[None]]) -> None:
        self._script: ScriptedProvider = script
        self._change: Callable[[], Awaitable[None]] = change
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) == 1:
            await self._change()
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_a_running_run_is_handed_the_changed_notes_it_may_be_handed_within_its_budget(
    login_env: dict[str, str],
) -> None:
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            # Before the run: a lesson of Dot's and a lesson of another agent.
            old: MemoryRecord = (await save_memory(runtime.memory, OLD_LESSON, [], "run_before", None)).record
            theirs: MemoryRecord = (
                await save_memory(runtime.memory, THEIR_LESSON, [], "run_printer", OTHER_AGENT)
            ).record

            async def change() -> None:
                # The nightly reflection retires the old lesson and merges tags into the other agent's one.
                await runtime.store.set_memory_tags(old.id, (*old.tags, REST_TAG))
                await runtime.store.set_memory_tags(theirs.id, (*theirs.tags, "printer"))
                # The other agent learns a new lesson, the owner writes a note, and another run of Dot saves many.
                await save_memory(runtime.memory, THEIR_NEW_LESSON, [], "run_printer_2", OTHER_AGENT)
                await save_memory(runtime.memory, OWNER_NOTE, [], None, None)
                for number in range(1, FACTS + 1):
                    await save_memory(runtime.memory, _fact(number), [], "run_archive", None)

            model: NotesChangeMidRun = NotesChangeMidRun(ScriptedProvider(_PROVIDER, TURNS), change)
            await runtime.router.register(_PROVIDER, model)
            await asyncio.to_thread(seed_workspace, runtime.workspace, [TODO])
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            retired: MemoryRecord = await runtime.store.get_memory(old.id)
        finally:
            await close_runtime(runtime)

    assert record.status == "done", record.result
    assert len(model.requests) == 2
    notes: list[str] = [
        message.content for message in model.requests[1].messages if message.content.startswith(_LIVE_NOTE)
    ]
    assert len(notes) == 1, notes
    note: str = notes[0]
    assert OWNER_NOTE in note
    # Another agent's notes are its own, a retired note is not handed on, and a retag is no change.
    assert THEIR_NEW_LESSON not in note and THEIR_LESSON not in note
    assert OLD_LESSON not in note
    assert retired.tags == (REST_TAG,) and retired.updated_at == old.updated_at
    # The many new notes come within the budget of the starting notes; the note says how many more there are.
    shown: list[int] = [number for number in range(1, FACTS + 1) if f"Fact {number} about" in note]
    assert 0 < len(shown) < FACTS
    assert f"{FACTS - len(shown)} more changed notes" in note
