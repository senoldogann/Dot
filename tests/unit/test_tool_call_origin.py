"""A tool call's replay state goes back only to the model that wrote it: Gemini's thought signature with the call, and
nothing at all when the conversation moves to another provider's model."""

from dotagent.llm.codec_chat import ChatOptions, encode_messages
from dotagent.llm.types import Message, ToolCall

OPTIONS = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=False,
    replay_reasoning=False,
    cache_key_in_body=False,
    reasoning_object=False,
    replay_details=False,
)
SIGNATURE = {"google": {"thought_signature": "abc"}}


def _conversation(extra_origin: str) -> tuple[Message, ...]:
    return (
        Message(
            role="user",
            content="dosyayı oku",
            images=(),
            tool_calls=(),
            tool_call_id=None,
            tool_name=None,
            thinking=None,
            raw=(),
            raw_origin=None,
        ),
        Message(
            role="assistant",
            content="",
            images=(),
            tool_calls=(
                ToolCall(id="a", name="read_file", arguments={"path": "a"}, extra=SIGNATURE, extra_origin=extra_origin),
            ),
            tool_call_id=None,
            tool_name=None,
            thinking=None,
            raw=(),
            raw_origin=None,
        ),
    )


def _extra_of(call_origin: str, encoding_origin: str) -> object:
    wire = encode_messages(_conversation(call_origin), OPTIONS, encoding_origin)
    return wire[1]["tool_calls"][0].get("extra_content")


def test_the_signature_goes_back_to_the_model_that_wrote_it() -> None:
    origin = "antigravity:gemini-2.5-flash-thinking"
    assert _extra_of(origin, origin) == SIGNATURE


def test_another_models_turn_is_not_handed_a_signature_it_never_wrote() -> None:
    assert _extra_of("antigravity:gemini-2.5-flash-thinking", "google:gemini-3.5-flash-lite") is None
