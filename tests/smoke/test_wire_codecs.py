"""Wire-format checks for the OpenAI, OpenCode Go and Anthropic connectors.

The first half replays recorded-style streams (including the gateway's trailing events and error bodies, which a
local server cannot produce). The second half makes real calls through a local Ollama's OpenAI- and
Anthropic-compatible routes, so every codec is exercised end to end with tools, reasoning replay and images.
"""

import base64
import io

import httpx
import pytest
from ollama_live import live_with
from PIL import Image

from dotagent.agent.review import VET_TOOL, LessonVetting, _verdict_from
from dotagent.config import GoRoute, OpenAIConfig, OpenCodeGoConfig
from dotagent.core.errors import (
    ProviderAccessError,
    ProviderAuthError,
    ProviderHttpError,
    ProviderProtocolError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRefusedError,
)
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject
from dotagent.llm.anthropic import claude_traits
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.codec_messages import MessagesOptions, build_messages_body, messages_codec
from dotagent.llm.codec_responses import ResponsesOptions, build_responses_body, responses_codec
from dotagent.llm.connector import StreamCodec
from dotagent.llm.http import SseEvent, is_retryable_provider_error
from dotagent.llm.openai import OpenAIProvider, is_chat_model, openai_traits
from dotagent.llm.opencode_go import CATALOG, OpenCodeGoProvider, go_free, go_traits, route_of
from dotagent.llm.provider_errors import (
    WireError,
    classify_anthropic,
    classify_openai,
    classify_opencode,
    error_from_stream,
)
from dotagent.llm.router import unavailable_reason
from dotagent.llm.traits import ModelTraits
from dotagent.llm.types import (
    ChatProvider,
    ChatRequest,
    Effort,
    ImageData,
    Message,
    ModelReply,
    StreamDelta,
    ToolCall,
    ToolSpec,
    assistant_from_reply,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)

OLLAMA_ROOT: str = "http://localhost:11434"
LIVE_MODEL: str = "gpt-oss:20b-cloud"
VISION_MODEL: str = "gemma4:cloud"
# What OpenCode Go's messages route sends: a plain thinking switch, no cache marks, and its own output limit.
GO_MESSAGES: MessagesOptions = MessagesOptions(
    adaptive_thinking=False, prompt_cache=False, settled_mark=False, default_max_tokens=32768
)
# What the Anthropic connector sends: adaptive thinking at the reasoning level, the prompt cache, the model's own limit.
CLAUDE_MESSAGES: MessagesOptions = MessagesOptions(
    adaptive_thinking=True, prompt_cache=True, settled_mark=True, default_max_tokens=None
)
# What OpenCode Go's chat route sends: ``max_tokens``, the thinking of a turn carried back as ``reasoning_content``.
GO_CHAT: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=False,
    replay_reasoning=True,
    cache_key_in_body=False,
    reasoning_object=False,
    replay_details=False,
)

TEMPERATURE_TOOL: ToolSpec = ToolSpec(
    name="get_temperature",
    description="Return the current temperature in Celsius for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
)
SCREENSHOT_TOOL: ToolSpec = ToolSpec(
    name="screenshot",
    description="Take a screenshot of the screen.",
    parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)


def request_for(model: str, messages: tuple[Message, ...], tools: tuple[ToolSpec, ...]) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=messages,
        tools=tools,
        temperature=None,
        max_output_tokens=None,
        effort="low",
        speed="standard",
        session_key="smoke-session",
        cache_mark=None,
        window=None,
    )


def sse(data: JsonObject, event: str | None) -> SseEvent:
    return SseEvent(event=event, data=dumps_compact(data))


def replay[S](codec: StreamCodec[S], model: str, events: list[SseEvent]) -> tuple[ModelReply, list[StreamDelta]]:
    state: S = codec.initial(model)
    deltas: list[StreamDelta] = []
    for event in events:
        state, new = codec.fold(state, event, {})
        deltas.extend(new)
    assert codec.is_final(state)
    return codec.to_reply(state), deltas


# ------------------------------------------------------------------------------------ recorded streams


