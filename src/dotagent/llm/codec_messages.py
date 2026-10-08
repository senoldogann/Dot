"""Anthropic's Messages wire format (``POST /messages``): the Anthropic API, and OpenCode Go's messages route.

An assistant turn that came from the same model is replayed as the content blocks the server produced (thinking
signatures included); any other turn is rebuilt from its neutral fields. A screenshot returned by a tool travels inside
its ``tool_result`` block.
"""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.core.errors import ProviderProtocolError
from dotagent.core.ids import new_id
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import StreamCodec, parse_event_object
from dotagent.llm.http import SseEvent
from dotagent.llm.provider_errors import StatusClassifier, WireError, error_from_stream, wire_error_from_object
from dotagent.llm.traits import ModelTraits, choose_effort, label_rank
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
    empty_usage,
)
from dotagent.llm.wire import (
    get_int,
    get_obj,
    get_str,
    non_blank,
    parse_tool_arguments,
    split_instructions,
    tool_call_id_of,
)

_THINKING_BLOCKS: frozenset[str] = frozenset({"thinking", "redacted_thinking"})


class MessagesOptions(FrozenModel):
    """How a server differs in filling in the Messages format.

    ``adaptive_thinking`` (Anthropic): a model that reasons gets adaptive thinking with its summary shown and the
    reasoning level as ``output_config.effort``, or thinking turned off at the lowest level of a model that allows it;
    a replayed thinking block whose earlier context changed is dropped by the server instead of failing the request
    (``block_binding``, which needs the ``thinking-binding-controls-2026-08-01`` beta header). Without it a model with
    a thinking switch gets only the switch (OpenCode Go). ``prompt_cache``: the server caches the prompt up to its last
    block, so the next step of a run reads it back at a fraction of the price. ``default_max_tokens``: the output limit
    of a request that names none (``None``: the model's own); never more than the model's own.
    """

    adaptive_thinking: bool
    prompt_cache: bool
    default_max_tokens: int | None


# --------------------------------------------------------------------------------------------- request


def _text_block(text: str) -> JsonObject:
    return {"type": "text", "text": text}


def _image_block(image: ImageData) -> JsonObject:
    return {"type": "image", "source": {"type": "base64", "media_type": image.media_type, "data": image.data_b64}}


def _user_blocks(message: Message) -> list[JsonObject]:
    blocks: list[JsonObject] = []
    if message.content.strip() or not message.images:
        blocks.append(_text_block(non_blank(message.content, "(empty message)")))
    blocks.extend(_image_block(image) for image in message.images)
    return blocks


def _assistant_blocks(message: Message, origin: str) -> list[JsonObject]:
    if message.raw and message.raw_origin == origin:
        return list(message.raw)
    blocks: list[JsonObject] = []
    if message.content.strip():
        blocks.append(_text_block(message.content))
    blocks.extend(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments} for call in message.tool_calls
    )
    return blocks or [_text_block("(no text)")]


def _tool_result_block(message: Message) -> JsonObject:
    """A tool result. Its screenshots sit inside it, where the format puts them: a picture placed beside the result in
    the same turn never reaches some of the models behind the gateway."""
    text: str = non_blank(message.content, "(no output)")
    pieces: list[JsonValue] = [_text_block(text), *(_image_block(image) for image in message.images)]
    content: JsonValue = pieces if message.images else text
    return {"type": "tool_result", "tool_use_id": tool_call_id_of(message), "content": content}


def _turn_of(message: Message, origin: str) -> tuple[str, list[JsonObject]]:
    if message.role == "assistant":
        return "assistant", _assistant_blocks(message, origin)
    if message.role == "tool":
        return "user", [_tool_result_block(message)]
    return "user", _user_blocks(message)


def _tool_results_first(blocks: Sequence[JsonObject]) -> list[JsonObject]:
    """The API requires the results of a tool-use turn to open the user message that answers it."""
    return sorted(blocks, key=lambda block: 0 if get_str(block, "type") == "tool_result" else 1)


def encode_messages(messages: Sequence[Message], origin: str) -> list[JsonObject]:
    """Neutral messages as alternating user/assistant turns; neighbours with the same role are merged."""
    turns: list[tuple[str, list[JsonObject]]] = []
    for message in messages:
        role, blocks = _turn_of(message, origin)
        if turns and turns[-1][0] == role:
            turns[-1] = (role, [*turns[-1][1], *blocks])
        else:
            turns.append((role, blocks))
    return [{"role": role, "content": list(_tool_results_first(blocks))} for role, blocks in turns]


def encode_tools(tools: Sequence[ToolSpec]) -> list[JsonObject]:
    return [{"name": tool.name, "description": tool.description, "input_schema": tool.parameters} for tool in tools]


