"""The prompt cache of a chat across its runs (item 1742), measured on a scripted model that records every request: the
instructions and the tools of a chat's next run are the bytes of its last, a chat that comes back after a pause writes
its cache for an hour, and a run says in its own numbers how much of its input was read from the cache and why its first
step was cold when it was. No model is called and nothing leaves the Mac."""

import asyncio
from collections.abc import Sequence

import pytest

from dotagent.agent.cache_report import CacheReport
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import db_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, Usage
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.store.stats import read_cache_report
from dotagent.tools.shell import read_login_env

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_INPUT: int = 30_000


class _Recording:
    """The scripted model, keeping every request it was sent. Its first request finds nothing in the cache and writes
    it, as the first call of a chat does; every later one reads all but a few thousand tokens from it."""

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        self._script: ScriptedProvider = ScriptedProvider(_PROVIDER, turns)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        first: bool = not self.requests
        self.requests.append(request)
        reply: ModelReply = await self._script.complete(request, sink)
        usage: Usage = (
            Usage(input_tokens=_INPUT, output_tokens=40, cached_tokens=0, cache_write_tokens=_INPUT)
            if first
            else Usage(input_tokens=_INPUT, output_tokens=40, cached_tokens=_INPUT - 2_000, cache_write_tokens=0)
        )
        return reply.model_copy(update={"usage": usage})

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


def _finish() -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),))


def _earlier(seconds: float) -> None:
    """Every model call of the chat so far is ``seconds`` older, as if the owner had been away."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path())) as connection:
        connection.execute("UPDATE events SET ts = ts - ? WHERE kind = 'model_call'", (seconds,))
        connection.commit()


@pytest.mark.asyncio
async def test_a_chats_next_run_sends_the_same_instructions_and_tools_and_says_how_much_the_cache_gave_it() -> None:
    recording: _Recording = _Recording([_finish(), _finish(), _finish()])
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, recording)
            first: RunRecord = await manager.start(
                "Say done.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
            )  # fmt: skip
            assert (await manager.wait(first.id)).status == "done"
            second: RunRecord = await manager.start(
                "Say done again.", "task", SCRIPTED_MODEL, first.thread_id, "ui", None, None, "standard", None,
                "yolo", False, (),
            )  # fmt: skip
            assert (await manager.wait(second.id)).status == "done"
            # The owner is away for fifteen minutes: the provider's five minutes would have run out.
            await asyncio.to_thread(_earlier, 900.0)
            third: RunRecord = await manager.start(
                "And once more.", "task", SCRIPTED_MODEL, first.thread_id, "ui", None, None, "standard", None,
                "yolo", False, (),
            )  # fmt: skip
            assert (await manager.wait(third.id)).status == "done"
            calls: list[EventRecord] = await runtime.store.events_of_kind(first.id, "model_call", 10)
            reports: list[CacheReport | None] = [
                await asyncio.to_thread(read_cache_report, db_path(), run.id) for run in (first, second, third)
            ]
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    one, two, three = recording.requests
    # The bytes of the prefix are the chat's last run's: the system message and the tools, to the character.
    assert two.messages[0] == one.messages[0] and three.messages[0] == one.messages[0]
    assert two.tools == one.tools and three.tools == one.tools
    # Back within five minutes the provider's own time serves; back after fifteen, an hour is written.
    assert (one.cache_ttl, two.cache_ttl, three.cache_ttl) == ("5m", "5m", "1h")
    # The call records what was written to the cache.
    assert calls and calls[0].data["cache_write_tokens"] == _INPUT
    # The reports: a new chat is cold for a reason, and the next run of it finds its prompt, the same prompt.
    first_report, second_report, third_report = reports
    assert (
        first_report is not None
        and first_report.first_cause == "new_chat"
        and first_report.cache_write_tokens == _INPUT
    )
    assert second_report is not None and second_report.first_cause == "warm" and second_report.changed == ()
    assert third_report is not None and third_report.changed == () and third_report.gap_s is not None
    assert 800.0 < third_report.gap_s < 1000.0
    assert second_report.share is not None and second_report.share > 0.9