def chat_chunk(delta: JsonObject, finish: str | None) -> JsonObject:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def test_chat_stream_assembles_tool_call_and_usage_chunk_without_choices() -> None:
    chunk = chat_chunk
    events: list[SseEvent] = [
        sse(chunk({"role": "assistant", "content": ""}, None), None),
        sse(chunk({"reasoning_content": "weigh "}, None), None),
        sse(chunk({"content": "Checking."}, None), None),
        sse(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_temperature", "arguments": ""},
                        }
                    ]
                },
                None,
            ),
            None,
        ),
        sse(chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"city":'}}]}, None), None),
        sse(chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]}, None), None),
        sse(chunk({}, "tool_calls"), None),
        sse(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 100},
                },
            },
            None,
        ),
        SseEvent(event=None, data="[DONE]"),
    ]
    reply, deltas = replay(chat_codec(classify_opencode, GO_CHAT), "m", events)
    assert reply.text == "Checking."
    assert reply.thinking == "weigh "
    assert [(call.id, call.name, call.arguments) for call in reply.tool_calls] == [
        ("call_1", "get_temperature", {"city": "Paris"})
    ]
    assert reply.finish == "tool_calls"
    assert (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.cached_tokens) == (120, 30, 100)
    assert [delta.kind for delta in deltas] == ["thinking", "text"]


def test_responses_stream_prefers_terminal_output_and_keeps_replayable_items() -> None:
    reasoning: JsonObject = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "Plan"}],
        "encrypted_content": "ENC",
    }
    call: JsonObject = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "get_temperature",
        "arguments": '{"city": "Paris"}',
        "status": "completed",
    }
    events: list[SseEvent] = [
        sse({"type": "response.created", "response": {"id": "resp_1"}}, "response.created"),
        sse(
            {"type": "response.reasoning_summary_text.delta", "delta": "Plan"}, "response.reasoning_summary_text.delta"
        ),
        sse({"type": "response.output_item.done", "output_index": 0, "item": reasoning}, "response.output_item.done"),
        sse({"type": "response.output_item.done", "output_index": 1, "item": call}, "response.output_item.done"),
        sse(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [reasoning, call],
                    "usage": {"input_tokens": 50, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 10}},
                },
            },
            "response.completed",
        ),
    ]
    reply, _ = replay(responses_codec(classify_openai), "m", events)
    assert reply.finish == "tool_calls"
    assert reply.thinking == "Plan"
    assert reply.tool_calls[0].arguments == {"city": "Paris"}
    assert reply.raw == (reasoning, call)
    assert (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.cached_tokens) == (50, 20, 10)


def test_responses_stream_malformed_arguments_become_stand_in() -> None:
    call: JsonObject = {"type": "function_call", "call_id": "c", "name": "t", "arguments": '{"a": '}
    events: list[SseEvent] = [
        sse({"type": "response.output_item.done", "output_index": 0, "item": call}, "response.output_item.done"),
        sse(
            {"type": "response.completed", "response": {"status": "completed", "output": [call]}}, "response.completed"
        ),
    ]
    reply, _ = replay(responses_codec(classify_openai), "m", events)
    assert reply.tool_calls[0].arguments == {"__invalid_arguments__": '{"a": '}


def _refusal_message(words: str) -> JsonObject:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "refusal", "refusal": words}],
    }


def test_a_refusal_of_the_responses_wire_is_the_models_declining_and_not_its_answer() -> None:
    """Item 552: a run does not end done with a refusal for its result, and the review model that declines is known."""
    refusal: JsonObject = _refusal_message("I can't help with that.")
    events: list[SseEvent] = [
        sse({"type": "response.refusal.delta", "delta": "I can't help "}, "response.refusal.delta"),
        sse({"type": "response.refusal.delta", "delta": "with that."}, "response.refusal.delta"),
        sse({"type": "response.output_item.done", "output_index": 0, "item": refusal}, "response.output_item.done"),
        sse(
            {"type": "response.completed", "response": {"status": "completed", "output": [refusal]}},
            "response.completed",
        ),
    ]
    reply, deltas = replay(responses_codec(classify_openai), "m", events)
    assert (reply.finish, reply.raw_finish, reply.text) == ("other", "refusal", "I can't help with that.")
    assert "".join(delta.text for delta in deltas) == "I can't help with that."
    with pytest.raises(ProviderRefusedError):
        _verdict_from(reply, VET_TOOL, LessonVetting, "m")


