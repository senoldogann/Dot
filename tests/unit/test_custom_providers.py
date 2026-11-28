"""Custom providers (item 1949): the configuration's rules, the config file's edit, a model ``<name>:<model>``
routed through each of the three protocols to a recorded answer (no live call), and Fill from docs on saved pages."""

import json
import tomllib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue, ValidationError

from dotagent.agent.helpers import keyed_providers
from dotagent.config import AppConfig, CustomProviderConfig, load_config, load_default_config
from dotagent.config_edit import set_custom_provider
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.llm import custom as custom_module
from dotagent.llm.calls import CallOrigin, ModelCall
from dotagent.llm.custom import CustomProvider, check_call
from dotagent.llm.custom_docs import DocsProposal, merged_proposal, needs_model, propose_from_text
from dotagent.llm.router import ModelRouter, parse_model_ref
from dotagent.llm.types import ChatRequest, ModelReply, StreamDelta, system_message, user_message
from dotagent.web.extract import ExtractedField, Extraction
from dotagent.web.text import html_to_text

FIXTURES: Path = Path(__file__).parent.parent / "fixtures" / "custom_providers"


def _config(table: JsonObject) -> AppConfig:
    merged: JsonObject = load_default_config().model_dump(mode="json")
    providers = merged["providers"]
    assert isinstance(providers, dict)
    providers["custom"] = table
    return AppConfig.model_validate(merged)


def _provider(**fields: JsonValue) -> JsonObject:
    return {
        "base_url": "https://api.example-cloud.dev/openai/v1",
        "protocol": "chat",
        "api_key_env": "DOT_CUSTOM_GROQ_API_KEY",
        "models": [{"id": "llama-4-scout"}],
        **fields,
    }


def test_a_provider_with_a_model_is_taken_and_its_models_may_be_named_anywhere() -> None:
    config: AppConfig = _config({"groq": _provider()})
    assert config.providers.custom["groq"].models[0].tools
    merged: JsonObject = config.model_dump(mode="json")
    models = merged["models"]
    assert isinstance(models, dict)
    models["standby"] = ["groq:llama-4-scout"]
    assert AppConfig.model_validate(merged).models.standby == ["groq:llama-4-scout"]


@pytest.mark.parametrize(
    ("name", "fields", "words"),
    [
        ("openai", {}, "cannot name a custom provider"),
        ("typesafe", {}, "cannot name a custom provider"),
        ("Groq", {}, "cannot name a custom provider"),
        ("groq", {"base_url": "http://api.example-cloud.dev/v1"}, "https://"),
        ("groq", {"base_url": "https://user:pw@api.example-cloud.dev/v1"}, "user name"),
        ("groq", {"headers": {"X-Api-Key": "sk-1"}}, "carries a secret"),
        ("groq", {"headers": {"Authorization": "Bearer sk-1"}}, "carries a secret"),
        ("groq", {"api_key_env": "OPENAI_API_KEY"}, "key of a built-in provider"),
        ("groq", {"models": []}, "at least one model"),
        ("groq", {"models": [{"id": "a"}, {"id": "a"}]}, "listed twice"),
        ("groq", {"models": [{"id": "a", "efforts": ["loud"]}]}, "unknown reasoning labels"),
    ],
)
def test_a_provider_the_rules_refuse_is_refused_with_why(name: str, fields: JsonObject, words: str) -> None:
    with pytest.raises(ValidationError) as refused:
        _config({name: _provider(**fields)})
    assert words in str(refused.value)


def test_a_server_on_this_mac_may_be_plain_http_and_take_no_key() -> None:
    config: AppConfig = _config(
        {"lm": _provider(base_url="http://127.0.0.1:1234/v1", api_key_env="", models=[], list_models=True)}
    )
    assert config.providers.custom["lm"].api_key_env == ""


def test_two_providers_cannot_share_a_key() -> None:
    with pytest.raises(ValidationError, match="same key"):
        _config({"one": _provider(), "two": _provider()})


def test_a_model_of_a_custom_provider_that_is_not_configured_is_refused() -> None:
    merged: JsonObject = load_default_config().model_dump(mode="json")
    models = merged["models"]
    assert isinstance(models, dict)
    models["main"] = "groq:llama-4-scout"
    with pytest.raises(ValidationError, match=r"models\.main names groq:llama-4-scout"):
        AppConfig.model_validate(merged)


