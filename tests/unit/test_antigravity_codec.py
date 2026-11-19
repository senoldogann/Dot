"""The Antigravity provider's wire: the Cloud Code envelope around a Gemini request, and the stream it answers with.

The route serves the account's Gemini models and the Claude and GPT-OSS ones beside them, and it is strict about two
things this file pins down: a function call's ``thoughtSignature`` goes back with the call (a request without it is a
400), and a model whose id already names its thinking level is not given a second one.
"""

import json

from dotagent.core.types import JsonObject
from dotagent.llm.antigravity import (
    _fold,
    _initial,
    _to_reply,
    assumed_traits,
    build_request_body,
    is_chat_model,
    traits_of,
)
from dotagent.llm.http import SseEvent
from dotagent.llm.types import ChatRequest, ImageData, Message, StreamDelta, ToolCall, ToolSpec
from dotagent.llm.wire import get_obj, get_objects, get_str

GEMINI_FLASH: JsonObject = {
    "displayName": "Gemini 3 Flash",
    "supportsImages": True,
    "supportsThinking": True,
    "apiProvider": "API_PROVIDER_GOOGLE_GEMINI",
    "maxTokens": 1048576,
    "maxOutputTokens": 65536,
}


def message(
    role: str, text: str, calls: tuple[ToolCall, ...] = (), images: tuple[ImageData, ...] = (), name: str | None = None
) -> Message:
    return Message(
        role=role,
        content=text,
        images=images,
        tool_calls=calls,
        tool_call_id="call_1" if role == "tool" else None,
        tool_name=name,
        thinking=None,
        raw=(),
        raw_origin=None,
    )


def chat_request(
    model: str, messages: tuple[Message, ...], tools: tuple[ToolSpec, ...] = (), effort: str = "medium"
) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=messages,
        tools=tools,
        temperature=None,
        max_output_tokens=None,
        effort=effort,
        speed="standard",
        session_key="test",
        cache_mark=None,
        prompt_reuse="conversation",
        window=None,
    )


def gemini_of(body: JsonObject) -> JsonObject:
    return get_obj(body, "request")


def parts_of(request: JsonObject, turn: int) -> list[JsonObject]:
    return get_objects(get_objects(request, "contents")[turn], "parts")


def test_the_envelope_names_the_model_the_project_and_the_client() -> None:
    body: JsonObject = build_request_body(
        chat_request("gemini-3-flash", (message("user", "hi"),)),
        traits_of("gemini-3-flash", GEMINI_FLASH),
        "aicode-consumers",
    )
    assert get_str(body, "model") == "gemini-3-flash"
    assert get_str(body, "project") == "aicode-consumers"
    assert get_str(body, "requestType") == "agent"
    assert get_str(body, "requestId").startswith("agent-")


def test_a_system_message_becomes_the_system_instruction_and_tools_are_declared() -> None:
    tools: tuple[ToolSpec, ...] = (
        ToolSpec(
            name="shell",
            description="Run a command",
            parameters={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"command": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
    )
    body: JsonObject = build_request_body(
        chat_request("gemini-3-flash", (message("system", "You are Dot."), message("user", "hi")), tools),
        traits_of("gemini-3-flash", GEMINI_FLASH),
        "p",
    )
    request: JsonObject = gemini_of(body)
    assert request["systemInstruction"] == {"parts": [{"text": "You are Dot."}]}
    assert [get_str(turn, "role") for turn in get_objects(request, "contents")] == ["user"]
    declared: JsonObject = get_objects(request, "tools")[0]
    specification: JsonObject = get_objects(declared, "functionDeclarations")[0]
    assert specification["parameters"] == {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }


def test_a_call_goes_back_with_its_thought_signature_and_its_result_as_a_function_response() -> None:
    call = ToolCall(
        id="call_9",
        name="shell",
        arguments={"command": "echo hi"},
        extra={"thoughtSignature": "sig-abc"},
        extra_origin="antigravity:gemini-3-flash",
    )
    body: JsonObject = build_request_body(
        chat_request(
            "gemini-3-flash",
            (
                message("user", "run it"),
                message("assistant", "", calls=(call,)),
                message("tool", "hi\n", name="shell"),
            ),
        ),
        traits_of("gemini-3-flash", GEMINI_FLASH),
        "p",
    )
    request: JsonObject = gemini_of(body)
    call_part: JsonObject = parts_of(request, 1)[0]
    assert get_objects(request, "contents")[1]["role"] == "model"
    assert call_part["functionCall"] == {"name": "shell", "args": {"command": "echo hi"}}
    assert get_str(call_part, "thoughtSignature") == "sig-abc"
    assert get_objects(request, "contents")[2] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "shell", "response": {"result": "hi\n"}}}],
    }


