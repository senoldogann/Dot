"""Chat Completions wire format (``POST /chat/completions``): OpenCode Go's chat route and OpenAI-style servers."""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.core.errors import ProviderProtocolError
from dotagent.core.ids import new_id
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import StreamCodec, parse_event_object
from dotagent.llm.http import SseEvent
from dotagent.llm.provider_errors import StatusClassifier, WireError, error_from_stream, wire_error_from_object
from dotagent.llm.traits import ModelTraits, choose_effort
from dotagent.llm.types import (
    ChatRequest,
    FinishReason,
    Message,
    ModelReply,
    StreamDelta,
    ToolCall,
    Usage,
    arguments_for_wire,
    empty_usage,
)
from dotagent.llm.wire import (
    data_url,
    function_tools,
    get_int,
    get_obj,
    get_objects,
    get_str,
    hoist_tool_images,
    non_blank,
    parse_tool_arguments,
    split_instructions,
    tool_call_id_of,
)

_EFFORT_NONE: str = "none"


class ChatOptions(FrozenModel):
    """How a server differs in filling in the Chat Completions format."""

    max_tokens_field: str
    request_usage: bool
    replay_reasoning: bool
    cache_key_in_body: bool


# --------------------------------------------------------------------------------------------- request


def _user_message(message: Message) -> JsonObject:
    if not message.images:
        return {"role": "user", "content": non_blank(message.content, "(empty message)")}
    parts: list[JsonValue] = []
    if message.content.strip():
        parts.append({"type": "text", "text": message.content})
    parts.extend({"type": "image_url", "image_url": {"url": data_url(image)}} for image in message.images)
    return {"role": "user", "content": parts}


def _assistant_message(message: Message, options: ChatOptions) -> JsonObject:
    entry: JsonObject = {"role": "assistant"}
    if message.tool_calls and not message.content.strip():
        entry["content"] = None
    else:
        entry["content"] = non_blank(message.content, "(no text)")
    if options.replay_reasoning:
        entry["reasoning_content"] = message.thinking or ""
    if message.tool_calls:
        entry["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": arguments_for_wire(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return entry


def encode_messages(messages: Sequence[Message], options: ChatOptions) -> list[JsonObject]:
    wire: list[JsonObject] = []
    for message in hoist_tool_images(messages):
        if message.role == "system":
            wire.append({"role": "system", "content": non_blank(message.content, "(empty)")})
        elif message.role == "user":
            wire.append(_user_message(message))
        elif message.role == "assistant":
            wire.append(_assistant_message(message, options))
        else:
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id_of(message),
                    "content": non_blank(message.content, "(no output)"),
                }
            )
    return wire


def build_chat_body(request: ChatRequest, traits: ModelTraits, options: ChatOptions) -> JsonObject:
    """The JSON object to POST. ``stream`` is the last key, which OpenCode's gateway relies on."""
    instructions, conversation = split_instructions(request.messages)
    label: str | None = choose_effort(traits.efforts, request.effort) if traits.reasoning else None
    messages: list[JsonValue] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    messages.extend(encode_messages(conversation, options))
    body: JsonObject = {"model": request.model, "messages": messages}
    if request.tools:
        body["tools"] = list(function_tools(request.tools))
    if label is not None:
        body["reasoning_effort"] = label
    if request.max_output_tokens is not None:
        body[options.max_tokens_field] = request.max_output_tokens
    if request.temperature is not None and traits.temperature and label in (None, _EFFORT_NONE):
        body["temperature"] = request.temperature
    if options.cache_key_in_body:
        body["prompt_cache_key"] = request.session_key
    if options.request_usage:
        body["stream_options"] = {"include_usage": True}
    body["stream"] = True
    return body


# ---------------------------------------------------------------------------------------------- stream


class PartialCall(FrozenModel):
    """A tool call being assembled from deltas; ``index`` is the server's slot number."""

    index: int
    id: str
    name: str
    arguments: str


class ChatState(FrozenModel):
    """Everything accumulated from the event stream so far. ``done`` becomes true at ``[DONE]``."""

    model: str
    text: str
    thinking: str
    calls: tuple[PartialCall, ...]
    usage: Usage
    finish: str
    done: bool


def initial_chat_state(model: str) -> ChatState:
    return ChatState(model=model, text="", thinking="", calls=(), usage=empty_usage(), finish="", done=False)


def _usage_of(usage: JsonObject) -> Usage:
    cached: int = (
        get_int(get_obj(usage, "prompt_tokens_details"), "cached_tokens")
        or get_int(usage, "cached_tokens")
        or get_int(usage, "prompt_cache_hit_tokens")
    )
    return Usage(
        input_tokens=get_int(usage, "prompt_tokens"),
        output_tokens=get_int(usage, "completion_tokens"),
        cached_tokens=cached,
    )


