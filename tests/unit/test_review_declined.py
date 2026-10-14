"""A review model that declines (item 225): when its provider ends the reply as a refusal, with no call, the lesson
writer says that the model declined, naming it, and does not ask it again, as the same question gets the same answer.
A reply that only lacks the call is still asked for once more."""

import pytest

from dotagent.agent.review import LessonRequest, LessonWriter, lesson_writer_for
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderError, ProviderProtocolError, ProviderRefusedError
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply, Usage

REQUEST: LessonRequest = LessonRequest(
    goal="Write the weekly report to ~/Reports/week41.md.",
    digest="write_file failed once, then wrote the report.",
    signals=("A call failed: write_file",),
    answer="The report is written.",
    known=(),
    reached_goal=True,
)


class EndingProvider(ScriptedProvider):
    """The scripted model, but every reply of it ends as ``raw_finish``, with no text and no call."""

    def __init__(self, raw_finish: str) -> None:
        super().__init__("ollama", ())
        self._raw_finish: str = raw_finish

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return ModelReply(
            model=request.model,
            text="",
            thinking="",
            tool_calls=(),
            usage=Usage(input_tokens=1000, output_tokens=0, cached_tokens=0),
            finish="stop" if self._raw_finish == "stop" else "other",
            raw_finish=self._raw_finish,
            raw=(),
        )


async def _writer(raw_finish: str, calls: list[ModelCall]) -> LessonWriter:
    async def record(call: ModelCall) -> None:
        calls.append(call)

    router: ModelRouter = ModelRouter(load_default_config(), record)
    await router.register("ollama", EndingProvider(raw_finish))
    return lesson_writer_for(router, "ollama:scripted", 30.0, "lessons:test", None)


@pytest.mark.parametrize(
    ("raw_finish", "error", "message", "asked"),
    [
        (
            "refusal",
            ProviderRefusedError,
            "the review model ollama:scripted declined to answer (stop reason: refusal)",
            1,
        ),
        (
            "content_filter",
            ProviderRefusedError,
            "the review model ollama:scripted declined to answer (stop reason: content_filter)",
            1,
        ),
        ("stop", ProviderProtocolError, "the review model did not call propose_lessons", 2),
    ],
)
async def test_a_review_model_that_declines_is_said_to_have_declined_and_is_not_asked_again(
    raw_finish: str, error: type[ProviderError], message: str, asked: int
) -> None:
    calls: list[ModelCall] = []
    writer: LessonWriter = await _writer(raw_finish, calls)
    with pytest.raises(error) as raised:
        await writer(REQUEST)
    assert raised.value.message == message
    assert [call.purpose for call in calls] == ["lessons"] * asked
