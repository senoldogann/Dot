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
# The parts of a streamed reasoning detail that arrive in pieces and are joined; its other fields are replaced.
_DETAIL_TEXTS: frozenset[str] = frozenset({"text", "summary", "data"})
# The details whose words are shown as the model's thinking, by type, and the field that holds them.
_DETAIL_WORDS: dict[str, str] = {"reasoning.text": "text", "reasoning.summary": "summary"}


class ChatOptions(FrozenModel):
    """How a server differs in filling in the Chat Completions format. ``reasoning_object``: the reasoning level goes as
    a ``reasoning`` object (OpenRouter's: ``{"effort": label}``, or ``{"enabled": ...}`` for a model whose reasoning is
    a switch) instead of ``reasoning_effort``. ``replay_details``: a reply keeps the ``reasoning_details`` the server sent
    (and takes the words of its thinking from them when no string carries them), and an assistant turn of the same
    model carries them back, unchanged, as OpenRouter asks beside tool calls; without it they are not read."""

    max_tokens_field: str
    request_usage: bool
    replay_reasoning: bool
    cache_key_in_body: bool
    reasoning_object: bool
    replay_details: bool


# --------------------------------------------------------------------------------------------- request


def _user_message(message: Message) -> JsonObject:
    if not message.images:
        return {"role": "user", "content": non_blank(message.content, "(empty message)")}
    parts: list[JsonValue] = []
    if message.content.strip():
        parts.append({"type": "text", "text": message.content})
    parts.extend({"type": "image_url", "image_url": {"url": data_url(image)}} for image in message.images)
    return {"role": "user", "content": parts}


def _assistant_message(message: Message, options: ChatOptions, origin: str) -> JsonObject:
    entry: JsonObject = {"role": "assistant"}
    if message.tool_calls and not message.content.strip():
        entry["content"] = None
    else:
        entry["content"] = non_blank(message.content, "(no text)")
    if options.replay_reasoning:
        entry["reasoning_content"] = message.thinking or ""
    if options.replay_details and message.raw and message.raw_origin == origin:
        entry["reasoning_details"] = list(message.raw)
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


def encode_messages(messages: Sequence[Message], options: ChatOptions, origin: str) -> list[JsonObject]:
    """The conversation in the wire format; ``origin`` (``provider:model``) names the model whose own assistant turns
    may carry their replay state back."""
    wire: list[JsonObject] = []
    for message in hoist_tool_images(messages):
        if message.role == "system":
            wire.append({"role": "system", "content": non_blank(message.content, "(empty)")})
        elif message.role == "user":
            wire.append(_user_message(message))
        elif message.role == "assistant":
            wire.append(_assistant_message(message, options, origin))
        else:
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id_of(message),
                    "content": non_blank(message.content, "(no output)"),
                }
            )
    return wire


def _reasoning_object(traits: ModelTraits, label: str | None, request: ChatRequest) -> JsonObject | None:
    """OpenRouter's ``reasoning`` object: the level's label, the switch of a model that offers no levels, or none."""
    if label is not None:
        return {"effort": label}
    if traits.thinking_toggle:
        return {"enabled": request.effort != "off"}
    return None


def build_chat_body(request: ChatRequest, traits: ModelTraits, options: ChatOptions, origin: str) -> JsonObject:
    """The JSON object to POST. ``stream`` is the last key, which OpenCode's gateway relies on."""
    instructions, conversation = split_instructions(request.messages)
    label: str | None = choose_effort(traits.efforts, request.effort) if traits.reasoning else None
    messages: list[JsonValue] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    messages.extend(encode_messages(conversation, options, origin))
    body: JsonObject = {"model": request.model, "messages": messages}
    if request.tools:
        body["tools"] = list(function_tools(request.tools))
    if options.reasoning_object:
        reasoning: JsonObject | None = _reasoning_object(traits, label, request)
        if reasoning is not None:
            body["reasoning"] = reasoning
    elif label is not None:
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
    """Everything accumulated from the event stream so far. ``done`` becomes true at ``[DONE]``; ``details`` are the
    reasoning details the server sent (OpenRouter's), joined into whole ones (:func:`merge_details`), when the codec
    sends them back (``ChatOptions.replay_details``)."""

    model: str
    text: str
    thinking: str
    calls: tuple[PartialCall, ...]
    details: tuple[JsonObject, ...]
    usage: Usage
    finish: str
    done: bool


def initial_chat_state(model: str) -> ChatState:
    return ChatState(
        model=model, text="", thinking="", calls=(), details=(), usage=empty_usage(), finish="", done=False
    )


def _continues(last: JsonObject, piece: JsonObject) -> bool:
    """Whether a streamed piece goes on with the last detail: the same type and index, and not another detail's id."""
    ids: tuple[JsonValue, JsonValue] = (last.get("id"), piece.get("id"))
    same_id: bool = not (ids[0] and ids[1]) or ids[0] == ids[1]
    return last.get("type") == piece.get("type") and last.get("index") == piece.get("index") and same_id


def merge_details(kept: tuple[JsonObject, ...], pieces: Sequence[JsonObject]) -> tuple[JsonObject, ...]:
    """Streamed reasoning details joined into whole ones, as a reply that is not streamed holds them: a piece that goes
    on with the last detail (the same type and index, no other id) adds its text (``text``, ``summary``, ``data``) to
    it, and its other fields that say something (a signature, an id) replace the last one's; any other piece starts a
    new detail."""
    merged: list[JsonObject] = list(kept)
    for piece in pieces:
        last: JsonObject | None = merged[-1] if merged else None
        if last is None or not _continues(last, piece):
            merged.append(dict(piece))
            continue
        joined: JsonObject = dict(last)
        for key, value in piece.items():
            before: JsonValue = joined.get(key)
            if key in _DETAIL_TEXTS and isinstance(value, str) and isinstance(before, str):
                joined[key] = before + value
            elif value is not None and value != "":
                joined[key] = value
        merged[-1] = joined
    return tuple(merged)


def _detail_words(pieces: Sequence[JsonObject]) -> str:
    """The readable words of reasoning details: the text of a thought or of its summary; an encrypted one has none."""
    words: list[str] = []
    for piece in pieces:
        field: str | None = _DETAIL_WORDS.get(get_str(piece, "type"))
        if field is not None:
            words.append(get_str(piece, field))
    return "".join(words)


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
    state: ChatState, event: SseEvent, context: JsonObject, classify: StatusClassifier, options: ChatOptions
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
    pieces: list[JsonObject] = get_objects(delta, "reasoning_details") if options.replay_details else []
    # The words of the thinking come once: as a string where the server sends one, else from the details.
    reasoning: str = get_str(delta, "reasoning_content") or get_str(delta, "reasoning") or _detail_words(pieces)
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
            "details": merge_details(state.details, pieces) if pieces else state.details,
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
        raw=state.details,
    )


def chat_codec(classify: StatusClassifier, options: ChatOptions) -> StreamCodec[ChatState]:
    """The stream codec of a server that fills in the format as ``options`` say (the body's options)."""
    return StreamCodec(
        initial=initial_chat_state,
        fold=lambda state, event, context: fold_chat_event(state, event, context, classify, options),
        is_final=lambda state: state.done,
        accepts_eof=lambda state: state.finish != "",
        to_reply=chat_reply,
    )
