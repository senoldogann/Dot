"""Dot's first words in a chat (item 2018): a run the owner starts says its words at once, then the fast model's line of
what it looks at first, unless the run spoke before the line came. Played by a real runtime and run manager, the
scripted model standing for the run's own model and for the fast model, each held until the test lets it go.

No network and no model service is used."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.first_words import FIRST_WORDS_EVENT
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.first_words]

GOAL: str = "Write the quarterly report from the notes."
LINE: str = "Looking at the notes folder first."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class _Model(ScriptedProvider):
    """The run's steps, held until ``step`` is set, and the fast model's line, held until ``line`` is set (``said``:
    what it answers; ``None``: it fails). ``asked``: the requests of the fast model."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER, [_FINISH] * 8)
        self.step: asyncio.Event = asyncio.Event()
        self.line: asyncio.Event = asyncio.Event()
        self.said: str | None = LINE
        self.asked: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.session_key.endswith(":first_words"):
            self.asked.append(request)
            await self.line.wait()
            if self.said is None:
                raise ProviderProtocolError("the fast model failed", {})
            return await ScriptedProvider(_PROVIDER, [ScriptedTurn(text=self.said, calls=())]).complete(request, sink)
        await self.step.wait()
        return await super().complete(request, sink)


def _config(on: bool) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"first_words": on, "goal_mode": False}),
            "models": config.models.model_copy(update={"fast": SCRIPTED_MODEL}),
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _running(login_env: dict[str, str], model: _Model, on: bool) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(on), login_env)
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager
        finally:
            model.step.set()
            model.line.set()
            await manager.shutdown()
            await close_runtime(runtime)


async def _start(manager: RunManager, source: str) -> RunRecord:
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, source, None, None, "standard", None, "yolo", False, ()
    )


async def _events(runtime: Runtime, run_id: str) -> list[EventRecord]:
    return await runtime.store.list_events(run_id, 0, 1000)


async def _until(runtime: Runtime, run_id: str, kind: str, count: int) -> list[EventRecord]:
    """The run's events once ``count`` of them are of ``kind``; fails after ten seconds."""
    async with asyncio.timeout(10):
        while True:
            events: list[EventRecord] = await _events(runtime, run_id)
            if sum(1 for event in events if event.kind == kind) >= count:
                return events
            await asyncio.sleep(0.02)


def _lines(events: list[EventRecord]) -> list[object]:
    return [event.data["text"] for event in events if event.kind == FIRST_WORDS_EVENT]


async def test_the_words_come_at_once_and_the_line_takes_their_place_while_the_run_has_said_nothing(
    login_env: dict[str, str],
) -> None:
    model: _Model = _Model()
    async with _running(login_env, model, True) as (runtime, manager):
        run: RunRecord = await _start(manager, UI_SOURCE)
        # At once, while the run's first step is held: before the start's notes and skills, and the first step.
        events: list[EventRecord] = await _until(runtime, run.id, FIRST_WORDS_EVENT, 1)
        kinds: list[str] = [event.kind for event in events]
        assert kinds.index(FIRST_WORDS_EVENT) == kinds.index("run_started") + 1
        started: float = next(event.ts for event in events if event.kind == "run_started")
        said: float = next(event.ts for event in events if event.kind == FIRST_WORDS_EVENT)
        assert said - started < 0.5
        # The fast model is shown the owner's message alone, in a fence, and asked for no tool.
        model.line.set()
        events = await _until(runtime, run.id, FIRST_WORDS_EVENT, 2)
        assert _lines(events) == ["", LINE]
        assert len(model.asked) == 1 and model.asked[0].tools == ()
        assert GOAL in model.asked[0].messages[-1].content and "<<REQUEST" in model.asked[0].messages[-1].content
        model.step.set()
        assert (await manager.wait(run.id)).status == "done"
        assert _lines(await _events(runtime, run.id)) == ["", LINE]
        # The line is no message of the conversation the run's model reads.
        messages = await runtime.store.load_messages(run.thread_id, 0)
        assert all(LINE not in message.content for message in messages)


async def test_a_run_that_speaks_first_gets_no_second_voice(login_env: dict[str, str]) -> None:
    model: _Model = _Model()
    async with _running(login_env, model, True) as (runtime, manager):
        run: RunRecord = await _start(manager, UI_SOURCE)
        model.step.set()
        assert (await manager.wait(run.id)).status == "done"
        # The fast model answers after the run called its finish and ended: its line is not told.
        model.line.set()
        await asyncio.sleep(0.2)
        assert _lines(await _events(runtime, run.id)) == [""]


async def test_a_fast_model_that_fails_leaves_the_words_said_at_once(login_env: dict[str, str]) -> None:
    model: _Model = _Model()
    model.said = None
    async with _running(login_env, model, True) as (runtime, manager):
        run: RunRecord = await _start(manager, UI_SOURCE)
        model.line.set()
        await _until(runtime, run.id, FIRST_WORDS_EVENT, 1)
        await asyncio.sleep(0.2)
        model.step.set()
        assert (await manager.wait(run.id)).status == "done"
        assert _lines(await _events(runtime, run.id)) == [""]


async def test_a_schedules_run_and_first_words_off_say_nothing_first(login_env: dict[str, str]) -> None:
    model: _Model = _Model()
    model.step.set()
    model.line.set()
    async with _running(login_env, model, True) as (runtime, manager):
        scheduled: RunRecord = await _start(manager, SCHEDULE_SOURCE)
        assert (await manager.wait(scheduled.id)).status == "done"
        assert _lines(await _events(runtime, scheduled.id)) == []
    async with _running(login_env, model, False) as (runtime, manager):
        run: RunRecord = await _start(manager, UI_SOURCE)
        assert (await manager.wait(run.id)).status == "done"
        assert _lines(await _events(runtime, run.id)) == []
    assert model.asked == []