def _max_tokens(request: ChatRequest, traits: ModelTraits, options: MessagesOptions) -> int:
    asked: int | None = request.max_output_tokens
    wanted: int | None = asked if asked is not None else options.default_max_tokens
    return traits.max_output_tokens if wanted is None else min(wanted, traits.max_output_tokens)


def _without_thinking(message: Message) -> Message:
    """The message without the thinking blocks it would be replayed with. A request that turns thinking off sends none
    back: with thinking off, a model may refuse one whose earlier context changed. A turn left with no block is rebuilt
    from its neutral fields."""
    kept: tuple[JsonObject, ...] = tuple(
        block for block in message.raw if get_str(block, "type") not in _THINKING_BLOCKS
    )
    return message.model_copy(update={"raw": kept})


def _adaptive_fields(label: str | None) -> JsonObject:
    """Adaptive thinking with its summary shown, at the level ``label`` (``None``: the model's default level)."""
    thinking: JsonObject = {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    return {"thinking": thinking} if label is None else {"thinking": thinking, "output_config": {"effort": label}}


def build_messages_body(request: ChatRequest, traits: ModelTraits, origin: str, options: MessagesOptions) -> JsonObject:
    """The JSON object to POST. ``stream`` is the last key, which OpenCode's gateway relies on."""
    instructions, conversation = split_instructions(request.messages)
    adaptive: bool = options.adaptive_thinking and traits.reasoning
    label: str | None = choose_effort(traits.efforts, request.effort) if adaptive else None
    thinking_off: bool = label is not None and label_rank(label) == 0
    replayed: list[Message] = [_without_thinking(message) for message in conversation] if thinking_off else conversation
    body: JsonObject = {"model": request.model, "max_tokens": _max_tokens(request, traits, options)}
    if options.prompt_cache:
        body["cache_control"] = {"type": "ephemeral"}
    if instructions:
        body["system"] = instructions
    body["messages"] = list(encode_messages(replayed, origin))
    if request.tools:
        body["tools"] = list(encode_tools(request.tools))
    if traits.thinking_toggle:
        body["thinking"] = {"type": "disabled" if request.effort == "off" else "adaptive"}
    elif thinking_off:
        body["thinking"] = {"type": "disabled"}
    elif adaptive:
        body.update(_adaptive_fields(label))
    if request.temperature is not None and traits.temperature:
        body["temperature"] = request.temperature
    body["stream"] = True
    return body


# ---------------------------------------------------------------------------------------------- stream


class BlockState(FrozenModel):
    """One content block being assembled. ``json_text`` is a tool call's input as streamed."""

    kind: str
    text: str
    signature: str
    data: str
    call_id: str
    name: str
    json_text: str


class MessagesState(FrozenModel):
    """Everything accumulated from the event stream so far. ``stopped`` becomes true at ``message_stop``."""

    model: str
    blocks: dict[int, BlockState]
    usage: Usage
    stop_reason: str
    stopped: bool


def initial_messages_state(model: str) -> MessagesState:
    return MessagesState(model=model, blocks={}, usage=empty_usage(), stop_reason="", stopped=False)


def _merge_usage(previous: Usage, usage: JsonObject) -> Usage:
    """Later usage objects are cumulative; a field they leave out keeps its earlier value."""
    cache_read: int = get_int(usage, "cache_read_input_tokens")
    total_input: int = get_int(usage, "input_tokens") + cache_read + get_int(usage, "cache_creation_input_tokens")
    return Usage(
        input_tokens=total_input or previous.input_tokens,
        output_tokens=get_int(usage, "output_tokens") or previous.output_tokens,
        cached_tokens=cache_read or previous.cached_tokens,
    )


def _started(block: JsonObject) -> BlockState:
    kind: str = get_str(block, "type")
    return BlockState(
        kind=kind,
        text=get_str(block, "text") or get_str(block, "thinking"),
        signature=get_str(block, "signature"),
        data=get_str(block, "data"),
        call_id=get_str(block, "id"),
        name=get_str(block, "name"),
        json_text="",
    )


def _block_delta(block: BlockState, delta: JsonObject) -> tuple[BlockState, tuple[StreamDelta, ...]]:
    """Apply one block delta; delta types this client does not know (citations, ...) are skipped."""
    kind: str = get_str(delta, "type")
    if kind == "text_delta":
        piece: str = get_str(delta, "text")
        return block.model_copy(update={"text": block.text + piece}), (StreamDelta(kind="text", text=piece),)
    if kind == "thinking_delta":
        piece = get_str(delta, "thinking")
        return block.model_copy(update={"text": block.text + piece}), (StreamDelta(kind="thinking", text=piece),)
    if kind == "signature_delta":
        return block.model_copy(update={"signature": block.signature + get_str(delta, "signature")}), ()
    if kind == "input_json_delta":
        return block.model_copy(update={"json_text": block.json_text + get_str(delta, "partial_json")}), ()
    return block, ()


def fold_messages_event(
    state: MessagesState, event: SseEvent, context: JsonObject, classify: StatusClassifier
) -> tuple[MessagesState, tuple[StreamDelta, ...]]:
    """Fold one SSE event into the state; ``ping`` and block-stop events carry nothing we need."""
    data: JsonObject = parse_event_object(event, context)
    kind: str = get_str(data, "type") or (event.event or "")
    if kind == "error":
        failure: WireError | None = wire_error_from_object(data)
        if failure is None:
            raise ProviderProtocolError("error event carries no error description", context)
        raise error_from_stream(failure, context, classify)
    if kind == "message_start":
        usage: JsonObject = get_obj(get_obj(data, "message"), "usage")
        return state.model_copy(update={"usage": _merge_usage(state.usage, usage)}), ()
    if kind == "content_block_start":
        block: BlockState = _started(get_obj(data, "content_block"))
        blocks: dict[int, BlockState] = {**state.blocks, get_int(data, "index"): block}
        opening: tuple[StreamDelta, ...] = (
            (StreamDelta(kind="thinking" if block.kind == "thinking" else "text", text=block.text),)
            if block.text and block.kind in ("text", "thinking")
            else ()
        )
        return state.model_copy(update={"blocks": blocks}), opening
    if kind == "content_block_delta":
        index: int = get_int(data, "index")
        current: BlockState | None = state.blocks.get(index)
        if current is None:
            raise ProviderProtocolError("delta for a content block that was never started", {**context, "index": index})
        changed, deltas = _block_delta(current, get_obj(data, "delta"))
        return state.model_copy(update={"blocks": {**state.blocks, index: changed}}), deltas
    if kind == "message_delta":
        return state.model_copy(
            update={
                "usage": _merge_usage(state.usage, get_obj(data, "usage")),
                "stop_reason": get_str(get_obj(data, "delta"), "stop_reason") or state.stop_reason,
            }
        ), ()
    if kind == "message_stop":
        return state.model_copy(update={"stopped": True}), ()
    return state, ()


def _tool_call_of(block: BlockState) -> ToolCall:
    return ToolCall(
        id=block.call_id or new_id("call"), name=block.name, arguments=parse_tool_arguments(block.json_text)
    )


def _raw_block(block: BlockState, call: ToolCall | None) -> JsonObject | None:
    """The block as the server would have sent it whole; empty text and thinking are left out (the API rejects them)."""
    if block.kind == "text":
        return _text_block(block.text) if block.text else None
    if block.kind == "thinking":
        if not block.text and not block.signature:
            return None
        return {"type": "thinking", "thinking": block.text, "signature": block.signature}
    if block.kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    if block.kind == "tool_use" and call is not None:
        return {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
    return None


def _finish_of(stop_reason: str, has_calls: bool) -> FinishReason:
    if has_calls:
        return "tool_calls"
    if stop_reason in ("", "end_turn", "stop_sequence"):
        return "stop"
    if stop_reason in ("max_tokens", "model_context_window_exceeded"):
        return "length"
    return "other"


def messages_reply(state: MessagesState) -> ModelReply:
    ordered: list[BlockState] = [state.blocks[index] for index in sorted(state.blocks)]
    calls: list[ToolCall] = []
    raw: list[JsonObject] = []
    for block in ordered:
        call: ToolCall | None = _tool_call_of(block) if block.kind == "tool_use" else None
        if call is not None:
            calls.append(call)
        entry: JsonObject | None = _raw_block(block, call)
        if entry is not None:
            raw.append(entry)
    texts: list[str] = [block.text for block in ordered if block.kind == "text" and block.text]
    thoughts: list[str] = [block.text for block in ordered if block.kind == "thinking" and block.text]
    return ModelReply(
        model=state.model,
        text="\n\n".join(texts),
        thinking="\n\n".join(thoughts),
        tool_calls=tuple(calls),
        usage=state.usage,
        finish=_finish_of(state.stop_reason, bool(calls)),
        raw_finish=state.stop_reason,
        raw=tuple(raw),
    )


def messages_codec(classify: StatusClassifier) -> StreamCodec[MessagesState]:
    return StreamCodec(
        initial=initial_messages_state,
        fold=lambda state, event, context: fold_messages_event(state, event, context, classify),
        is_final=lambda state: state.stopped,
        accepts_eof=lambda state: state.stop_reason != "",
        to_reply=messages_reply,
    )