def test_a_refusal_the_responses_stream_only_streams_is_a_refusal_too() -> None:
    events: list[SseEvent] = [
        sse({"type": "response.refusal.delta", "delta": "No."}, "response.refusal.delta"),
        sse({"type": "response.completed", "response": {"status": "completed", "output": []}}, "response.completed"),
    ]
    reply, _ = replay(responses_codec(classify_openai), "m", events)
    assert (reply.finish, reply.raw_finish, reply.text) == ("other", "refusal", "No.")


def test_an_answer_and_a_call_of_the_responses_wire_are_not_taken_for_a_refusal() -> None:
    answer: JsonObject = {
        "type": "message",
        "id": "msg_2",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "Paris is 14 degrees."}],
    }
    call: JsonObject = {"type": "function_call", "call_id": "c", "name": "t", "arguments": "{}", "status": "completed"}
    for output, finish, raw in (
        ([answer], "stop", "completed"),
        ([_refusal_message("No."), call], "tool_calls", "completed"),
    ):
        events: list[SseEvent] = [
            sse(
                {"type": "response.completed", "response": {"status": "completed", "output": output}},
                "response.completed",
            )
        ]
        reply, _ = replay(responses_codec(classify_openai), "m", events)
        assert (reply.finish, reply.raw_finish) == (finish, raw)


def test_a_refusal_of_the_chat_wire_is_kept_in_the_reply_and_ends_it_as_declined() -> None:
    events: list[SseEvent] = [
        sse(chat_chunk({"role": "assistant", "content": None, "refusal": None}, None), None),
        sse(chat_chunk({"refusal": "I can't "}, None), None),
        sse(chat_chunk({"refusal": "help with that."}, None), None),
        sse(chat_chunk({}, "stop"), None),
        SseEvent(event=None, data="[DONE]"),
    ]
    reply, deltas = replay(chat_codec(classify_openai, GO_CHAT), "m", events)
    assert (reply.finish, reply.raw_finish, reply.text) == ("other", "refusal", "I can't help with that.")
    assert [(delta.kind, delta.text) for delta in deltas] == [("text", "I can't "), ("text", "help with that.")]
    with pytest.raises(ProviderRefusedError):
        _verdict_from(reply, VET_TOOL, LessonVetting, "m")
    answered: list[SseEvent] = [
        sse(chat_chunk({"content": "Paris."}, None), None),
        sse(chat_chunk({}, "stop"), None),
        SseEvent(event=None, data="[DONE]"),
    ]
    plain, _ = replay(chat_codec(classify_openai, GO_CHAT), "m", answered)
    assert (plain.finish, plain.raw_finish, plain.text) == ("stop", "stop", "Paris.")


def test_responses_failed_event_raises_typed_error() -> None:
    codec = responses_codec(classify_openai)
    quota = sse(
        {"type": "response.failed", "response": {"error": {"code": "insufficient_quota", "message": "no credit"}}},
        "response.failed",
    )
    with pytest.raises(ProviderQuotaError):
        codec.fold(codec.initial("m"), quota, {})
    overloaded = sse(
        {"type": "response.failed", "response": {"error": {"code": "server_error", "message": "boom"}}},
        "response.failed",
    )
    with pytest.raises(ProviderHttpError) as caught:
        codec.fold(codec.initial("m"), overloaded, {})
    assert is_retryable_provider_error(caught.value)


