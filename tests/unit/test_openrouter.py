"""OpenRouter (item 349): what its list of models says each model can do (a variant of a model too), how streamed
reasoning details join into whole ones and that only a codec that sends them back keeps them, what a request asks (the
reasoning level as OpenRouter's ``reasoning`` object, and the details of an assistant turn sent back only to the model
that gave them), and that an answer not shaped as OpenRouter documents it is a protocol error."""

import pytest

from dotagent.core.errors import ProviderProtocolError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, fold_chat_event, initial_chat_state, merge_details
from dotagent.llm.http import SseEvent
from dotagent.llm.openrouter import catalog_of, listed_for, listed_model, model_info_of, parse_key, writes_text
from dotagent.llm.provider_errors import WireError, classify_generic, status_for_stream_error
from dotagent.llm.types import (
    ChatRequest,
    Effort,
    Message,
    ModelReply,
    ToolCall,
    assistant_from_reply,
    empty_usage,
    user_message,
)

# Entries as OpenRouter's GET /models gives them (2026-10-17), cut to the fields Dot reads.
_CLAUDE: JsonObject = {
    "id": "anthropic/claude-sonnet-5.5",
    "context_length": 1_000_000,
    "architecture": {"input_modalities": ["text", "image", "file"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0.000003", "completion": "0.000015"},
    "top_provider": {"context_length": 1_000_000, "max_completion_tokens": 128_000},
    "supported_parameters": ["max_tokens", "reasoning", "include_reasoning", "tool_choice", "tools"],
    "reasoning": {"mandatory": True, "supported_efforts": ["max", "xhigh", "high", "medium", "low"]},
}
_SWITCH: JsonObject = {
    "id": "lab/switch-model:free",
    "context_length": 262_144,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0", "completion": "0"},
    "top_provider": {"max_completion_tokens": None},
    "supported_parameters": ["reasoning", "temperature", "tools"],
    "reasoning": {"mandatory": False},
}
_ALWAYS: JsonObject = {
    "id": "deepseek/deepseek-r1-0528",
    "context_length": 163_840,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0.0000005", "completion": "0.000002"},
    "top_provider": {"max_completion_tokens": 32_768},
    "supported_parameters": ["reasoning", "temperature", "tools"],
    "reasoning": {"mandatory": True},
}
_PICTURES: JsonObject = {
    "id": "lab/image-maker",
    "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
    "supported_parameters": [],
}
_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=False,
    replay_reasoning=False,
    cache_key_in_body=False,
    reasoning_object=True,
    replay_details=True,
)


def _request(model: str, messages: tuple[Message, ...], effort: Effort) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=messages,
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort=effort,
        speed="standard",
        session_key="",
        cache_mark=None,
    )


def test_a_listed_model_says_what_it_can_do() -> None:
    claude = listed_model(_CLAUDE)
    assert (claude.tools, claude.free, claude.traits.vision, claude.traits.temperature) == (True, False, True, False)
    assert claude.traits.efforts == ("max", "xhigh", "high", "medium", "low")
    assert (claude.traits.reasoning, claude.traits.thinking_toggle) == (True, False)
    assert (claude.traits.context_length, claude.traits.max_output_tokens) == (1_000_000, 128_000)
    switch = listed_model(_SWITCH)
    assert (switch.free, switch.traits.thinking_toggle, switch.traits.efforts) == (True, True, ())
    assert (switch.traits.max_output_tokens, switch.traits.window_source) == (32_768, "published")
    always = listed_model(_ALWAYS)
    assert (always.traits.reasoning, always.traits.thinking_toggle, always.traits.efforts) == (True, False, ())
    assert writes_text(_CLAUDE) and not writes_text(_PICTURES)


def test_the_level_goes_as_the_reasoning_object() -> None:
    hello: tuple[Message, ...] = (user_message("Hello", ()),)
    with_levels = build_chat_body(_request("c", hello, "high"), listed_model(_CLAUDE).traits, _OPTIONS, "openrouter:c")
    assert with_levels["reasoning"] == {"effort": "high"} and "reasoning_effort" not in with_levels
    switch = listed_model(_SWITCH).traits
    assert build_chat_body(_request("s", hello, "off"), switch, _OPTIONS, "openrouter:s")["reasoning"] == {
        "enabled": False
    }
    assert build_chat_body(_request("s", hello, "medium"), switch, _OPTIONS, "openrouter:s")["reasoning"] == {
        "enabled": True
    }
    assert "reasoning" not in build_chat_body(
        _request("d", hello, "high"), listed_model(_ALWAYS).traits, _OPTIONS, "openrouter:d"
    )


