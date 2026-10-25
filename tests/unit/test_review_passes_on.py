"""A review model that cannot answer for a while (item 649): its quota spent, its rate, its key refused or missing.
A question of Dot's own learning then goes to the next model, as a declined one does (item 249): the run's own model,
then the standby models. The router pauses the spent model, and the next question does not ask it while another
model can answer. An error that says nothing about the model's being able to answer is raised as it comes, and a
question no model takes says who was asked and why."""

import time

import pytest

from dotagent.agent.review import LESSONS_TOOL, LessonProposal, LessonRequest, LessonWriter, lesson_writer_for
from dotagent.config import load_default_config
from dotagent.core.errors import DotError, ProviderQuotaError, ProviderTransportError
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.calls import ModelCall
from dotagent.llm.roles import ModelRoles
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
QUOTA: str = "provider answered HTTP 429: You reached the Free usage limit."


class SpentProvider(ScriptedProvider):
    """Every model of it fails with ``failure``, but the models named in ``answering``, which write one lesson."""

    def __init__(self, failure: DotError, answering: tuple[str, ...]) -> None:
        super().__init__("ollama", ())
        self._failure: DotError = failure
        self._answering: tuple[str, ...] = answering

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.model not in self._answering:
            raise self._failure
        call: ToolCall = ToolCall(id="call-1", name=LESSONS_TOOL, arguments={"lessons": [LESSON]})
        return ModelReply(
            model=request.model,
            text="",
            thinking="",
            tool_calls=(call,),
            usage=Usage(input_tokens=1000, output_tokens=20, cached_tokens=0),
            finish="tool_calls",
            raw_finish="tool_use",
            raw=(),
        )


async def _router(failure: DotError, answering: tuple[str, ...], calls: list[ModelCall]) -> ModelRouter:
    async def record(call: ModelCall) -> None:
        calls.append(call)

    router: ModelRouter = ModelRouter(load_default_config(), record, {})
    await router.register("ollama", SpentProvider(failure, answering))
    return router


def _spent() -> ProviderQuotaError:
    return ProviderQuotaError(QUOTA, 429, None, {"url": "https://ollama.com/api/chat"})


async def test_a_review_model_out_of_quota_passes_the_lessons_to_the_runs_own_model_and_is_then_left_out() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await _router(_spent(), ("writer",), calls)
    writer: LessonWriter = lesson_writer_for(router, "ollama:spent", ("ollama:writer",), 30.0, "lessons:test", None)
    assert await writer(REQUEST) == LessonProposal(lessons=[LESSON])
    assert [call.model for call in calls] == ["ollama:spent", "ollama:writer"]
    # The router paused the spent model: the next run's lessons go straight to the model that answers.
    assert "ollama:spent" in router.paused(time.time())
    calls.clear()
    assert await writer(REQUEST) == LessonProposal(lessons=[LESSON])
    assert [call.model for call in calls] == ["ollama:writer"]


async def test_a_question_no_model_can_take_raises_the_first_error_naming_who_was_asked() -> None:
    calls: list[ModelCall] = []
    router: ModelRouter = await _router(_spent(), (), calls)
    writer: LessonWriter = lesson_writer_for(router, "ollama:spent", ("ollama:other",), 30.0, "lessons:test", None)
    with pytest.raises(ProviderQuotaError) as raised:
        await writer(REQUEST)
    assert raised.value.message.startswith(QUOTA)
    assert (
        "no model could take it: ollama:spent (quota exhausted), ollama:other (quota exhausted)" in raised.value.message
    )
    assert raised.value.context["asked"] == ["ollama:spent (quota exhausted)", "ollama:other (quota exhausted)"]


async def test_an_error_that_says_nothing_of_the_models_quota_is_not_passed_on() -> None:
    calls: list[ModelCall] = []
    failure: ProviderTransportError = ProviderTransportError("the connection was reset", {"url": "https://x"})
    router: ModelRouter = await _router(failure, ("writer",), calls)
    writer: LessonWriter = lesson_writer_for(router, "ollama:broken", ("ollama:writer",), 30.0, "lessons:test", None)
    with pytest.raises(ProviderTransportError):
        await writer(REQUEST)
    assert [call.model for call in calls] == ["ollama:broken"]


def test_after_the_review_model_come_the_runs_own_model_then_the_standby_models_each_once() -> None:
    config = load_default_config()
    models = config.models.model_copy(update={"standby": ["anthropic:claude-sonnet-5-5", "ollama:writer"]})
    roles: ModelRoles = ModelRoles(models)
    assert roles.after_review(("ollama:writer",)) == ("ollama:writer", "anthropic:claude-sonnet-5-5")
    assert roles.after_review() == ("anthropic:claude-sonnet-5-5", "ollama:writer")
