"""The Cloud Code request Dot builds and the login it renews: the answers to the calls of one turn go in one content, a
signature goes back only to the model that wrote it, a 5xx from the token endpoint is asked again, and a revoked login
says so. A login pattern that matches nothing is a provider error rather than an IndexError."""

import json
from pathlib import Path

import httpx
import pytest

from dotagent.core.errors import ProviderAuthError
from dotagent.core.types import JsonObject
from dotagent.llm.antigravity import (
    Credentials,
    TokenSource,
    accepts_the_end_of_a_stream,
    build_request_body,
    read_credentials,
    traits_of,
)
from dotagent.llm.types import ChatRequest, Message, ToolCall

GEMINI_FLASH: JsonObject = {
    "displayName": "Gemini 2.5 Flash",
    "model": "gemini-2.5-flash-thinking",
    "maxTokens": 65536,
    "thinkingLevel": "medium",
}


def _message(**fields: object) -> Message:
    base: dict[str, object] = {
        "role": "user",
        "content": "",
        "images": (),
        "tool_calls": (),
        "tool_call_id": None,
        "tool_name": None,
        "thinking": None,
        "raw": (),
        "raw_origin": None,
    }
    base.update(fields)
    return Message(**base)  # type: ignore[arg-type]


def _body(messages: tuple[Message, ...], model: str = "gemini-2.5-flash-thinking") -> JsonObject:
    request = ChatRequest(
        model=model,
        messages=messages,
        tools=(),
        temperature=None,
        max_output_tokens=64,
        effort="minimal",
        speed="standard",
        session_key="test",
        cache_mark=None,
        prompt_reuse="single",
        window=None,
    )
    return build_request_body(request, traits_of(model, GEMINI_FLASH), "p")


def test_the_answers_to_two_calls_of_one_turn_are_one_content() -> None:
    body = _body(
        (
            _message(role="user", content="iki dosyayı oku"),
            _message(
                role="assistant",
                tool_calls=(
                    ToolCall(id="a", name="read_file", arguments={"path": "a"}),
                    ToolCall(id="b", name="read_file", arguments={"path": "b"}),
                ),
            ),
            _message(role="tool", tool_call_id="a", tool_name="read_file", content="A"),
            _message(role="tool", tool_call_id="b", tool_name="read_file", content="B"),
        )
    )
    contents = body["request"]["contents"]  # type: ignore[index]
    assert [content["role"] for content in contents] == ["user", "model", "user"]
    assert len(contents[2]["parts"]) == 2


def test_another_models_signature_is_not_sent_to_this_one() -> None:
    other = ToolCall(
        id="a",
        name="read_file",
        arguments={"path": "a"},
        extra={"thoughtSignature": "sig"},
        extra_origin="antigravity:gemini-3-flash",
    )
    body = _body(
        (
            _message(role="user", content="oku"),
            _message(role="assistant", tool_calls=(other,)),
        )
    )
    parts = body["request"]["contents"][1]["parts"]  # type: ignore[index]
    assert "thoughtSignature" not in parts[0]


def test_a_login_pattern_that_matches_nothing_names_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ProviderAuthError) as caught:
        read_credentials(str(tmp_path / "nothing" / "*" / "acp_token.json"))
    assert "sign in to Antigravity once" in json.dumps(caught.value.context)


def test_a_token_endpoint_that_is_not_googles_refuses_the_login(tmp_path: Path) -> None:
    login = tmp_path / "acp_token.json"
    login.write_text(
        json.dumps(
            {
                "client_id": "id",
                "client_secret": "secret",
                "refresh_token": "refresh",
                "token_uri": "https://token.example.com/token",
            }
        )
    )
    with pytest.raises(ProviderAuthError) as caught:
        read_credentials(str(login))
    assert "not Google's" in caught.value.message


def _source(handler: httpx.MockTransport) -> TokenSource:
    credentials = Credentials(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        project="p",
    )
    return TokenSource(credentials, httpx.AsyncClient(transport=handler))


async def test_a_token_endpoint_having_a_bad_moment_is_asked_again() -> None:
    answers = iter(
        [
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json={"access_token": "tok", "expires_in": 3600}),
        ]
    )
    source = _source(httpx.MockTransport(lambda request: next(answers)))
    assert await source.token() == "tok"


async def test_a_revoked_login_says_to_sign_in_again() -> None:
    source = _source(
        httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            )
        )
    )
    with pytest.raises(ProviderAuthError) as caught:
        await source.token()
    assert "sign in to Antigravity again" in json.dumps(caught.value.context)


def test_a_stream_cut_before_its_finish_reason_is_no_answer() -> None:
    assert not accepts_the_end_of_a_stream()