def test_the_table_settings_writes_reads_back_and_goes_whole(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('# mine\n[models]\neffort = "high"\n\n[judge]\nprovider = "none"\n', encoding="utf-8")
    groq: CustomProviderConfig = CustomProviderConfig.model_validate(
        _provider(headers={"X-Title": "Dot"}, models=[{"id": "llama-4-scout", "vision": True, "efforts": ["low"]}])
    )
    set_custom_provider(path, "groq", groq)
    assert load_config(path).providers.custom["groq"] == groq
    changed: CustomProviderConfig = groq.model_copy(update={"list_models": True})
    set_custom_provider(path, "groq", changed)
    assert load_config(path).providers.custom["groq"] == changed
    set_custom_provider(path, "groq", None)
    text: str = path.read_text(encoding="utf-8")
    assert "custom" not in text and text.startswith("# mine\n")
    assert tomllib.loads(text) == {"models": {"effort": "high"}, "judge": {"provider": "none"}}


# --------------------------------------------------------------------------------------------- routing


class _Seen:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []


def _answering(fixture: str, seen: _Seen) -> Callable[[float, float], httpx.AsyncClient]:
    """A client whose server answers every call with the recorded stream ``fixture``, as the provider sent it."""
    body: bytes = (FIXTURES / fixture).read_bytes()

    def handle(request: httpx.Request) -> httpx.Response:
        seen.requests.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return lambda connect_s, read_s: httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def _record(call: ModelCall) -> None:
    return None


async def _discard(delta: StreamDelta) -> None:
    return None


def _request(model: str) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(system_message("Be brief."), user_message("Say OK.", ())),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="off",
        speed="standard",
        session_key="s",
        cache_mark=None,
        prompt_reuse="single",
        window=None,
    )


@pytest.mark.parametrize(
    ("protocol", "fixture", "path", "auth"),
    [
        ("chat", "chat.sse", "/v1/chat/completions", ("authorization", "Bearer sk-test-key")),
        ("responses", "responses.sse", "/v1/responses", ("authorization", "Bearer sk-test-key")),
        ("messages", "messages.sse", "/v1/messages", ("x-api-key", "sk-test-key")),
    ],
)
async def test_a_custom_model_goes_through_the_router_to_its_base_url_on_its_protocol(
    protocol: str, fixture: str, path: str, auth: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: _Seen = _Seen()
    monkeypatch.setattr(custom_module, "build_client", _answering(fixture, seen))
    monkeypatch.setenv("DOT_CUSTOM_ACME_API_KEY", "sk-test-key")
    config: AppConfig = _config(
        {
            "acme": _provider(
                base_url="https://api.acme.example/v1",
                protocol=protocol,
                api_key_env="DOT_CUSTOM_ACME_API_KEY",
                headers={"X-Title": "Dot"},
                models=[{"id": "m-2", "max_output": 4096}],
            )
        }
    )
    router: ModelRouter = ModelRouter(config, _record, {})
    try:
        reply: ModelReply = await router.complete(
            parse_model_ref("acme:m-2"), _request("m-2"), _discard, CallOrigin(run_id=None, purpose="step")
        )
    finally:
        await router.aclose()
    assert reply.text == "OK"
    assert reply.usage.output_tokens == 1
    sent: httpx.Request = seen.requests[0]
    assert str(sent.url) == f"https://api.acme.example{path}"
    assert sent.headers[auth[0]] == auth[1]
    assert sent.headers["x-title"] == "Dot"
    assert json.loads(sent.content)["model"] == "m-2"
    assert router.address(parse_model_ref("acme:m-2")) == "https://api.acme.example/v1"


async def test_a_provider_without_a_key_sends_none_and_one_it_lists_is_assumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: _Seen = _Seen()
    monkeypatch.setattr(custom_module, "build_client", _answering("chat.sse", seen))
    config: CustomProviderConfig = CustomProviderConfig.model_validate(
        _provider(base_url="http://localhost:1234/v1", api_key_env="", models=[], list_models=True)
    )
    provider: CustomProvider = CustomProvider("lm", config, None)
    try:
        info = await provider.model_info("anything")
        reply: ModelReply = await provider.complete(_request("anything"), _discard)
    finally:
        await provider.aclose()
    assert info.tools and info.window_source == "assumed"
    assert reply.text == "OK"
    assert "authorization" not in seen.requests[0].headers


async def test_a_model_the_provider_does_not_name_is_refused_before_any_call() -> None:
    provider: CustomProvider = CustomProvider("acme", CustomProviderConfig.model_validate(_provider()), "k")
    try:
        with pytest.raises(ConfigError, match="lists no model"):
            await provider.model_info("other")
    finally:
        await provider.aclose()


async def test_an_unknown_provider_is_refused_by_the_router_and_one_added_is_served_at_once() -> None:
    router: ModelRouter = ModelRouter(load_default_config(), _record, {})
    try:
        with pytest.raises(ConfigError, match="unknown provider"):
            await router.provider("acme")
        await router.set_custom("acme", CustomProviderConfig.model_validate(_provider(api_key_env="")))
        assert (await router.provider("acme")).name == "acme"
        await router.set_custom("acme", None)
        with pytest.raises(ConfigError, match="unknown provider"):
            await router.provider("acme")
        with pytest.raises(ConfigError, match="built-in"):
            await router.set_custom("openai", None)
    finally:
        await router.aclose()


async def test_the_owners_test_says_a_refused_key_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})

    monkeypatch.setattr(
        custom_module,
        "build_client",
        lambda connect_s, read_s: httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )
    checked = await check_call("acme", CustomProviderConfig.model_validate(_provider()), "bad", "llama-4-scout")
    assert not checked.ok
    assert "401" in checked.detail and "Invalid API Key" in checked.detail


