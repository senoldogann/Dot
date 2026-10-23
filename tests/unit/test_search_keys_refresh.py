"""The keys of web_search's hosted rungs are read again when their provider is retired, as Settings › Providers does
on a saved or removed key, and a Keychain that refuses does not stop the daemon (item 558)."""

import pytest

from dotagent import runtime
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter


async def _recorded(call: ModelCall) -> None:
    del call


def test_a_keychain_that_refuses_is_no_key_for_a_service_the_daemon_starts_without(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(name: str) -> str | None:
        raise ConfigError("the macOS Keychain refused the lookup", {"name": name, "returncode": 36})

    monkeypatch.setattr(runtime, "resolve_secret", refused)
    assert runtime._optional_secret("OLLAMA_API_KEY") is None


async def test_a_retired_provider_has_its_search_key_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    config: AppConfig = load_default_config()
    held: dict[str, str] = {}
    monkeypatch.setattr(runtime, "resolve_secret", held.get)
    keys: runtime.SearchKeys = runtime.SearchKeys(ollama=False, anthropic=False)
    router: ModelRouter = ModelRouter(config, _recorded, {})
    told: list[str] = []

    async def heard(provider: str) -> None:
        told.append(provider)
        await runtime._refresh_search_key(config, keys, provider)

    router.on_retire(heard)
    try:
        # A key saved: the provider is retired, and the rung is climbed from the next search.
        held[config.providers.ollama.api_key_env] = "ollama-key-saved-in-settings"
        await router.retire("ollama")
        assert keys.ollama is True
        assert keys.anthropic is False
        held[config.providers.anthropic.api_key_env] = "anthropic-key-saved-in-settings"
        await router.retire("anthropic")
        assert keys.anthropic is True
        # A key removed: the rung is left out again.
        del held[config.providers.ollama.api_key_env]
        await router.retire("ollama")
        assert keys.ollama is False
        # Another provider's key changes nothing of the ladder.
        await router.retire("openai")
        assert (keys.ollama, keys.anthropic) == (False, True)
        assert told == ["ollama", "anthropic", "ollama", "openai"]
    finally:
        await router.aclose()
