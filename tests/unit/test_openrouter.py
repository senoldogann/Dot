"""OpenRouter (item 349): what its list of models says each model can do, how streamed reasoning details join into
whole ones, and what a request asks: the reasoning level as OpenRouter's ``reasoning`` object, and the details of an
assistant turn sent back only to the model that gave them."""

from dotagent.core.types import JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, merge_details
from dotagent.llm.openrouter import listed_model, parse_key, writes_text
from dotagent.llm.provider_errors import WireError, status_for_stream_error
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


def _assistant(body: JsonObject) -> JsonObject:
    messages = body["messages"]
    assert isinstance(messages, list)
    found = [entry for entry in messages if isinstance(entry, dict) and entry.get("role") == "assistant"]
    assert len(found) == 1
    return found[0]
