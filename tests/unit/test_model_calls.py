"""What the router records of a model call: a reply, a failure of any kind and a call cut off, also when the recording
itself fails; and how it pauses a provider that cannot answer for a while."""

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from structlog.testing import capture_logs

from dotagent.config import load_default_config
from dotagent.core.errors import ProviderQuotaError, ProviderTransportError
from dotagent.llm.calls import CANCELLED, CallOrigin, CallRecorder, ModelCall, call_event_data
from dotagent.llm.router import ModelRef, ModelRouter, Pause
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, StreamDelta, Usage

REF: ModelRef = ModelRef(provider="ollama", model="fake")
OTHER: ModelRef = ModelRef(provider="ollama", model="other")
ORIGIN: CallOrigin = CallOrigin(run_id="run_x", purpose="goal_check")
REQUEST: ChatRequest = ChatRequest(
    model="fake",
    messages=(),
    tools=(),
    temperature=None,
    max_output_tokens=None,
    effort="medium",
    speed="standard",
    session_key="thr_x:goal",
    cache_mark=None,
)
REPLY: ModelReply = ModelReply(
    model="fake",
    text="ok",
    thinking="",
    tool_calls=(),
    usage=Usage(input_tokens=10, output_tokens=5, cached_tokens=2),
    finish="stop",
    raw_finish="stop",
    raw=(),
)


class FakeProvider:
    """A connector whose call does what ``answer`` does."""

    def __init__(self, answer: Callable[[], Awaitable[ModelReply]]) -> None:
        self._answer: Callable[[], Awaitable[ModelReply]] = answer

    @property
    def name(self) -> str:
        return "ollama"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._answer()

    async def model_info(self, model: str) -> ModelInfo:
        raise NotImplementedError

    async def list_models(self) -> tuple[ModelInfo, ...]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class StreamingProvider:
    """A connector whose call streams through the sink it is given, as ``stream`` does, and answers what it returns."""

    def __init__(self, stream: Callable[[DeltaSink], Awaitable[ModelReply]]) -> None:
        self._stream: Callable[[DeltaSink], Awaitable[ModelReply]] = stream

    @property
    def name(self) -> str:
        return "ollama"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._stream(sink)

    async def model_info(self, model: str) -> ModelInfo:
        raise NotImplementedError

    async def list_models(self) -> tuple[ModelInfo, ...]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def keeping(calls: list[ModelCall]) -> CallRecorder:
    async def record(call: ModelCall) -> None:
        calls.append(call)

    return record


def failing(calls: list[ModelCall]) -> CallRecorder:
    """A recorder whose store cannot be written: it sees the call, then fails."""

    async def record(call: ModelCall) -> None:
        calls.append(call)
        raise RuntimeError("the store cannot be written")

    return record


async def router_with(answer: Callable[[], Awaitable[ModelReply]], recorder: CallRecorder) -> ModelRouter:
    router: ModelRouter = ModelRouter(load_default_config(), recorder, {})
    await router.register("ollama", FakeProvider(answer))
    return router


async def reply() -> ModelReply:
    return REPLY


async def broken() -> ModelReply:
    raise KeyError("choices")


async def unreachable() -> ModelReply:
    raise ProviderTransportError("the provider cannot be reached", {"model": "fake"})


async def endless() -> ModelReply:
    await asyncio.Event().wait()
    return REPLY


async def no_sink(_delta: object) -> None:
    return None


