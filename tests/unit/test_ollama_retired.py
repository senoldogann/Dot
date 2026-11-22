"""A cloud model Ollama's service retired is left out of the list of models, not the list with it (item 1195)."""

import json

import httpx
import pytest

from dotagent import doctor
from dotagent.agent.sizes import ContextSizes
from dotagent.config import AppConfig, OllamaConfig, load_default_config
from dotagent.core.errors import ProviderHttpError
from dotagent.llm.ollama import (
    DescribedModels,
    OllamaProvider,
    RetiredModels,
    Retirement,
    described_models,
    suggested_replacement,
)
from dotagent.llm.types import ModelInfo
from dotagent.server.routes_system import _provider_models
from dotagent.server.views import ProviderModels

_RETIRED: str = (
    "provider answered HTTP 410: mistral-large-3:675b was retired on November 9, 2026. Try glm-5.3-flash:cloud."
)


def _info(name: str) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=False,
        tools=True,
        thinking=True,
        context_length=131_072,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=False,
    )


def test_a_retired_model_is_set_apart_with_the_servers_words_and_the_others_are_listed() -> None:
    names: tuple[str, ...] = ("deepseek-v4.1-flash:cloud", "mistral-large-3:675b-cloud", "gemma4:cloud")
    outcomes: list[ModelInfo | BaseException] = [
        _info(names[0]),
        ProviderHttpError(_RETIRED, 410, {"url": "http://127.0.0.1:11434/api/show"}),
        _info(names[2]),
    ]
    described: DescribedModels = described_models(names, outcomes)
    assert [info.id for info in described.models] == [names[0], names[2]]
    assert described.retired == ((names[1], _RETIRED),)


def test_any_other_failure_fails_the_list() -> None:
    refused: ProviderHttpError = ProviderHttpError("provider answered HTTP 500: boom", 500, {})
    with pytest.raises(ProviderHttpError, match="HTTP 500"):
        described_models(("a", "b"), [_info("a"), refused])


# ------------------------------------------------------------------ remembered for a while, named, and said (item 1309)

_LISTED: tuple[str, ...] = ("minimax-m2.7:cloud", "gemma4:cloud")
_WORDS: str = (
    "minimax-m2.7 was retired on November 9, 2026. Try glm-5.3-flash:cloud. (ref: 45eaea78-e36d-444f-b5d9-9c3aff18a50d)"
)


class _Clock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now


class _Server:
    """A stand-in Ollama server listing two cloud models, one of which its service retired; it counts the asks."""

    def __init__(self) -> None:
        self.shown: list[str] = []
        self.listed: tuple[str, ...] = _LISTED

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in self.listed]})
        asked: str = json.loads(request.content)["model"]
        self.shown.append(asked)
        if asked == "minimax-m2.7:cloud":
            return httpx.Response(410, json={"error": _WORDS})
        return httpx.Response(200, json={"capabilities": ["tools"], "model_info": {"x.context_length": 131072}})


def _provider(server: _Server, retired: RetiredModels) -> OllamaProvider:
    config: OllamaConfig = load_default_config().providers.ollama
    provider: OllamaProvider = OllamaProvider(config, None, retired)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return provider


def test_the_replacement_is_the_model_the_services_words_say_to_try() -> None:
    assert suggested_replacement(_WORDS) == "glm-5.3-flash:cloud"
    assert (
        suggested_replacement("provider answered HTTP 410: x was retired. Try qwen3:480b-cloud.") == "qwen3:480b-cloud"
    )
    assert suggested_replacement("provider answered HTTP 410: x was retired.") is None
    assert suggested_replacement("") is None


def test_a_retirement_is_remembered_for_the_ttl_by_server_and_model_then_forgotten() -> None:
    clock: _Clock = _Clock()
    held: RetiredModels = RetiredModels(ttl_s=100.0, clock=clock)
    noted: Retirement = held.note("http://a", "m:cloud", _WORDS)
    assert noted.use == "glm-5.3-flash:cloud" and noted.model == "m:cloud"
    assert held.active("http://a", "m:cloud") == noted
    assert held.active("http://b", "m:cloud") is None and held.all("http://b") == ()
    clock.now = 99.0
    assert held.all("http://a") == (noted,)
    clock.now = 100.0
    assert held.active("http://a", "m:cloud") is None and held.all("http://a") == ()