def test_streamed_details_join_into_whole_ones() -> None:
    joined = merge_details(
        (),
        [
            {"type": "reasoning.text", "text": "First ", "index": 0, "format": "anthropic-claude-v1"},
            {"type": "reasoning.text", "text": "I look.", "index": 0},
            {"type": "reasoning.text", "signature": "sig-1", "index": 0},
            {"type": "reasoning.encrypted", "data": "abc", "index": 1},
            {"type": "reasoning.encrypted", "data": "def", "index": 1},
        ],
    )
    assert joined == (
        {
            "type": "reasoning.text",
            "text": "First I look.",
            "index": 0,
            "format": "anthropic-claude-v1",
            "signature": "sig-1",
        },
        {"type": "reasoning.encrypted", "data": "abcdef", "index": 1},
    )
    # A piece with another detail's id starts a detail of its own, even at the same index.
    first: JsonObject = {"type": "reasoning.encrypted", "id": "rs_1", "data": "a", "index": 0}
    second: JsonObject = {"type": "reasoning.encrypted", "id": "rs_2", "data": "b", "index": 0}
    assert merge_details((first,), [second]) == (first, second)


def test_only_a_codec_that_sends_details_back_keeps_them() -> None:
    piece: JsonObject = {"type": "reasoning.text", "text": "Plan.", "index": 0}
    chunk: SseEvent = SseEvent(
        event=None,
        data=dumps_compact({"choices": [{"index": 0, "delta": {"reasoning_details": [piece]}, "finish_reason": None}]}),
    )
    without: ChatOptions = _OPTIONS.model_copy(update={"replay_details": False})
    kept, _ = fold_chat_event(initial_chat_state("m"), chunk, {}, classify_generic, _OPTIONS)
    left, _ = fold_chat_event(initial_chat_state("m"), chunk, {}, classify_generic, without)
    assert (kept.thinking, kept.details) == ("Plan.", (piece,))
    assert (left.thinking, left.details) == ("", ())


def test_a_variant_of_a_listed_model_can_do_what_the_model_can() -> None:
    claude = listed_model(_CLAUDE)
    catalog = {"anthropic/claude-sonnet-5.5": claude}
    assert listed_for(catalog, "anthropic/claude-sonnet-5.5:nitro") == claude
    assert listed_for(catalog, "anthropic/claude-opus-5.5") is None
    assert model_info_of("anthropic/claude-sonnet-5.5:free", claude).free


def test_the_details_of_a_turn_go_back_only_to_the_model_that_gave_them() -> None:
    details: tuple[JsonObject, ...] = ({"type": "reasoning.text", "text": "Plan.", "signature": "s"},)
    reply = ModelReply(
        model="c",
        text="",
        thinking="Plan.",
        tool_calls=(ToolCall(id="call_1", name="list_dir", arguments={"path": "."}),),
        usage=empty_usage(),
        finish="tool_calls",
        raw_finish="tool_calls",
        raw=details,
    )
    turn: Message = assistant_from_reply(reply, "openrouter:c")
    conversation: tuple[Message, ...] = (user_message("Look", ()), turn)
    traits = listed_model(_CLAUDE).traits
    same = build_chat_body(_request("c", conversation, "high"), traits, _OPTIONS, "openrouter:c")
    other = build_chat_body(_request("d", conversation, "high"), traits, _OPTIONS, "openrouter:d")
    assert _assistant(same)["reasoning_details"] == list(details)
    assert "reasoning_details" not in _assistant(other)


def test_a_status_as_an_error_code_is_that_status_and_the_key_reads_its_limits() -> None:
    assert status_for_stream_error(WireError(kind="", code="402", message="no credits", limit_name="")) == 402
    assert status_for_stream_error(WireError(kind="", code="12", message="odd", limit_name="")) == 500
    status = parse_key({"data": {"usage": 1.25, "limit": None, "is_free_tier": True}})
    assert (status.usage, status.limit, status.free_tier) == (1.25, None, True)


def test_an_answer_not_shaped_as_documented_is_a_protocol_error() -> None:
    for listing in ({"error": {"message": "busy"}}, {"data": []}, {"data": {"id": "x"}}):
        with pytest.raises(ProviderProtocolError):
            catalog_of(listing)
    for payload in ({}, {"data": None}, {"data": {"limit": 5}}, {"data": {"usage": "1.5"}}):
        with pytest.raises(ProviderProtocolError):
            parse_key(payload)


def _assistant(body: JsonObject) -> JsonObject:
    messages = body["messages"]
    assert isinstance(messages, list)
    found = [entry for entry in messages if isinstance(entry, dict) and entry.get("role") == "assistant"]
    assert len(found) == 1
    return found[0]