def test_a_picture_goes_as_inline_data() -> None:
    body: JsonObject = build_request_body(
        chat_request(
            "gemini-3-flash",
            (message("user", "what is this?", images=(ImageData(media_type="image/png", data_b64="AAA"),)),),
        ),
        traits_of("gemini-3-flash", GEMINI_FLASH),
        "p",
    )
    assert parts_of(gemini_of(body), 0)[1] == {"inlineData": {"mimeType": "image/png", "data": "AAA"}}


def test_an_id_that_names_its_level_gets_no_level_and_a_plain_one_gets_the_effort() -> None:
    high: JsonObject = {**GEMINI_FLASH, "displayName": "Gemini 3.8 Flash High"}
    assert traits_of("gemini-3.8-flash-high", high).efforts == ()
    leveled: JsonObject = build_request_body(
        chat_request("gemini-3.8-flash-high", (message("user", "hi"),), effort="low"),
        traits_of("gemini-3.8-flash-high", high),
        "p",
    )
    assert get_obj(gemini_of(leveled), "generationConfig") == {}
    plain: JsonObject = build_request_body(
        chat_request("gemini-3-flash", (message("user", "hi"),), effort="high"),
        traits_of("gemini-3-flash", GEMINI_FLASH),
        "p",
    )
    assert get_obj(gemini_of(plain), "generationConfig") == {"thinkingConfig": {"thinkingLevel": "high"}}


def test_the_listing_leaves_out_what_is_not_a_chat_model() -> None:
    assert is_chat_model("gemini-3-flash", GEMINI_FLASH)
    assert not is_chat_model("gemini-4-argon-disabled", {**GEMINI_FLASH, "displayName": "Argon"})
    assert not is_chat_model("tab_20706", {**GEMINI_FLASH, "displayName": "Tab"})
    assert not is_chat_model("chat_20706", {})


def stream_event(response: JsonObject) -> SseEvent:
    return SseEvent(event=None, data=json.dumps({"response": response}))


def test_the_stream_folds_thought_text_a_call_with_its_signature_and_usage() -> None:
    state = _initial("gemini-3-flash")
    first = stream_event(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"thought": True, "text": "Let me think."}, {"text": "Running"}],
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 40, "totalTokenCount": 40},
        }
    )
    state, deltas = _fold(state, first, {}, "antigravity:gemini-3-flash")
    assert [delta.kind for delta in deltas] == ["thinking", "text"]
    second = stream_event(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {"id": "call_7", "name": "shell", "args": {"command": "echo hi"}},
                                "thoughtSignature": "sig",
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 12, "totalTokenCount": 52},
        }
    )
    state, deltas = _fold(state, second, {}, "antigravity:gemini-3-flash")
    assert deltas == ()
    reply = _to_reply(state, "gemini-3-flash")
    assert reply.text == "Running"
    assert reply.thinking == "Let me think."
    assert reply.finish == "tool_calls"
    assert reply.usage.input_tokens == 40
    assert reply.usage.output_tokens == 12
    assert reply.tool_calls[0].id == "call_7"
    assert reply.tool_calls[0].arguments == {"command": "echo hi"}
    assert reply.tool_calls[0].extra == {"thoughtSignature": "sig"}


def test_a_model_the_listing_does_not_name_still_gets_traits() -> None:
    traits = assumed_traits("claude-sonnet-4-6")
    assert traits.context_length == 250_000
    assert traits.efforts == ()
    assert assumed_traits("gemini-9-flash").efforts == ("low", "medium", "high")


def test_a_delta_is_text_not_something_else() -> None:
    assert StreamDelta(kind="text", text="x").text == "x"