async def test_the_first_word_of_a_reply_is_timed_and_a_reply_without_words_is_not() -> None:
    """A call records how long its first streamed word took (item 404); a reset of a retry is not a word, and a call
    that streams nothing records none."""
    calls: list[ModelCall] = []
    router: ModelRouter = ModelRouter(load_default_config(), keeping(calls), {})

    async def streaming(sink: DeltaSink) -> ModelReply:
        await sink(StreamDelta(kind="reset", text=""))
        await asyncio.sleep(0.02)
        await sink(StreamDelta(kind="text", text="Hel"))
        await sink(StreamDelta(kind="text", text="lo"))
        return REPLY

    await router.register("ollama", StreamingProvider(streaming))
    seen: list[str] = []

    async def collect(delta: StreamDelta) -> None:
        seen.append(delta.kind)

    assert await router.complete(REF, REQUEST, collect, ORIGIN) == REPLY
    assert seen == ["reset", "text", "text"]
    first: float | None = calls[0].first_token_seconds
    assert first is not None and 0.02 <= first <= calls[0].seconds
    assert call_event_data(calls[0])["first_token_seconds"] == round(first, 3)
    quiet: ModelRouter = await router_with(reply, keeping(calls))
    await quiet.complete(REF, REQUEST, no_sink, ORIGIN)
    assert calls[1].first_token_seconds is None and call_event_data(calls[1])["first_token_seconds"] is None


async def test_a_reply_is_recorded_with_its_run_purpose_and_tokens() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await router_with(reply, keeping(calls))
    assert await router.complete(REF, REQUEST, no_sink, ORIGIN) == REPLY
    [call] = calls
    assert (call.run_id, call.purpose, call.model, call.session_key) == (
        "run_x",
        "goal_check",
        "ollama:fake",
        REQUEST.session_key,
    )
    assert (call.input_tokens, call.output_tokens, call.cached_tokens, call.error) == (10, 5, 2, None)
    assert call.seconds >= 0.0


async def test_a_failure_of_any_kind_is_recorded_with_its_type_and_raised() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await router_with(broken, keeping(calls))
    with pytest.raises(KeyError):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    assert [(call.error, call.input_tokens) for call in calls] == [("KeyError", 0)]


async def test_a_recorder_that_fails_leaves_the_error_of_the_call_to_its_caller() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await router_with(unreachable, failing(calls))
    with capture_logs() as logs, pytest.raises(ProviderTransportError):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    assert [call.error for call in calls] == ["ProviderTransportError"]
    assert [entry["event"] for entry in logs] == ["model_call_not_recorded"]


async def test_a_call_cut_off_by_its_timeout_is_recorded_as_cancelled_and_its_caller_gets_the_timeout() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await router_with(endless, failing(calls))
    with capture_logs(), pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await router.complete(REF, REQUEST, no_sink, ORIGIN)
    assert [call.error for call in calls] == [CANCELLED]


async def test_a_recorder_that_fails_after_a_reply_raises_its_own_error() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await router_with(reply, failing(calls))
    with pytest.raises(RuntimeError, match="cannot be written"):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    assert [call.error for call in calls] == [None]


async def test_a_model_that_cannot_answer_is_paused_until_a_call_it_answers_or_a_new_key() -> None:
    answers: list[Callable[[], Awaitable[ModelReply]]] = [spent, unreachable, reply, reply, spent]

    async def in_turn() -> ModelReply:
        return await answers.pop(0)()

    router: ModelRouter = await router_with(in_turn, keeping([]))
    cooldown_s: float = load_default_config().models.helper_cooldown_s
    with pytest.raises(ProviderQuotaError):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    paused: dict[str, Pause] = router.paused(time.time())
    assert list(paused) == [REF.label]
    assert paused[REF.label].reason == "quota exhausted"
    assert time.time() < paused[REF.label].until <= time.time() + cooldown_s
    assert router.paused(paused[REF.label].until) == {}
    # A transport error says nothing about the model: the pause stays as it was.
    with pytest.raises(ProviderTransportError):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    assert router.paused(time.time()) == paused
    # Another model of the provider (a local one beside a cloud one) has a pause of its own.
    assert await router.complete(OTHER, REQUEST, no_sink, ORIGIN) == REPLY
    assert router.paused(time.time()) == paused
    assert await router.complete(REF, REQUEST, no_sink, ORIGIN) == REPLY
    assert router.paused(time.time()) == {}
    # A new key of the provider ends the pauses of its models.
    with pytest.raises(ProviderQuotaError):
        await router.complete(REF, REQUEST, no_sink, ORIGIN)
    await router.retire(REF.provider)
    assert router.paused(time.time()) == {}


async def spent() -> ModelReply:
    raise ProviderQuotaError("credits exhausted", 429, None, {"model": "fake"})
