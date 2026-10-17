"""OpenAI Responses wire format (``POST /responses``), used by OpenAI and by OpenCode Go's responses route.

Calls are stateless (``store: false``): every request carries the whole history. An assistant turn that came from
the same model is replayed as the output items the server produced, encrypted reasoning included; any other turn
is rebuilt from its neutral fields.
"""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import StreamCodec, parse_event_object
from dotagent.llm.http import SseEvent
from dotagent.llm.provider_errors import StatusClassifier, WireError, error_from_stream, wire_error_from_object
from dotagent.llm.traits import ModelTraits, choose_effort
from dotagent.llm.types import (
    ChatRequest,
    FinishReason,
    ImageData,
    Message,
    ModelReply,
    StreamDelta,
    ToolCall,
    ToolSpec,
    Usage,
    arguments_for_wire,
    empty_usage,
)
from dotagent.llm.wire import (
    data_url,
    get_int,
    get_obj,
    get_objects,
    get_scalar_text,
    get_str,
    hoist_tool_images,
    non_blank,
    parse_tool_arguments,
    split_instructions,
    tool_call_id_of,
)

_REPLAY_TYPES: frozenset[str] = frozenset({"reasoning", "message", "function_call"})
_TERMINAL_EVENTS: frozenset[str] = frozenset({"response.completed", "response.incomplete", "response.failed"})
_EFFORT_NONE: str = "none"


class ResponsesOptions(FrozenModel):
    """How a provider differs in filling in the Responses format."""

    image_detail: str
    tool_output_images: bool


# --------------------------------------------------------------------------------------------- request


def _image_part(image: ImageData, options: ResponsesOptions) -> JsonObject:
    return {"type": "input_image", "image_url": data_url(image), "detail": options.image_detail}


def _user_item(message: Message, options: ResponsesOptions) -> JsonObject:
    parts: list[JsonValue] = []
    if message.content.strip() or not message.images:
        parts.append({"type": "input_text", "text": non_blank(message.content, "(empty message)")})
    parts.extend(_image_part(image, options) for image in message.images)
    return {"role": "user", "content": parts}


def _is_replayable(raw: Sequence[JsonObject]) -> bool:
    """Server items can be sent back as they are only when every reasoning item still carries its encrypted state."""
    for item in raw:
        kind: str = get_str(item, "type")
        if kind not in _REPLAY_TYPES:
            return False
        if kind == "reasoning" and not get_str(item, "encrypted_content"):
            return False
    return True


def _assistant_items(message: Message, origin: str) -> list[JsonObject]:
    if message.raw and message.raw_origin == origin and _is_replayable(message.raw):
        return list(message.raw)
    items: list[JsonObject] = []
    if message.content.strip():
        items.append({"role": "assistant", "content": message.content})
    items.extend(
        {
            "type": "function_call",
            "call_id": call.id,
            "name": call.name,
            "arguments": arguments_for_wire(call.arguments),
        }
        for call in message.tool_calls
    )
    return items


def _tool_item(message: Message, options: ResponsesOptions) -> JsonObject:
    text: str = non_blank(message.content, "(no output)")
    output: JsonValue = text
    if message.images:
        parts: list[JsonValue] = [{"type": "input_text", "text": text}]
        parts.extend(_image_part(image, options) for image in message.images)
        output = parts
    return {"type": "function_call_output", "call_id": tool_call_id_of(message), "output": output}


def _is_message_item(item: JsonObject) -> bool:
    return get_str(item, "type") == "message" or (get_str(item, "type") == "" and get_str(item, "role") == "assistant")


def prune_unpaired(items: Sequence[JsonObject]) -> list[JsonObject]:
    """Drop calls without results, results without calls, and reasoning that no longer precedes its item: reasoning
    is kept when the first item after it that is not reasoning is a message or a call, so a run of reasoning items
    before one is kept whole."""
    called: set[str] = {get_str(item, "call_id") for item in items if get_str(item, "type") == "function_call"}
    answered: set[str] = {get_str(item, "call_id") for item in items if get_str(item, "type") == "function_call_output"}
    paired: set[str] = called & answered
    kept: list[JsonObject] = [
        item
        for item in items
        if get_str(item, "type") not in ("function_call", "function_call_output") or get_str(item, "call_id") in paired
    ]
    # Read from the end: whether the next item that is not reasoning is a message or a call.
    reversed_result: list[JsonObject] = []
    leads_on: bool = False
    for item in reversed(kept):
        kind: str = get_str(item, "type")
        if kind == "reasoning":
            if leads_on:
                reversed_result.append(item)
            continue
        leads_on = _is_message_item(item) or kind == "function_call"
        reversed_result.append(item)
    return reversed_result[::-1]


