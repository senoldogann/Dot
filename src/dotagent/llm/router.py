"""Model routing: ``provider:model`` references resolved to connectors that are created on first use."""

import asyncio

from dotagent.config import AppConfig
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel
from dotagent.llm.anthropic import AnthropicProvider
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.openai import OpenAIProvider
from dotagent.llm.opencode_go import OpenCodeGoProvider
from dotagent.llm.types import ChatProvider, ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.secrets import require_secret, resolve_secret


class ModelRef(FrozenModel):
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


KNOWN_PROVIDERS: tuple[str, ...] = ("ollama", "openai", "opencode_go", "anthropic")


def parse_model_ref(text: str) -> ModelRef:
    """Split ``provider:model`` at the first colon (model names may contain colons, e.g. ``qwen3:8b``)."""
    provider, separator, model = text.strip().partition(":")
    if separator == "" or model == "" or provider not in KNOWN_PROVIDERS:
        raise ConfigError(
            "a model reference must look like '<provider>:<model>'",
            {"value": text, "providers": list(KNOWN_PROVIDERS)},
        )
    return ModelRef(provider=provider, model=model)


class ModelRouter:
    """Owns the provider connectors and sends each request to the one its model reference names."""

    def __init__(self, config: AppConfig) -> None:
        self._config: AppConfig = config
        self._providers: dict[str, ChatProvider] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _create(self, name: str) -> ChatProvider:
        match name:
            case "ollama":
                key: str | None = await asyncio.to_thread(resolve_secret, self._config.providers.ollama.api_key_env)
                return OllamaProvider(self._config.providers.ollama, key)
            case "openai":
                openai = self._config.providers.openai
                api_key: str = await asyncio.to_thread(require_secret, openai.api_key_env, "the openai provider")
                organization: str | None = await asyncio.to_thread(resolve_secret, openai.organization_env)
                project: str | None = await asyncio.to_thread(resolve_secret, openai.project_env)
                return OpenAIProvider(openai, api_key, organization, project)
            case "opencode_go":
                go = self._config.providers.opencode_go
                go_key: str = await asyncio.to_thread(require_secret, go.api_key_env, "the opencode_go provider")
                return OpenCodeGoProvider(go, go_key)
            case "anthropic":
                claude = self._config.providers.anthropic
                claude_key: str = await asyncio.to_thread(require_secret, claude.api_key_env, "the anthropic provider")
                return AnthropicProvider(claude, claude_key)
            case _:
                raise ConfigError("unknown provider", {"provider": name})

    async def provider(self, name: str) -> ChatProvider:
        async with self._lock:
            existing: ChatProvider | None = self._providers.get(name)
            if existing is not None:
                return existing
            created: ChatProvider = await self._create(name)
            self._providers[name] = created
            return created

    async def register(self, name: str, provider: ChatProvider) -> None:
        """Serve the provider name ``name`` with ``provider`` instead of a connector made from the configuration (the
        scripted model of the evals and of the tests). It has to come before the name is used for the first time."""
        if name not in KNOWN_PROVIDERS:
            raise ConfigError("unknown provider", {"provider": name, "providers": list(KNOWN_PROVIDERS)})
        async with self._lock:
            if name in self._providers:
                raise ConfigError("a connector for this provider exists already", {"provider": name})
            self._providers[name] = provider

    async def complete(self, ref: ModelRef, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        provider: ChatProvider = await self.provider(ref.provider)
        return await provider.complete(request, sink)

    async def model_info(self, ref: ModelRef) -> ModelInfo:
        provider: ChatProvider = await self.provider(ref.provider)
        return await provider.model_info(ref.model)

    async def aclose(self) -> None:
        async with self._lock:
            providers: list[ChatProvider] = list(self._providers.values())
            self._providers.clear()
        for provider in providers:
            await provider.aclose()