def test_messages_stream_with_thinking_signature_and_tool_use() -> None:
    events: list[SseEvent] = [
        sse(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 1}},
            },
            "message_start",
        ),
        sse(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            "content_block_start",
        ),
        sse(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
            "content_block_delta",
        ),
        sse(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
            "content_block_delta",
        ),
        sse({"type": "content_block_stop", "index": 0}, "content_block_stop"),
        sse(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tu_1", "name": "get_temperature", "input": {}},
            },
            "content_block_start",
        ),
        sse(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"city":'},
            },
            "content_block_delta",
        ),
        sse(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'},
            },
            "content_block_delta",
        ),
        sse({"type": "content_block_stop", "index": 1}, "content_block_stop"),
        sse(
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 25}},
            "message_delta",
        ),
        sse({"type": "message_stop"}, "message_stop"),
    ]
    reply, _ = replay(messages_codec(classify_opencode), "m", events)
    assert reply.thinking == "hmm"
    assert [(call.id, call.arguments) for call in reply.tool_calls] == [("tu_1", {"city": "Paris"})]
    assert reply.finish == "tool_calls"
    assert reply.usage.input_tokens == 100
    assert reply.usage.cached_tokens == 90
    assert reply.usage.output_tokens == 25
    assert any(block.get("signature") == "SIG" for block in reply.raw)


def test_stream_error_frame_without_http_error_is_raised() -> None:
    codec = chat_codec(classify_opencode, GO_CHAT)
    frame = sse(
        {
            "type": "error",
            "error": {"type": "GoUsageLimitError", "message": "limit"},
            "metadata": {"limitName": "rolling"},
        },
        None,
    )
    with pytest.raises(ProviderQuotaError):
        codec.fold(codec.initial("m"), frame, {})


def test_an_error_event_of_the_responses_stream_that_names_no_error_is_raised() -> None:
    # An "event: error" line whose data says nothing was taken for nothing, and the stream went on as if the model had
    # stopped (the outside audit of 2026-10-17, R10).
    codec = responses_codec(classify_openai)
    for frame in (sse({}, "error"), sse({"type": "keepalive"}, "error")):
        with pytest.raises(ProviderProtocolError):
            codec.fold(codec.initial("m"), frame, {})


# ------------------------------------------------------------------------------------ error classes


def test_openai_quota_is_terminal_but_rate_limit_is_retried() -> None:
    quota = classify_openai(
        429, WireError(kind="insufficient_quota", code="insufficient_quota", message="m", limit_name=""), None, {}
    )
    burst = classify_openai(
        429, WireError(kind="rate_limit_error", code="slow_down", message="m", limit_name=""), 2.0, {}
    )
    assert isinstance(quota, ProviderQuotaError) and not is_retryable_provider_error(quota)
    assert isinstance(burst, ProviderRateLimitError) and is_retryable_provider_error(burst)


def test_opencode_usage_limit_is_terminal_but_burst_is_retried() -> None:
    quota = classify_opencode(
        429, WireError(kind="GoUsageLimitError", code="", message="m", limit_name="weekly"), 3600.0, {}
    )
    burst = classify_opencode(429, WireError(kind="RateLimitError", code="", message="m", limit_name=""), 60.0, {})
    assert isinstance(quota, ProviderQuotaError) and quota.context["limit_name"] == "weekly"
    assert isinstance(burst, ProviderRateLimitError) and is_retryable_provider_error(burst)