# --------------------------------------------------------------------------------------------- fill from docs


def _page(name: str) -> str:
    html: str = (FIXTURES / name).read_text(encoding="utf-8")
    page = html_to_text(html, "https://docs.example-cloud.dev/openai")
    return f"{page.title}\n{page.text}"


def test_an_openai_compatible_page_gives_its_base_protocol_and_models_without_a_model() -> None:
    proposal: DocsProposal = propose_from_text(
        "https://docs.example-cloud.dev/openai", _page("docs_openai_compatible.html")
    )
    assert proposal.base_url == "https://api.example-cloud.dev/openai/v1"
    assert proposal.protocol == "chat"
    assert proposal.models == ["llama-4-scout-17b", "qwen3-32b"]
    assert not needs_model(proposal)


def test_an_anthropic_compatible_page_gives_the_messages_base() -> None:
    proposal: DocsProposal = propose_from_text(
        "https://docs.sample-ai.example/anthropic", _page("docs_anthropic_compatible.html")
    )
    assert proposal.base_url == "https://api.sample-ai.example/anthropic/v1"
    assert proposal.protocol == "messages"
    assert proposal.models == ["sample-m2"]


def test_a_vague_page_asks_the_model_and_keeps_only_what_the_page_holds() -> None:
    text: str = _page("docs_vague.html")
    plain: DocsProposal = propose_from_text("https://docs.vague-labs.example/start", text)
    assert needs_model(plain)
    assert plain.base_url is None and plain.protocol is None

    def field(value: str) -> ExtractedField:
        return ExtractedField(value=value, quote="", verified=False)

    said: Extraction = Extraction(
        source="https://docs.vague-labs.example/start",
        fields={
            "base_url": field("https://inference.vague-labs.example"),
            "protocol": field("Chat"),
            "models": field("vague-large, vague-imagined"),
        },
    )
    merged: DocsProposal = merged_proposal(plain, said, text)
    assert merged.base_url == "https://inference.vague-labs.example"
    assert merged.protocol == "chat"
    assert merged.models == ["vague-large"]
    assert merged.source == "model"
    invented: Extraction = said.model_copy(
        update={"fields": {**said.fields, "base_url": field("https://evil.example/v1")}}
    )
    assert merged_proposal(plain, invented, text).base_url is None


async def test_a_custom_model_stands_in_the_standby_and_helper_roster_by_its_own_key() -> None:
    """The review of item 1949: a custom provider's model as a standby or helper model raised "unknown provider" in
    the roster; it is keyed by its own key (or none), and one removed while a setting still names it has nothing."""
    config: AppConfig = _config({"lm": _provider(api_key_env="", base_url="http://localhost:1234/v1")})
    keyed: frozenset[str] = await keyed_providers(
        config.providers, config.providers.custom, ["lm:llama-4-scout", "gone:model"]
    )
    assert keyed == frozenset({"lm"})