def encode_input(messages: Sequence[Message], origin: str, options: ResponsesOptions) -> list[JsonObject]:
    prepared: Sequence[Message] = messages if options.tool_output_images else hoist_tool_images(messages)
    items: list[JsonObject] = []
    for message in prepared:
        if message.role == "system":
            if message.content.strip():
                items.append({"role": "developer", "content": message.content})
        elif message.role == "user":
            items.append(_user_item(message, options))
        elif message.role == "assistant":
            items.extend(_assistant_items(message, origin))
        else:
            items.append(_tool_item(message, options))
    return prune_unpaired(items)


def encode_tools(tools: Sequence[ToolSpec]) -> list[JsonObject]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": False,
        }
        for tool in tools
    ]


def build_responses_body(
    request: ChatRequest, traits: ModelTraits, origin: str, options: ResponsesOptions
) -> JsonObject:
    """The JSON object to POST. ``stream`` is the last key, which OpenCode's gateway relies on."""
    instructions, conversation = split_instructions(request.messages)
    label: str | None = choose_effort(traits.efforts, request.effort) if traits.reasoning else None
    body: JsonObject = {"model": request.model}
    if instructions:
        body["instructions"] = instructions
    body["input"] = list(encode_input(conversation, origin, options))
    if request.tools:
        body["tools"] = list(encode_tools(request.tools))
    body["store"] = False
    body["prompt_cache_key"] = request.session_key
    if traits.reasoning:
        body["include"] = ["reasoning.encrypted_content"]
    if label is not None:
        reasoning: JsonObject = {"effort": label}
        if label != _EFFORT_NONE:
            reasoning["summary"] = "auto"
        body["reasoning"] = reasoning
    if request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    if request.temperature is not None and traits.temperature and label in (None, _EFFORT_NONE):
        body["temperature"] = request.temperature
    body["stream"] = True
    return body


# ---------------------------------------------------------------------------------------------- stream


class ResponsesState(FrozenModel):
    """Everything accumulated from the event stream so far. ``status`` stays empty until a terminal event."""

    model: str
    items: dict[int, JsonObject]
    text: str
    thinking: str
    status: str
    stop_reason: str
    usage: Usage
    final_output: tuple[JsonObject, ...]


def initial_responses_state(model: str) -> ResponsesState:
    return ResponsesState(
        model=model,
        items={},
        text="",
        thinking="",
        status="",
        stop_reason="",
        usage=empty_usage(),
        final_output=(),
    )


def _usage_of(response: JsonObject) -> Usage:
    usage: JsonObject = get_obj(response, "usage")
    return Usage(
        input_tokens=get_int(usage, "input_tokens"),
        output_tokens=get_int(usage, "output_tokens"),
        cached_tokens=get_int(get_obj(usage, "input_tokens_details"), "cached_tokens"),
    )


def _with_text(state: ResponsesState, piece: str) -> tuple[ResponsesState, tuple[StreamDelta, ...]]:
    if not piece:
        return state, ()
    return state.model_copy(update={"text": state.text + piece}), (StreamDelta(kind="text", text=piece),)


def _with_thinking(state: ResponsesState, piece: str) -> tuple[ResponsesState, tuple[StreamDelta, ...]]:
    if not piece:
        return state, ()
    return state.model_copy(update={"thinking": state.thinking + piece}), (StreamDelta(kind="thinking", text=piece),)


def _terminal(
    state: ResponsesState, kind: str, data: JsonObject, context: JsonObject, classify: StatusClassifier
) -> ResponsesState:
    response: JsonObject = get_obj(data, "response")
    if kind == "response.failed":
        failure: JsonObject = get_obj(response, "error")
        wire: WireError = WireError(
            kind=get_str(failure, "type"),
            code=get_scalar_text(failure, "code"),
            message=get_str(failure, "message") or "the response failed without details",
            limit_name="",
        )
        raise error_from_stream(wire, context, classify)
    return state.model_copy(
        update={
            "status": kind.removeprefix("response."),
            "stop_reason": get_str(get_obj(response, "incomplete_details"), "reason"),
            "usage": _usage_of(response),
            "final_output": tuple(get_objects(response, "output")),
        }
    )