def test_anthropic_billing_and_a_spent_balance_are_terminal_but_an_overloaded_api_is_retried() -> None:
    billing = classify_anthropic(402, WireError(kind="billing_error", code="", message="m", limit_name=""), None, {})
    # A spent prepaid balance, as the API words it.
    no_credit = classify_anthropic(
        400,
        WireError(
            kind="invalid_request_error",
            code="",
            message="Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to "
            "upgrade or purchase credits.",
            limit_name="",
        ),
        None,
        {},
    )
    refused = classify_anthropic(
        400, WireError(kind="invalid_request_error", code="", message="max_tokens: too large", limit_name=""), None, {}
    )
    overloaded = classify_anthropic(
        529, WireError(kind="overloaded_error", code="", message="m", limit_name=""), None, {}
    )
    assert isinstance(billing, ProviderQuotaError) and not is_retryable_provider_error(billing)
    assert isinstance(no_credit, ProviderQuotaError) and not is_retryable_provider_error(no_credit)
    assert not isinstance(refused, ProviderQuotaError) and not is_retryable_provider_error(refused)
    assert is_retryable_provider_error(overloaded)
    codec = messages_codec(classify_anthropic)
    frame = sse({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, "error")
    with pytest.raises(ProviderHttpError) as caught:
        codec.fold(codec.initial("m"), frame, {})
    assert caught.value.status == 529 and is_retryable_provider_error(caught.value)


# ------------------------------------------------------------------------------------ request bodies


def test_request_bodies_end_with_stream_and_follow_each_gateway_flavour() -> None:
    traits: ModelTraits = go_traits("glm-5.2")
    request: ChatRequest = request_for(
        "glm-5.2", (system_message("be brief"), user_message("hi", ())), (TEMPERATURE_TOOL,)
    )
    gateway_chat: JsonObject = build_chat_body(
        request,
        traits,
        ChatOptions(
            max_tokens_field="max_tokens",
            request_usage=False,
            replay_reasoning=True,
            cache_key_in_body=False,
            reasoning_object=False,
            replay_details=False,
        ),
        "opencode_go:glm-5.2",
    )
    assert list(gateway_chat)[-1] == "stream"
    assert "stream_options" not in gateway_chat and "prompt_cache_key" not in gateway_chat
    assert gateway_chat["reasoning_effort"] == "high"
    openai_chat: JsonObject = build_chat_body(
        request.model_copy(update={"max_output_tokens": 500}),
        openai_traits("gpt-6.1-sol"),
        ChatOptions(
            max_tokens_field="max_completion_tokens",
            request_usage=True,
            replay_reasoning=False,
            cache_key_in_body=True,
            reasoning_object=False,
            replay_details=False,
        ),
        "openai:gpt-6.1-sol",
    )
    assert openai_chat["stream_options"] == {"include_usage": True}
    assert openai_chat["max_completion_tokens"] == 500 and openai_chat["prompt_cache_key"] == "smoke-session"
    responses: JsonObject = build_responses_body(
        request_for("gpt-6.1-sol", (system_message("be brief"), user_message("hi", ())), (TEMPERATURE_TOOL,)),
        openai_traits("gpt-6.1-sol"),
        "openai:gpt-6.1-sol",
        ResponsesOptions(image_detail="original", tool_output_images=True),
    )
    assert list(responses)[-1] == "stream" and responses["store"] is False
    assert responses["include"] == ["reasoning.encrypted_content"]
    assert responses["reasoning"] == {"effort": "low", "summary": "auto"}
    assert responses["instructions"] == "be brief"
    assert "temperature" not in responses
    toggled: JsonObject = build_messages_body(
        request_for("minimax-m3", (user_message("hi", ()),), ()),
        go_traits("minimax-m3"),
        "opencode_go:minimax-m3",
        GO_MESSAGES,
    )
    assert toggled["thinking"] == {"type": "adaptive"} and list(toggled)[-1] == "stream"


def test_messages_body_puts_a_screenshot_inside_its_tool_result() -> None:
    """The gateway's Qwen models never see a picture that sits beside the result in the same turn."""
    call: ToolCall = ToolCall(id="toolu_1", name="screenshot", arguments={})
    picture: ImageData = solid_image(20, 20, 220)

    def last_turn(result: Message) -> JsonObject:
        body: JsonObject = build_messages_body(
            request_for(
                "qwen3.8-flash",
                (user_message("look", ()), assistant_message("", None, (call,)), result),
                (SCREENSHOT_TOOL,),
            ),
            go_traits("qwen3.8-flash"),
            "opencode_go:qwen3.8-flash",
            GO_MESSAGES,
        )
        return body["messages"][-1]

    with_picture: JsonObject = last_turn(tool_message(call, "screenshot captured", (picture,)))
    assert with_picture == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": [
                    {"type": "text", "text": "screenshot captured"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": picture.data_b64},
                    },
                ],
            }
        ],
    }
    assert last_turn(tool_message(call, "done", ()))["content"][0]["content"] == "done"


