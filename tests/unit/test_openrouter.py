"""OpenRouter (item 349): what its list of models says each model can do (a variant of a model too), how streamed
reasoning details join into whole ones and that only a codec that sends them back keeps them, what a request asks (the
reasoning level as OpenRouter's ``reasoning`` object, and the details of an assistant turn sent back only to the model
that gave them), that an answer not shaped as OpenRouter documents it is a protocol error, and how a model's routing
goes as the ``provider`` object and is named by a 404 it leaves no provider in (item 737)."""

import json
from pathlib import Path

import httpx
import pytest

import dotagent
from dotagent.config import OpenRouterConfig, load_config, load_default_config
from dotagent.core.errors import ConfigError, ProviderAccessError, ProviderProtocolError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, fold_chat_event, initial_chat_state, merge_details
from dotagent.llm.http import SseEvent
from dotagent.llm.openrouter import (
    OpenRouterProvider,
    catalog_of,
    classify_routed,
    listed_for,
    listed_model,
    model_info_of,
    parse_key,
    routing_for,
    writes_text,
)
from dotagent.llm.provider_errors import (
    WireError,
    classify_generic,
    classify_openrouter,
    parse_wire_error,
    status_for_stream_error,
)
from dotagent.llm.types import (
    ChatRequest,
    Effort,
    Message,
    ModelReply,
    StreamDelta,
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
        window=None,
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


# How OpenRouter chooses a model's provider (item 737): the request's ``provider`` object, sent through the real
# connector to a stand-in of OpenRouter on this process, which keeps the bodies it is sent. No model is called.
_DEEPSEEK: str = "deepseek/deepseek-v4.1-flash-1017"
_ROUTED: dict[str, object] = {
    _DEEPSEEK: {"sort": "throughput", "max_price": {"prompt": 0.3, "completion": 1.2}, "allow_fallbacks": False}
}
_LISTING: JsonObject = {
    "data": [
        {"id": _DEEPSEEK, "context_length": 128000, "supported_parameters": ["tools"], "pricing": {}},
        {"id": "openai/gpt-5.6-luna", "context_length": 128000, "supported_parameters": ["tools"], "pricing": {}},
    ]
}
_CHUNK: str = dumps_compact(
    {
        "id": "gen-1",
        "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
)


def _config(routing: dict[str, object]) -> OpenRouterConfig:
    fields: JsonObject = load_default_config().providers.openrouter.model_dump(mode="json")
    return OpenRouterConfig.model_validate({**fields, "routing": routing})


def _stand_in(sent: list[JsonObject], answer: httpx.Response | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=_LISTING)
        sent.append(json.loads(request.content))
        if answer is not None:
            return answer
        stream: str = f"data: {_CHUNK}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def _sent_bodies(routing: dict[str, object], models: tuple[str, ...]) -> list[JsonObject]:
    sent: list[JsonObject] = []
    provider: OpenRouterProvider = OpenRouterProvider(_config(routing), "sk-or-test")
    await provider.aclose()
    provider._client = _stand_in(sent)
    try:
        for model in models:
            reply: ModelReply = await provider.complete(
                _request(model, (user_message("Merhaba", ()),), "off"), _discard
            )
            assert reply.text == "ok"
    finally:
        await provider.aclose()
    return sent


async def _discard(delta: StreamDelta) -> None:
    return None


async def test_a_model_with_routing_sends_it_as_the_provider_object_and_one_without_sends_none() -> None:
    routed, plain = await _sent_bodies(_ROUTED, (_DEEPSEEK, "openai/gpt-5.6-luna"))
    assert routed["provider"] == {
        "sort": "throughput",
        "allow_fallbacks": False,
        "max_price": {"prompt": 0.3, "completion": 1.2},
    }
    assert "provider" not in plain
    # OpenCode's gateway relies on it, and OpenRouter's body keeps it so too.
    assert list(routed)[-1] == "stream" and list(plain)[-1] == "stream"


async def test_a_variant_takes_its_models_routing_and_its_own_id_comes_first() -> None:
    own: dict[str, object] = {**_ROUTED, f"{_DEEPSEEK}:floor": {"sort": "price"}}
    nitro, floor = await _sent_bodies(own, (f"{_DEEPSEEK}:nitro", f"{_DEEPSEEK}:floor"))
    assert nitro["model"] == f"{_DEEPSEEK}:nitro" and nitro["provider"] == _config(_ROUTED).routing[
        _DEEPSEEK
    ].model_dump(exclude_none=True)
    assert floor["provider"] == {"sort": "price"}
    assert list(nitro)[-1] == "stream"


def test_routing_for_finds_the_model_or_the_model_of_its_variant() -> None:
    routing = _config(_ROUTED).routing
    assert routing_for(routing, _DEEPSEEK) is routing[_DEEPSEEK]
    assert routing_for(routing, f"{_DEEPSEEK}:nitro") is routing[_DEEPSEEK]
    assert routing_for(routing, "openai/gpt-5.6-luna") is None


@pytest.mark.parametrize(
    ("setting", "problem"),
    [
        ('sort = "fastest"', "providers.openrouter.routing"),
        ('speed = "high"', "is not a setting this version of Dot knows"),
        ("max_price = { prompt = -1 }", "providers.openrouter.routing"),
        ("max_price = { prompt = 0.3, request = 1 }", "is not a setting this version of Dot knows"),
        ('only = "deepinfra"', "providers.openrouter.routing"),
    ],
)
def test_a_routing_value_or_key_dot_does_not_know_is_a_config_error(setting: str, problem: str, tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text(f'[providers.openrouter]\nrouting = {{ "{_DEEPSEEK}" = {{ {setting} }} }}\n', encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        load_config(path)
    assert problem in refused.value.message


def test_the_routing_in_the_defaults_is_none_and_the_example_of_the_defaults_loads(tmp_path: Path) -> None:
    assert load_default_config().providers.openrouter.routing == {}
    example: str = next(
        line.removeprefix("# ")
        for line in (Path(dotagent.__file__).parent / "defaults.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("# routing = ")
    )
    path: Path = tmp_path / "config.toml"
    path.write_text(f"[providers.openrouter]\n{example}\n", encoding="utf-8")
    routing = load_config(path).providers.openrouter.routing[_DEEPSEEK]
    assert routing.model_dump(exclude_none=True) == {
        "sort": "throughput",
        "max_price": {"prompt": 0.3, "completion": 1.2},
    }


# OpenRouter's 404 when the account's privacy settings, or the request's routing, leave no provider of the model.
_DATA_POLICY: str = (
    '{"error": {"message": "0 endpoints out of 1 requested are available matching your guardrail restrictions and data '
    'policy. We removed them for the following reasons:\\nZDR violation (account settings): 1 endpoint excluded", '
    '"code": 404}}'
)
_NO_ENDPOINTS: str = (
    '{"error": {"message": "No endpoints found that can handle the requested parameters. To learn more about provider '
    'routing, visit: https://openrouter.ai/docs/provider-routing", "code": 404}}'
)


def test_a_404_that_leaves_no_provider_names_the_routing_when_the_request_carried_one() -> None:
    routed = classify_routed(404, parse_wire_error(_DATA_POLICY), None, {})
    assert isinstance(routed, ProviderAccessError)
    assert "[providers.openrouter] routing" in routed.message and "settings/privacy" in routed.message
    assert "ZDR violation" in routed.message
    unrouted = classify_openrouter(404, parse_wire_error(_DATA_POLICY), None, {})
    assert isinstance(unrouted, ProviderAccessError) and "routing" not in unrouted.message
    no_endpoints = classify_routed(404, parse_wire_error(_NO_ENDPOINTS), None, {})
    assert isinstance(no_endpoints, ProviderAccessError)
    assert no_endpoints.message.startswith(
        "The routing set for the model in config.toml ([providers.openrouter] routing)"
    )
    allowed = classify_routed(
        404,
        parse_wire_error(
            '{"error": {"message": "No allowed providers are available for the selected model.", "code": 404}}'
        ),
        None,
        {},
    )
    assert isinstance(allowed, ProviderAccessError) and "[providers.openrouter] routing" in allowed.message
    # A 404 that the model lacks what the request asks is the request's, not the routing's (the pre-push review).
    capability = classify_routed(
        404,
        parse_wire_error('{"error": {"message": "No endpoints found that support image input", "code": 404}}'),
        None,
        {},
    )
    assert "routing" not in capability.message
    # Without a routing in the request, such a 404 is read as before.
    assert not isinstance(classify_openrouter(404, parse_wire_error(_NO_ENDPOINTS), None, {}), ProviderAccessError)


async def test_a_routed_request_that_leaves_no_provider_is_a_refused_access_through_the_connector() -> None:
    sent: list[JsonObject] = []
    provider: OpenRouterProvider = OpenRouterProvider(_config(_ROUTED), "sk-or-test")
    await provider.aclose()
    provider._client = _stand_in(sent, httpx.Response(404, text=_NO_ENDPOINTS))
    try:
        with pytest.raises(ProviderAccessError) as refused:
            await provider.complete(_request(_DEEPSEEK, (user_message("Merhaba", ()),), "off"), _discard)
    finally:
        await provider.aclose()
    assert "[providers.openrouter] routing" in refused.value.message
    assert len(sent) == 1 and sent[0]["provider"]
