"""Model routing: ``provider:model`` references resolved to connectors that are created on first use."""

import asyncio
import time

from dotagent.config import AppConfig
from dotagent.core.errors import (
    ConfigError,
    MissingSecretError,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.llm.anthropic import AnthropicProvider
from dotagent.llm.calls import CANCELLED, CallOrigin, CallRecorder, ModelCall
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.openai import OpenAIProvider
from dotagent.llm.opencode_go import OpenCodeGoProvider
from dotagent.llm.types import ChatProvider, ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.secrets import require_secret, resolve_secret

log = get_logger(__name__)


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


class Pause(FrozenModel):
    """A provider that cannot answer for a while: why, and until when (seconds since the epoch)."""

    reason: str
    until: float


def unavailable_reason(error: Exception) -> str | None:
    """Why the provider that raised ``error`` cannot answer for a while: its quota or its rate is spent, its key was
    refused, or it has no key. ``None`` for an error that says nothing about that, such as a transport or protocol
    error. The connector has retried what can be retried before such an error reaches its caller."""
    if isinstance(error, ProviderQuotaError):
        return "quota exhausted"
    if isinstance(error, ProviderRateLimitError):
        return "rate limited"
    if isinstance(error, ProviderAuthError):
        return "key refused"
    if isinstance(error, MissingSecretError):
        return "no key"
    return None


def pause_for(error: Exception, cooldown_s: float, now: float) -> Pause | None:
    """The pause of the provider that raised ``error`` at ``now``: for the time a quota or rate error named, else for
    ``cooldown_s``; ``None`` when the error says nothing about the provider being unable to answer."""
    reason: str | None = unavailable_reason(error)
    if reason is None:
        return None
    named: float | None = (
        error.retry_after_s if isinstance(error, ProviderQuotaError | ProviderRateLimitError) else None
    )
    wait: float = cooldown_s if named is None or named <= 0 else named
    return Pause(reason=reason, until=now + wait)


def _answered(ref: ModelRef, request: ChatRequest, origin: CallOrigin, seconds: float, reply: ModelReply) -> ModelCall:
    """A call that got its reply, with the tokens its provider reported."""
    return ModelCall(
        run_id=origin.run_id,
        purpose=origin.purpose,
        model=ref.label,
        session_key=request.session_key,
        seconds=seconds,
        input_tokens=reply.usage.input_tokens,
        output_tokens=reply.usage.output_tokens,
        cached_tokens=reply.usage.cached_tokens,
        error=None,
    )


def _failed(ref: ModelRef, request: ChatRequest, origin: CallOrigin, seconds: float, error: str) -> ModelCall:
    """A call that ended without a reply: no provider reported tokens for it."""
    return ModelCall(
        run_id=origin.run_id,
        purpose=origin.purpose,
        model=ref.label,
        session_key=request.session_key,
        seconds=seconds,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        error=error,
    )


class ModelRouter:
    """Owns the provider connectors and sends each request to the one its model reference names. Every call it sends
    is measured and handed to ``recorder``.

    A provider whose call failed with an error that says it cannot answer for a while (:func:`pause_for`) is paused
    until a call it answers or the end of the pause. The router still sends it every call it is given: the pauses only
    tell :mod:`dotagent.agent.helpers` which helper or standby model to choose."""

    def __init__(self, config: AppConfig, recorder: CallRecorder) -> None:
        self._config: AppConfig = config
        self._recorder: CallRecorder = recorder
        self._providers: dict[str, ChatProvider] = {}
        self._pauses: dict[str, Pause] = {}
        # The names served by a connector given through `register`; `retire` leaves them alone.
        self._registered: set[str] = set()
        # The connectors `retire` took out of service, closed with the others at `aclose`.
        self._retired: list[ChatProvider] = []
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
            self._registered.add(name)

    async def retire(self, name: str) -> None:
        """Make the next request to the provider ``name`` create a new connector, with the key in use then. The old
        connector is closed at ``aclose``, so a request it is serving is not cut. A connector given through
        ``register`` stays in service."""
        async with self._lock:
            if name in self._registered:
                return
            retired: ChatProvider | None = self._providers.pop(name, None)
            if retired is not None:
                self._retired.append(retired)

    async def complete(self, ref: ModelRef, request: ChatRequest, sink: DeltaSink, origin: CallOrigin) -> ModelReply:
        """Send ``request`` to the provider of ``ref`` and hand the recorder what the call cost: its seconds and tokens,
        or, for a call that failed, the type of its error (:data:`CANCELLED` for one that a stop or the caller's
        timeout cut off; a connector that cannot be made, such as one without its key, fails the call too). The
        seconds include the retries the connector makes and its waits between them. A failure is raised after it is
        recorded; after a reply, a recorder that fails raises its own error, as any failed write of the run does. A
        failure that says the provider cannot answer for a while pauses the provider, and a reply ends its pause."""
        started: float = time.monotonic()
        try:
            provider: ChatProvider = await self.provider(ref.provider)
            reply: ModelReply = await provider.complete(request, sink)
        except asyncio.CancelledError:
            await self._record_failure(_failed(ref, request, origin, time.monotonic() - started, CANCELLED))
            raise
        except Exception as error:
            self._pause(ref, error)
            await self._record_failure(_failed(ref, request, origin, time.monotonic() - started, type(error).__name__))
            raise
        self._pauses.pop(ref.provider, None)
        await self._recorder(_answered(ref, request, origin, time.monotonic() - started, reply))
        return reply

    def _pause(self, ref: ModelRef, error: Exception) -> None:
        pause: Pause | None = pause_for(error, self._config.models.helper_cooldown_s, time.time())
        if pause is None:
            return
        self._pauses[ref.provider] = pause
        log.warning("provider_paused", provider=ref.provider, model=ref.label, reason=pause.reason, until=pause.until)

    def paused(self, now: float) -> dict[str, Pause]:
        """The providers that cannot answer for a while at ``now`` (seconds since the epoch), with why and until when."""
        return {name: pause for name, pause in self._pauses.items() if pause.until > now}

    async def _record_failure(self, call: ModelCall) -> None:
        """Record a call that failed or was cut off. Its caller gets the call's own error or cancellation: a recorder
        that fails here (the store cannot be written) is logged instead, so it cannot replace that outcome or turn a
        cancellation into another error."""
        try:
            await self._recorder(call)
        except Exception as error:
            log.error(
                "model_call_not_recorded",
                run_id=call.run_id,
                purpose=call.purpose,
                model=call.model,
                call_error=call.error,
                error_type=type(error).__name__,
                error=str(error),
            )

    async def model_info(self, ref: ModelRef) -> ModelInfo:
        provider: ChatProvider = await self.provider(ref.provider)
        return await provider.model_info(ref.model)

    async def aclose(self) -> None:
        async with self._lock:
            providers: list[ChatProvider] = [*self._providers.values(), *self._retired]
            self._providers.clear()
            self._retired.clear()
        for provider in providers:
            await provider.aclose()