def test_routes_and_traits_follow_catalog_prefix_and_override() -> None:
    assert route_of("grok-4.7", {}) == "responses"
    assert route_of("qwen9-new", {}) == "messages"
    assert route_of("claude-sonnet-9", {}) == "messages"
    assert go_free("step-5-preview-free") and not go_free("space-bunny")
    assert route_of("brand-new-model", {}) == "chat"
    assert route_of("brand-new-model", {"brand-new-model": "responses"}) == "responses"
    assert route_of("glm-5.3", {"glm-5.3": "messages"}) == "messages"
    assert all(model.traits.reasoning for model in CATALOG.values())
    assert openai_traits("gpt-6-luna").efforts[0] == "none"
    assert "none" not in openai_traits("gpt-6.1-sol").efforts
    assert not openai_traits("gpt-4.1").reasoning
    assert all(
        not openai_traits(model).reasoning
        for model in ("gpt-4", "gpt-4-turbo", "gpt-4-0125-preview", "gpt-3.5-turbo", "gpt-5-chat-latest")
    )
    assert openai_traits("gpt-3.5-turbo").efforts == ()
    assert openai_traits("gpt-7").reasoning
    assert not is_chat_model("gpt-3.5-turbo-instruct")
    assert openai_traits("some-local-model").reasoning is False


def claude_entry(
    model: str, thinking: tuple[str, ...], efforts: tuple[str, ...], window: int, output: int
) -> JsonObject:
    """A model's entry as the Models API lists it, with the thinking types and effort levels it supports."""
    return {
        "id": model,
        "type": "model",
        "max_input_tokens": window,
        "max_tokens": output,
        "capabilities": {
            "image_input": {"supported": True},
            "effort": {
                "supported": bool(efforts),
                **{level: {"supported": level in efforts} for level in ("low", "medium", "high", "max")},
                "xhigh": {"supported": True} if "xhigh" in efforts else None,
            },
            "thinking": {
                "supported": bool(thinking),
                "types": {kind: {"supported": kind in thinking} for kind in ("adaptive", "enabled", "disabled")},
            },
        },
    }


# A model that cannot turn thinking off, one that can, and one without adaptive thinking.
OPUS_LIKE: JsonObject = claude_entry(
    "claude-opus-5-5", ("adaptive",), ("low", "medium", "high", "xhigh", "max"), 1_000_000, 128_000
)
HAIKU_LIKE: JsonObject = claude_entry(
    "claude-haiku-5-5", ("adaptive", "disabled"), ("low", "medium", "high", "max"), 1_000_000, 128_000
)
SONNET_45_LIKE: JsonObject = claude_entry("claude-sonnet-4-5", ("enabled", "disabled"), (), 200_000, 64_000)


def test_claude_traits_come_from_the_models_api_entry() -> None:
    opus: ModelTraits = claude_traits(OPUS_LIKE)
    assert opus.efforts == ("low", "medium", "high", "xhigh", "max") and opus.reasoning and opus.vision
    assert (opus.context_length, opus.max_output_tokens) == (1_000_000, 128_000) and not opus.temperature
    assert claude_traits(HAIKU_LIKE).efforts == ("off", "low", "medium", "high", "max")
    older: ModelTraits = claude_traits(SONNET_45_LIKE)
    assert older.efforts == () and not older.reasoning and older.temperature
    bare: ModelTraits = claude_traits(
        {"id": "claude-next", "capabilities": None, "max_input_tokens": None, "max_tokens": None}
    )
    assert not bare.vision and not bare.reasoning and not bare.temperature
    assert (bare.context_length, bare.max_output_tokens) == (200_000, 32_768)


