"""A model that replays a script: the stand-in of the offline check and of the tests."""

from collections.abc import Sequence

from dotagent.core.errors import EvalError
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, ToolCall, Usage

# What every scripted reply reports as its use of tokens: fixed, so that a run of the script is the same every time.
_INPUT_TOKENS: int = 1000
_OUTPUT_TOKENS: int = 50
_CONTEXT_LENGTH: int = 128_000


class ScriptedProvider:
    """Answers the n-th model call with the n-th turn of its script, whatever the conversation holds.

    It offers the one model the script was written for, with tool calls and no vision, thinking or reasoning levels.
    A call after the last turn is an error: a run that goes on longer than its script is wrong, and says so.
    """

    def __init__(self, name: str, turns: Sequence[ScriptedTurn]) -> None:
        self._name: str = name
        self._turns: tuple[ScriptedTurn, ...] = tuple(turns)
        self._served: int = 0

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self._served >= len(self._turns):
            raise EvalError(
                "the scripted model has no reply left", {"model": request.model, "replies": len(self._turns)}
            )
        turn: ScriptedTurn = self._turns[self._served]
        self._served += 1
        calls: tuple[ToolCall, ...] = tuple(
            ToolCall(id=f"call_{self._served}_{index}", name=call.tool, arguments=call.arguments)
            for index, call in enumerate(turn.calls)
        )
        return ModelReply(
            model=request.model,
            text=turn.text,
            thinking="",
            tool_calls=calls,
            usage=Usage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS, cached_tokens=0),
            finish="tool_calls" if calls else "stop",
            raw_finish="tool_calls" if calls else "stop",
            raw=(),
        )

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=True,
            thinking=False,
            context_length=_CONTEXT_LENGTH,
            own_context_length=None,
            efforts=(),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("scripted"),)

    async def aclose(self) -> None:
        return None
