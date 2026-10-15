"""A review model that declines (item 225): when its provider ends the reply as a refusal, with no call, the lesson
writer says that the model declined, naming it, and does not ask it again, as the same question gets the same answer.
A reply that only lacks the call is still asked for once more. A question the review model declines goes to the run's
own model when that is another one (item 249), which writes the lessons; a refusal of both names both."""

import pytest

from dotagent.agent.review import LESSONS_TOOL, LessonProposal, LessonRequest, LessonWriter, lesson_writer_for
from dotagent.config import load_default_config
from dotagent.core.errors import ProviderError, ProviderProtocolError, ProviderRefusedError
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply, ToolCall, Usage

REQUEST: LessonRequest = LessonRequest(
    goal="Write the weekly report to ~/Reports/week41.md.",
    digest="write_file failed once, then wrote the report.",
    signals=("A call failed: write_file",),
    answer="The report is written.",
    known=(),
    reached_goal=True,
)


LESSON: str = "Check that the folder exists before writing a file into it."


class EndingProvider(ScriptedProvider):
    """The scripted model, but every reply of it ends as ``raw_finish``, with no text and no call; a model named
    ``writer`` answers instead, with one lesson."""

    def __init__(self, raw_finish: str) -> None:
        super().__init__("ollama", ())
        self._raw_finish: str = raw_finish

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        usage: Usage = Usage(input_tokens=1000, output_tokens=0, cached_tokens=0)
        if request.model == "writer":
            call: ToolCall = ToolCall(id="call-1", name=LESSONS_TOOL, arguments={"lessons": [LESSON]})
            return ModelReply(
                model=request.model,
                text="",
                thinking="",
                tool_calls=(call,),
                usage=usage,
                finish="tool_calls",
                raw_finish="tool_use",
                raw=(),
            )
        return ModelReply(
            model=request.model,
            text="",
            thinking="",
            tool_calls=(),
            usage=usage,
            finish="stop" if self._raw_finish == "stop" else "other",
            raw_finish=self._raw_finish,
            raw=(),
        )


async def _writer(raw_finish: str, others: tuple[str, ...], calls: list[ModelCall]) -> LessonWriter:
    async def record(call: ModelCall) -> None:
        calls.append(call)

    router: ModelRouter = ModelRouter(load_default_config(), record, {})
    await router.register("ollama", EndingProvider(raw_finish))
    return lesson_writer_for(router, "ollama:scripted", others, 30.0, "lessons:test", None)


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
    writer: LessonWriter = await _writer(raw_finish, (), calls)
    with pytest.raises(error) as raised:
        await writer(REQUEST)
    assert raised.value.message == message
    assert [call.purpose for call in calls] == ["lessons"] * asked


async def test_a_question_the_review_model_declines_goes_to_the_runs_own_model() -> None:
    calls: list[ModelCall] = []
    writer: LessonWriter = await _writer("refusal", ("ollama:writer",), calls)
    assert await writer(REQUEST) == LessonProposal(lessons=[LESSON])
    assert [(call.purpose, call.model) for call in calls] == [
        ("lessons", "ollama:scripted"),
        ("lessons", "ollama:writer"),
    ]


@pytest.mark.parametrize(
    ("others", "message", "asked"),
    [
        # The run worked on the review model itself: nobody else to ask.
        (("ollama:scripted",), "the review model ollama:scripted declined to answer (stop reason: refusal)", 1),
        (
            ("ollama:other", "ollama:other"),
            "the review model ollama:scripted declined to answer (stop reason: refusal); ollama:other declined it too",
            2,
        ),
    ],
)
async def test_a_question_no_model_answers_says_who_declined(others: tuple[str, ...], message: str, asked: int) -> None:
    calls: list[ModelCall] = []
    writer: LessonWriter = await _writer("refusal", others, calls)
    with pytest.raises(ProviderRefusedError) as raised:
        await writer(REQUEST)
    assert raised.value.message == message
    assert len(calls) == asked


async def test_a_reply_that_only_lacks_the_call_is_not_passed_on() -> None:
    # Not a refusal: the review model is asked once more, as before, and the run's model never.
    calls: list[ModelCall] = []
    writer: LessonWriter = await _writer("stop", ("ollama:writer",), calls)
    with pytest.raises(ProviderProtocolError):
        await writer(REQUEST)
    assert [call.model for call in calls] == ["ollama:scripted", "ollama:scripted"]