def _arguments_piece(function: JsonObject) -> str:
    raw: JsonValue = function.get("arguments")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return dumps_compact(raw)
    return ""


def _slot_for(calls: tuple[PartialCall, ...], index: int, call_id: str) -> int | None:
    """The slot a delta belongs to: the latest one with its index, unless it announces a different call id."""
    for position in range(len(calls) - 1, -1, -1):
        slot: PartialCall = calls[position]
        if slot.index == index:
            if call_id and slot.id and slot.id != call_id:
                return None
            return position
    return None


def _fold_call(calls: tuple[PartialCall, ...], entry: JsonObject) -> tuple[PartialCall, ...]:
    function: JsonObject = get_obj(entry, "function")
    index: int = get_int(entry, "index")
    call_id: str = get_str(entry, "id")
    name: str = get_str(function, "name")
    piece: str = _arguments_piece(function)
    position: int | None = _slot_for(calls, index, call_id)
    if position is None:
        return (*calls, PartialCall(index=index, id=call_id, name=name, arguments=piece))
    current: PartialCall = calls[position]
    updated: PartialCall = PartialCall(
        index=index, id=current.id or call_id, name=current.name or name, arguments=current.arguments + piece
    )
    return (*calls[:position], updated, *calls[position + 1 :])


def fold_chat_event(
    state: ChatState, event: SseEvent, context: JsonObject, classify: StatusClassifier
) -> tuple[ChatState, tuple[StreamDelta, ...]]:
    """Fold one SSE chunk into the state; zero-choice chunks (usage only) are normal."""
    if event.data.strip() == "[DONE]":
        return state.model_copy(update={"done": True}), ()
    data: JsonObject = parse_event_object(event, context)
    failure: WireError | None = wire_error_from_object(data)
    if failure is not None:
        raise error_from_stream(failure, context, classify)
    choices: list[JsonObject] = get_objects(data, "choices")
    choice: JsonObject = choices[0] if choices else {}
    delta: JsonObject = get_obj(choice, "delta")
    deltas: list[StreamDelta] = []
    text: str = state.text
    thinking: str = state.thinking
    reasoning: str = get_str(delta, "reasoning_content") or get_str(delta, "reasoning")
    if reasoning:
        thinking += reasoning
        deltas.append(StreamDelta(kind="thinking", text=reasoning))
    content: str = get_str(delta, "content")
    if content:
        text += content
        deltas.append(StreamDelta(kind="text", text=content))
    calls: tuple[PartialCall, ...] = state.calls
    for entry in get_objects(delta, "tool_calls"):
        calls = _fold_call(calls, entry)
    usage_object: JsonObject = get_obj(data, "usage") or get_obj(choice, "usage")
    new_state: ChatState = state.model_copy(
        update={
            "text": text,
            "thinking": thinking,
            "calls": calls,
            "usage": _usage_of(usage_object) if usage_object else state.usage,
            "finish": get_str(choice, "finish_reason") or state.finish,
        }
    )
    return new_state, tuple(deltas)


def _finish_of(state: ChatState, has_calls: bool) -> FinishReason:
    if has_calls:
        return "tool_calls"
    if state.finish in ("", "stop"):
        return "stop"
    if state.finish == "length":
        return "length"
    return "other"


def _tool_calls_of(slots: tuple[PartialCall, ...]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    used: set[str] = set()
    for slot in slots:
        if not slot.name:
            raise ProviderProtocolError(
                "stream carried a tool call without a function name",
                {"call_id": slot.id, "arguments": slot.arguments[:300]},
            )
        identifier: str = slot.id if slot.id and slot.id not in used else new_id("call")
        used.add(identifier)
        calls.append(ToolCall(id=identifier, name=slot.name, arguments=parse_tool_arguments(slot.arguments)))
    return tuple(calls)


def chat_reply(state: ChatState) -> ModelReply:
    calls: tuple[ToolCall, ...] = _tool_calls_of(state.calls)
    return ModelReply(
        model=state.model,
        text=state.text,
        thinking=state.thinking,
        tool_calls=calls,
        usage=state.usage,
        finish=_finish_of(state, bool(calls)),
        raw_finish=state.finish,
        raw=(),
    )


def chat_codec(classify: StatusClassifier) -> StreamCodec[ChatState]:
    return StreamCodec(
        initial=initial_chat_state,
        fold=lambda state, event, context: fold_chat_event(state, event, context, classify),
        is_final=lambda state: state.done,
        accepts_eof=lambda state: state.finish != "",
        to_reply=chat_reply,
    )
