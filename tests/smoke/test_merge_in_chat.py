"""A chat that quoted notes a merge put aside (item 584, the audit of the harness): the old text of a note stays in the
chat, and only its id tells the model that the note is gone. After a merge the next message names each source as
forgotten and hands the merged note in their place, once, whether or not the message's own words recall it.

No model service is used: the scripted model plays the runs."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.lifecycle import merge_notes
from dotagent.memory.service import forget_memory
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TWIN: str = "pdftotext keeps the columns of a table only with the -layout flag."
OTHER: str = "Use pdftotext with -layout when a table must keep its columns."
GOAL: str = "How do I keep the columns of a table with pdftotext?"
FOLLOW_UP: str = "Say hello in one word."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager
    model: RecordingProvider


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, model=model)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _ask(home: Home, goal: str, thread_id: str | None) -> RunRecord:
    run: RunRecord = await home.manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await home.manager.wait(run.id)


def _handed(home: Home, since: int) -> str:
    """What the model was handed under the user's messages in the requests made from number ``since`` on."""
    return "\n".join(
        message.content
        for request in home.model.requests[since:]
        for message in request.messages
        if message.role == "user"
    )


async def test_a_chat_that_quoted_notes_a_merge_put_aside_is_told_they_are_gone_and_handed_the_merged_note_once(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, [_FINISH, _FINISH, _FINISH, _FINISH]) as home:
        store = home.runtime.store
        first: MemoryRecord = await store.add_memory(TWIN, ("lesson",), "run_1", None, None, False)
        second: MemoryRecord = await store.add_memory(OTHER, ("lesson",), "run_2", None, None, False)
        gone: MemoryRecord = await store.add_memory(
            "Pdftotext notes the owner dropped later.", ("lesson",), "run_3", None, None, False
        )
        # The first message of the chat is handed the notes that fit it.
        opened: RunRecord = await _ask(home, GOAL, None)
        handed: str = _handed(home, 0)
        assert f"[{first.id}] {TWIN}" in handed and f"[{second.id}] {OTHER}" in handed and gone.id in handed
        # They are merged (the words of the first kept), and another is forgotten, between the messages.
        merged: MemoryRecord = await merge_notes(home.runtime.memory, [first.id, second.id], first.id, time.time())
        await forget_memory(home.runtime.memory, gone.id)
        seen: int = len(home.model.requests)
        # A message of the chat that does not fit the notes: the sources are named as forgotten, the merged note is
        # handed in their place, and the other forgotten note is named too, as before.
        await _ask(home, FOLLOW_UP, opened.thread_id)
        later: str = _handed(home, seen)
        for note in (first, second, gone):
            assert f"- forgotten: {note.id}" in later, note.id
        assert f"[{merged.id}] {TWIN}" in later
        # The next message names none of them again and hands the merged note no more.
        seen = len(home.model.requests)
        await _ask(home, "Say goodbye in one word.", opened.thread_id)
        again: str = _handed(home, seen)
        # (The earlier notes of the chat are in its history, so what is checked is what the new message adds.)
        assert again.count(f"- forgotten: {first.id}") == 1 and again.count(f"[{merged.id}] {TWIN}") == 1
        # A merged note a later message recalls by its words is handed by the usual way, once.
        seen = len(home.model.requests)
        await _ask(home, GOAL, opened.thread_id)
        assert _handed(home, seen).count(f"[{merged.id}] {TWIN}") == 1
