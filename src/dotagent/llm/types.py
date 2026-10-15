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
# How fast a model writes its answer (item 223): ``fast`` is the faster output some Claude models offer at a higher
# price (``llm/anthropic.py``), ``standard`` every other model's.
Speed = Literal["standard", "fast"]
# Where a model's window comes from (item 250): its provider's API names it, Dot's table of the windows its provider
# publishes holds it, Dot assumes it for a model neither names, or it is the window Dot asks Ollama for (num_ctx).
WindowSource = Literal["provider", "published", "assumed", "asked"]

INVALID_ARGUMENTS_KEY: str = "__invalid_arguments__"

# The providers a model reference ("<provider>:<model>") can name.
KNOWN_PROVIDERS: tuple[str, ...] = ("ollama", "openai", "opencode_go", "anthropic")


def split_model_ref(text: str) -> tuple[str, str] | None:
    """The provider and the model of ``<provider>:<model>``, split at the first colon (model names may contain colons,
    e.g. ``qwen3:8b``), or ``None`` when ``text`` is not one or names a provider Dot does not know."""
    provider, separator, model = text.strip().partition(":")
    if separator == "" or model == "" or provider not in KNOWN_PROVIDERS:
        return None
    return provider, model


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
    """One model call. ``session_key`` is stable for a conversation and lets providers reuse their prompt cache;
    ``speed`` is ``fast`` only for a model that offers it (:func:`dotagent.llm.router.offers_fast_mode`).
    ``cache_mark`` is the index in ``messages`` of the last message the conversation's next call sends unchanged, when
    the caller knows it (item 237); a provider whose cache looks back only a few blocks from a mark marks it there."""

    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    temperature: float | None
    max_output_tokens: int | None
    effort: Effort
    speed: Speed
    session_key: str
    cache_mark: int | None


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
    when the provider does not say; ``own_context_length`` is the model's own window where Dot asks for less of it (an
    Ollama model past ``num_ctx``), else ``None``; ``window_source`` says where ``context_length`` comes from, ``None``
    with it. ``efforts`` are its reasoning levels, lowest first; fewer than two means no choice. ``free``: the provider
    serves it at no cost."""

    id: str
    vision: bool
    tools: bool
    thinking: bool
    context_length: int | None
    own_context_length: int | None
    window_source: WindowSource | None
    efforts: tuple[Effort, ...]
    free: bool


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