async def test_a_retired_model_is_not_asked_about_at_every_refresh_and_is_asked_again_after_the_ttl() -> None:
    clock: _Clock = _Clock()
    server: _Server = _Server()
    provider: OllamaProvider = _provider(server, RetiredModels(ttl_s=6 * 3600.0, clock=clock))
    try:
        first: tuple[ModelInfo, ...] = await provider.list_models()
        assert [info.id for info in first] == ["gemma4:cloud"]
        assert [(one.model, one.use) for one in provider.retired()] == [("minimax-m2.7:cloud", "glm-5.3-flash:cloud")]
        for _ in range(3):
            assert [info.id for info in await provider.list_models()] == ["gemma4:cloud"]
        assert server.shown.count("minimax-m2.7:cloud") == 1, "the retired model was probed again at a refresh"
        clock.now = 6 * 3600.0 + 1
        await provider.list_models()
        assert server.shown.count("minimax-m2.7:cloud") == 2
        assert server.shown.count("gemma4:cloud") == 1, "a model that answers is described once, as before"
    finally:
        await provider.aclose()


async def test_a_model_the_owner_removed_is_forgotten_and_a_call_of_a_retired_model_is_remembered() -> None:
    server: _Server = _Server()
    held: RetiredModels = RetiredModels()
    provider: OllamaProvider = _provider(server, held)
    try:
        with pytest.raises(ProviderHttpError, match="410"):
            await provider.model_info("minimax-m2.7:cloud")
        assert [one.model for one in provider.retired()] == ["minimax-m2.7:cloud"]
        server.listed = ("gemma4:cloud",)
        await provider.list_models()
        assert provider.retired() == ()
    finally:
        await provider.aclose()


def test_the_doctor_says_which_role_names_a_retired_model_and_what_to_use(monkeypatch: pytest.MonkeyPatch) -> None:
    base: AppConfig = load_default_config()
    # A configuration that names the retired model nowhere.
    config: AppConfig = base.model_copy(
        update={
            "models": base.models.model_copy(
                update={
                    "main": "ollama:gemma4:cloud",
                    "review": "ollama:gemma4:cloud",
                    "helpers": [],
                    "standby": [],
                }
            )
        }
    )
    held: RetiredModels = RetiredModels()
    held.note(config.providers.ollama.base_url, "minimax-m2.7:cloud", _WORDS)
    monkeypatch.setattr(doctor, "RETIRED", held)
    # Nothing in the configuration names it: a passing row that says it is gone and what to use.
    (idle,) = doctor._retired_rows(config)
    assert idle.ok and not idle.required and "glm-5.3-flash:cloud" in idle.detail and "names it" not in idle.detail
    pinned: AppConfig = config.model_copy(
        update={
            "models": config.models.model_copy(
                update={
                    "main": "ollama:minimax-m2.7:cloud",
                    "standby": ["ollama:gemma4:cloud", "ollama:minimax-m2.7:cloud"],
                }
            )
        }
    )
    (row,) = doctor._retired_rows(pinned)
    assert not row.ok and row.required and row.name == "ollama model minimax-m2.7:cloud"
    assert "main, standby 2" in row.detail and "use glm-5.3-flash:cloud" in row.detail
    monkeypatch.setattr(doctor, "RETIRED", RetiredModels())
    assert doctor._retired_rows(pinned) == []


async def test_the_models_api_lists_the_retired_models_with_what_to_use() -> None:
    server: _Server = _Server()
    provider: OllamaProvider = _provider(server, RetiredModels())

    class _Router:
        async def provider(self, name: str) -> OllamaProvider:
            return provider

    config: AppConfig = load_default_config()
    try:
        view: ProviderModels = await _provider_models(
            _Router(),  # type: ignore[arg-type]
            "ollama",
            config.models.effort,
            config.agent,
            ContextSizes({}),
        )
    finally:
        await provider.aclose()
    assert [option.ref for option in view.models] == ["ollama:gemma4:cloud"] and view.error is None
    assert [(one.model, one.use) for one in view.retired] == [("minimax-m2.7:cloud", "glm-5.3-flash:cloud")]
