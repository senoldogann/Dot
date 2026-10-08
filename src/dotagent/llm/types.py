"""Provider-neutral chat types and the provider interface."""

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from pydantic import JsonValue

from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import FrozenModel, JsonObject

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "other"]
# One scale for every provider: ``off`` and ``on`` are the two ends of a plain thinking switch, the rest are levels.
Effort = Literal["off", "on", "minimal", "low", "medium", "high", "xhigh", "max"]

INVALID_ARGUMENTS_KEY: str = "__invalid_arguments__"


class ImageData(FrozenModel):
    media_type: str
    data_b64: str


class ToolCall(FrozenModel):
    id: str
    name: str
    arguments: JsonObject


class Message(FrozenModel):
    """One conversation turn. ``tool`` messages answer a ``ToolCall`` and may carry screenshots."""

    role: Role
    content: str
    images: tuple[ImageData, ...]
    tool_calls: tuple[ToolCall, ...]
    tool_call_id: str | None
    tool_name: str | None
    thinking: str | None
    raw: tuple[JsonObject, ...]
    raw_origin: str | None


class ToolSpec(FrozenModel):
    name: str
    description: str
    parameters: JsonObject


class Usage(FrozenModel):
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class ChatRequest(FrozenModel):
    """One model call. ``session_key`` is stable for a conversation and lets providers reuse their prompt cache."""

    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    temperature: float | None
    max_output_tokens: int | None
    effort: Effort
    session_key: str


class ModelReply(FrozenModel):
    model: str
    text: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    finish: FinishReason
    raw_finish: str
    raw: tuple[JsonObject, ...]


class StreamDelta(FrozenModel):
    """Incremental output. ``reset`` tells listeners to discard what was streamed so far (retry)."""

    kind: Literal["text", "thinking", "reset"]
    text: str


class ModelInfo(FrozenModel):
    """What a model offers. ``context_length`` is the number of tokens one request (prompt and reply) can hold, ``None``
    when the provider does not say. ``efforts`` are its reasoning levels, lowest first; fewer than two means no choice."""

    id: str
    vision: bool
    tools: bool
    thinking: bool
    context_length: int | None
    efforts: tuple[Effort, ...]


DeltaSink = Callable[[StreamDelta], Awaitable[None]]


class ChatProvider(Protocol):
    """A connector to one model service."""

    @property
    def name(self) -> str: ...

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply: ...

    async def model_info(self, model: str) -> ModelInfo: ...

    async def list_models(self) -> tuple[ModelInfo, ...]: ...

    async def aclose(self) -> None: ...


def system_message(text: str) -> Message:
    return Message(
        role="system",
        content=text,
        images=(),
        tool_calls=(),
        tool_call_id=None,
        tool_name=None,
        thinking=None,
        raw=(),
        raw_origin=None,
    )


def user_message(text: str, images: tuple[ImageData, ...]) -> Message:
    return Message(
        role="user",
        content=text,
        images=images,
        tool_calls=(),
        tool_call_id=None,
        tool_name=None,
        thinking=None,
        raw=(),
        raw_origin=None,
    )


def assistant_message(text: str, thinking: str | None, tool_calls: tuple[ToolCall, ...]) -> Message:
    return Message(
        role="assistant",
        content=text,
        images=(),
        tool_calls=tool_calls,
        tool_call_id=None,
        tool_name=None,
        thinking=thinking,
        raw=(),
        raw_origin=None,
    )


def assistant_from_reply(reply: ModelReply, origin: str) -> Message:
    """The assistant turn exactly as a provider produced it, keeping opaque replay state."""
    return Message(
        role="assistant",
        content=reply.text,
        images=(),
        tool_calls=reply.tool_calls,
        tool_call_id=None,
        tool_name=None,
        thinking=reply.thinking or None,
        raw=reply.raw,
        raw_origin=origin if reply.raw else None,
    )


def tool_message(call: ToolCall, content: str, images: tuple[ImageData, ...]) -> Message:
    return Message(
        role="tool",
        content=content,
        images=images,
        tool_calls=(),
        tool_call_id=call.id,
        tool_name=call.name,
        thinking=None,
        raw=(),
        raw_origin=None,
    )


def empty_usage() -> Usage:
    return Usage(input_tokens=0, output_tokens=0, cached_tokens=0)


def invalid_arguments(raw: str) -> JsonObject:
    """Stand-in arguments for a tool call whose JSON the model got wrong; the executor reports it back."""
    return {INVALID_ARGUMENTS_KEY: raw}


def invalid_arguments_text(arguments: JsonObject) -> str | None:
    """The broken JSON text when ``arguments`` is a stand-in made by :func:`invalid_arguments`."""
    raw: JsonValue = arguments.get(INVALID_ARGUMENTS_KEY)
    return raw if isinstance(raw, str) and len(arguments) == 1 else None


def arguments_for_wire(arguments: JsonObject) -> str:
    """Tool-call arguments as JSON text; broken arguments go back exactly as the model wrote them."""
    broken: str | None = invalid_arguments_text(arguments)
    return broken if broken is not None else dumps_compact(arguments)
