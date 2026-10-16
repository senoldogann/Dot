"""Item 306 with the real store and the nightly reflection on the scripted model: a note a run saved and a newer note
about the same thing are shown to the review model; the older one it calls outdated rests, names the note that replaced
it and is no longer found, and the reflection's record and journal say so. A note the owner wrote is never the one put
aside, and a note about something else is not asked about.
"""

import asyncio
import time

import pytest

from dotagent.agent.reflection import reflect
from dotagent.agent.reflection_record import Reflection
from dotagent.agent.review import OUTDATED_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import REST_TAG, recall
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import MemoryRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
OWNERS: str = "The owner keeps the monthly invoices in the folder ~/Desktop/Invoices."
OLD: str = "The owner keeps the monthly invoices in the folder ~/Documents/Invoices."
OTHER: str = "The owner's printer is the Brother in the study."
NEW: str = "The owner keeps the monthly invoices in the folder ~/Dropbox/Invoices now."


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


def _config() -> AppConfig:
    """An eval configuration with the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})


def _outdated() -> ScriptedTurn:
    verdicts: list[dict[str, bool | str]] = [{"outdated": True, "reason": "the folder moved"}]
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=OUTDATED_TOOL, arguments={"verdicts": verdicts}),))


async def test_the_note_a_newer_one_made_outdated_is_put_aside_and_names_what_replaced_it() -> None:
    login_env: dict[str, str] = await read_login_env(load_default_config().shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, [_outdated()]))
            await runtime.router.register(_PROVIDER, model)
            owners: MemoryRecord = await runtime.store.add_memory(OWNERS, (), None, None, None)
            old: MemoryRecord = await runtime.store.add_memory(OLD, (), "run_old", None, None)
            other: MemoryRecord = await runtime.store.add_memory(OTHER, (), "run_other", None, None)
            new: MemoryRecord = await runtime.store.add_memory(NEW, (), "run_new", None, None)
            before: list[str] = [hit.record.id for hit in await recall(runtime.memory, "Documents", 10)]
            reflection: Reflection = await reflect(runtime, asyncio.Lock(), time.time())
            put_aside: MemoryRecord = await runtime.store.get_memory(old.id)
            kept: list[MemoryRecord] = [await runtime.store.get_memory(note.id) for note in (owners, other, new)]
            # Words of each note alone: those all three share are in most notes, and a search leaves them out.
            searched: list[list[str]] = [
                [hit.record.id for hit in await recall(runtime.memory, words, 10)]
                for words in ("Documents", "Dropbox", "Desktop")
            ]
            assert reflection.journal is not None
            journal: MemoryRecord = await runtime.store.get_memory(reflection.journal)
        finally:
            await close_runtime(runtime)
    # One question, about the one pair: the owner's note and the note about the printer were not in it.
    assert len(model.requests) == 1
    asked: str = "\n".join(message.content for message in model.requests[0].messages)
    assert OLD in asked and NEW in asked and OWNERS not in asked and OTHER not in asked
    assert reflection.outdated == 1
    assert REST_TAG in put_aside.tags and put_aside.replaced_by == new.id
    assert all(REST_TAG not in note.tags and note.replaced_by is None for note in kept)
    assert before == [old.id] and searched == [[], [new.id], [owners.id]]
    assert "Put aside 1 note that newer ones made outdated." in journal.content