def test_claude_body_thinks_adaptively_caches_the_prompt_and_turns_thinking_off_where_it_can() -> None:
    thought: JsonObject = {"type": "thinking", "thinking": "hmm", "signature": "SIG"}
    call: ToolCall = ToolCall(id="toolu_1", name="get_temperature", arguments={"city": "Paris"})
    tool_use: JsonObject = {"type": "tool_use", "id": "toolu_1", "name": "get_temperature", "input": {"city": "Paris"}}

    def body(model: str, entry: JsonObject, effort: Effort, temperature: float | None) -> JsonObject:
        """The body of a step that follows a tool call the same model made after thinking."""
        earlier: Message = assistant_message("", None, (call,)).model_copy(
            update={"raw": (thought, tool_use), "raw_origin": f"anthropic:{model}"}
        )
        messages: tuple[Message, ...] = (
            system_message("be brief"),
            user_message("weather?", ()),
            earlier,
            tool_message(call, "18 C", ()),
        )
        request: ChatRequest = request_for(model, messages, (TEMPERATURE_TOOL,)).model_copy(
            update={"effort": effort, "temperature": temperature}
        )
        return build_messages_body(request, claude_traits(entry), f"anthropic:{model}", CLAUDE_MESSAGES)

    thinking: JsonObject = body("claude-opus-5-5", OPUS_LIKE, "high", 0.2)
    assert thinking["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert thinking["output_config"] == {"effort": "high"} and thinking["cache_control"] == {"type": "ephemeral"}
    assert thinking["max_tokens"] == 128_000 and "temperature" not in thinking and list(thinking)[-1] == "stream"
    assert thinking["system"] == "be brief" and thinking["messages"][1]["content"] == [thought, tool_use]
    assert body("claude-opus-5-5", OPUS_LIKE, "off", None)["output_config"] == {"effort": "low"}
    switched_off: JsonObject = body("claude-haiku-5-5", HAIKU_LIKE, "off", None)
    assert switched_off["thinking"] == {"type": "disabled"} and "output_config" not in switched_off
    assert switched_off["messages"][1]["content"] == [tool_use]
    older: JsonObject = body("claude-sonnet-4-5", SONNET_45_LIKE, "high", 0.2)
    assert "thinking" not in older and "output_config" not in older
    assert older["temperature"] == 0.2 and older["max_tokens"] == 64_000


# ------------------------------------------------------------------------------------ live calls


def ollama_is_up() -> bool:
    try:
        return httpx.get(f"{OLLAMA_ROOT}/api/version", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


# Live (item 632): the cloud models answer from off this Mac.
requires_ollama = live_with(ollama_is_up(), "needs a running local Ollama server")


FIRST_ROUND_ATTEMPTS: int = 3


async def ignore_delta(delta: StreamDelta) -> None:
    return None


def go_provider(model: str, route: GoRoute) -> ChatProvider:
    config: OpenCodeGoConfig = OpenCodeGoConfig(
        base_url=f"{OLLAMA_ROOT}/v1",
        api_key_env="UNUSED",
        route_overrides={model: route},
        connect_timeout_s=10.0,
        read_timeout_s=240.0,
        attempts=3,
        max_concurrent=2,
    )
    return OpenCodeGoProvider(config, "ollama")


def openai_provider(wire: str) -> ChatProvider:
    config: OpenAIConfig = OpenAIConfig(
        base_url=f"{OLLAMA_ROOT}/v1",
        api_key_env="UNUSED",
        organization_env="UNUSED",
        project_env="UNUSED",
        wire="responses" if wire == "responses" else "chat",
        connect_timeout_s=10.0,
        read_timeout_s=240.0,
        attempts=3,
        max_concurrent=2,
    )
    return OpenAIProvider(config, "ollama", None, None)


def solid_image(red: int, green: int, blue: int) -> ImageData:
    buffer: io.BytesIO = io.BytesIO()
    Image.new("RGB", (128, 128), (red, green, blue)).save(buffer, format="PNG")
    return ImageData(media_type="image/png", data_b64=base64.b64encode(buffer.getvalue()).decode("ascii"))


def greedy_request_for(model: str, messages: tuple[Message, ...], tools: tuple[ToolSpec, ...]) -> ChatRequest:
    """Greedy decoding: a model that samples sometimes answers from its prior instead of the tool result."""
    return request_for(model, messages, tools).model_copy(update={"temperature": 0.0})


async def first_tool_call_reply(
    provider: ChatProvider, model: str, history: tuple[Message, ...], tools: tuple[ToolSpec, ...]
) -> ModelReply:
    """The reply that calls the tool. A model now and then answers in prose instead, which says nothing about the wire
    codec, so the question is asked again a few times."""
    texts: list[str] = []
    for _ in range(FIRST_ROUND_ATTEMPTS):
        reply: ModelReply = await provider.complete(greedy_request_for(model, history, tools), ignore_delta)
        if reply.finish == "tool_calls":
            return reply
        texts.append(reply.text)
    raise AssertionError(f"the model never called the tool: {texts}")


async def tool_round_trip(provider: ChatProvider, model: str) -> ModelReply:
    system = system_message("You are terse. Use tools when they help.")
    ask = user_message("What is the temperature in Paris right now? Use the tool.", ())
    first: ModelReply = await first_tool_call_reply(provider, model, (system, ask), (TEMPERATURE_TOOL,))
    call = first.tool_calls[0]
    assert call.name == "get_temperature" and call.arguments == {"city": "Paris"}
    history = (system, ask, assistant_from_reply(first, f"{provider.name}:{model}"), tool_message(call, "21", ()))
    return await provider.complete(greedy_request_for(model, history, (TEMPERATURE_TOOL,)), ignore_delta)


@pytest.mark.live
@requires_ollama
@pytest.mark.parametrize("route", ["chat", "responses", "messages"])
async def test_gateway_routes_round_trip_tools_and_reasoning(route: GoRoute) -> None:
    provider: ChatProvider = go_provider(LIVE_MODEL, route)
    try:
        final: ModelReply = await tool_round_trip(provider, LIVE_MODEL)
    finally:
        await provider.aclose()
    assert "21" in final.text and final.finish == "stop"


@pytest.mark.live
@requires_ollama
@pytest.mark.parametrize("wire", ["responses", "chat"])
async def test_openai_wires_round_trip_tools_and_report_usage(wire: str) -> None:
    provider: ChatProvider = openai_provider(wire)
    try:
        final: ModelReply = await tool_round_trip(provider, LIVE_MODEL)
    finally:
        await provider.aclose()
    assert "21" in final.text and final.usage.input_tokens > 0


@pytest.mark.live
@requires_ollama
@pytest.mark.parametrize("route", ["chat", "responses", "messages"])
async def test_screenshots_in_tool_results_reach_the_model(route: GoRoute) -> None:
    provider: ChatProvider = go_provider(VISION_MODEL, route)
    system = system_message("Answer in one short sentence.")
    ask = user_message("Take a screenshot with the tool, then tell me the single color that fills it.", ())
    try:
        first: ModelReply = await provider.complete(
            request_for(VISION_MODEL, (system, ask), (SCREENSHOT_TOOL,)), ignore_delta
        )
        assert first.tool_calls, first.text
        call = first.tool_calls[0]
        history = (
            system,
            ask,
            assistant_from_reply(first, f"opencode_go:{VISION_MODEL}"),
            tool_message(call, "screenshot captured", (solid_image(20, 20, 220),)),
        )
        final: ModelReply = await provider.complete(
            request_for(VISION_MODEL, history, (SCREENSHOT_TOOL,)), ignore_delta
        )
    finally:
        await provider.aclose()
    assert "blue" in final.text.lower()


def test_a_403_is_a_refused_access_and_a_401_a_refused_key() -> None:
    """Item 185: a 403, as an answer or inside a stream, is the provider refusing what the key asked, not the key; the
    reason a run moves to its standby model says so. Before, both were a refused key."""
    asked: WireError = WireError(
        kind="permission_error", code="", message="this key may not use the model", limit_name=""
    )
    access: ProviderHttpError = classify_openai(403, asked, None, {})
    key: ProviderHttpError = classify_openai(
        401, WireError(kind="", code="invalid_api_key", message="the key is not valid", limit_name=""), None, {}
    )
    streamed: ProviderHttpError = error_from_stream(asked, {}, classify_anthropic)
    assert isinstance(access, ProviderAccessError) and not isinstance(access, ProviderAuthError)
    assert isinstance(streamed, ProviderAccessError) and streamed.status == 403
    assert isinstance(key, ProviderAuthError)
    assert access.message == "provider answered HTTP 403: this key may not use the model"
    assert (unavailable_reason(access), unavailable_reason(key)) == ("access refused", "key refused")
    assert not is_retryable_provider_error(access)