def fold_responses_event(
    state: ResponsesState, event: SseEvent, context: JsonObject, classify: StatusClassifier
) -> tuple[ResponsesState, tuple[StreamDelta, ...]]:
    """Fold one SSE event into the state; unknown event types are ignored so new server features do not break us."""
    if event.data.strip() == "[DONE]":
        return state, ()
    data: JsonObject = parse_event_object(event, context)
    kind: str = get_str(data, "type") or (event.event or "")
    if kind in ("", "error"):
        wire: WireError | None = wire_error_from_object(data)
        if wire is not None:
            raise error_from_stream(wire, context, classify)
        return state, ()
    if kind in ("response.output_text.delta", "response.refusal.delta"):
        return _with_text(state, get_str(data, "delta"))
    if kind == "response.reasoning_summary_text.delta":
        return _with_thinking(state, get_str(data, "delta"))
    if kind == "response.reasoning_summary_part.added" and state.thinking:
        return _with_thinking(state, "\n\n")
    if kind == "response.output_item.added" and state.text and get_str(get_obj(data, "item"), "type") == "message":
        return _with_text(state, "\n\n")
    if kind == "response.output_item.done":
        items: dict[int, JsonObject] = {**state.items, get_int(data, "output_index"): get_obj(data, "item")}
        return state.model_copy(update={"items": items}), ()
    if kind in _TERMINAL_EVENTS:
        return _terminal(state, kind, data, context, classify), ()
    return state, ()


def _keep(item: JsonObject) -> bool:
    kind: str = get_str(item, "type")
    if kind not in _REPLAY_TYPES:
        return False
    if kind == "function_call":
        return get_str(item, "status") in ("", "completed")
    return True


def _message_text(item: JsonObject) -> str:
    pieces: list[str] = []
    for part in get_objects(item, "content"):
        part_kind: str = get_str(part, "type")
        if part_kind == "output_text":
            pieces.append(get_str(part, "text"))
        elif part_kind == "refusal":
            pieces.append(get_str(part, "refusal"))
    return "".join(pieces)


def _reasoning_text(item: JsonObject) -> str:
    return "\n\n".join(text for summary in get_objects(item, "summary") if (text := get_str(summary, "text")))


def _tool_call_of(item: JsonObject) -> ToolCall:
    return ToolCall(
        id=get_str(item, "call_id"),
        name=get_str(item, "name"),
        arguments=parse_tool_arguments(get_str(item, "arguments")),
    )


def _finish_of(state: ResponsesState, has_calls: bool) -> FinishReason:
    if has_calls:
        return "tool_calls"
    if state.status == "completed":
        return "stop"
    if state.status == "incomplete" and state.stop_reason == "max_output_tokens":
        return "length"
    return "other"


def responses_reply(state: ResponsesState) -> ModelReply:
    """The finished reply. The terminal event's output list wins; otherwise the items finished one by one."""
    output: tuple[JsonObject, ...] = state.final_output or tuple(state.items[index] for index in sorted(state.items))
    kept: tuple[JsonObject, ...] = tuple(item for item in output if _keep(item))
    calls: tuple[ToolCall, ...] = tuple(
        _tool_call_of(item) for item in kept if get_str(item, "type") == "function_call"
    )
    texts: list[str] = [_message_text(item) for item in kept if get_str(item, "type") == "message"]
    thoughts: list[str] = [_reasoning_text(item) for item in kept if get_str(item, "type") == "reasoning"]
    return ModelReply(
        model=state.model,
        text="\n\n".join(text for text in texts if text) or state.text,
        thinking="\n\n".join(text for text in thoughts if text) or state.thinking,
        tool_calls=calls,
        usage=state.usage,
        finish=_finish_of(state, bool(calls)),
        raw_finish=state.stop_reason or state.status,
        raw=kept,
    )


def responses_codec(classify: StatusClassifier) -> StreamCodec[ResponsesState]:
    return StreamCodec(
        initial=initial_responses_state,
        fold=lambda state, event, context: fold_responses_event(state, event, context, classify),
        is_final=lambda state: state.status != "",
        accepts_eof=lambda state: False,
        to_reply=responses_reply,
    )
