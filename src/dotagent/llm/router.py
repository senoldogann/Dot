"""Model routing: ``provider:model`` references resolved to connectors that are created on first use."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence

from dotagent.agent.sizes import ContextSizes
from dotagent.config import AppConfig
from dotagent.core.errors import (
    ConfigError,
    MissingSecretError,
    ProviderAccessError,
    ProviderAuthError,
    ProviderCreditError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.llm.anthropic import FAST_MODE_MODELS, AnthropicProvider
from dotagent.llm.calls import CANCELLED, CallOrigin, CallRecorder, ModelCall
from dotagent.llm.ollama import OllamaProvider
from dotagent.llm.openai import OpenAIProvider
from dotagent.llm.opencode import OpenCodeProvider
from dotagent.llm.opencode_go import OpenCodeGoProvider
from dotagent.llm.openrouter import OpenRouterProvider
from dotagent.llm.types import (
    KNOWN_PROVIDERS,
    ChatProvider,
    ChatRequest,
    DeltaSink,
    ModelInfo,
    ModelReply,
    StreamDelta,
    grown_window,
    split_model_ref,
)
from dotagent.opencode.bridge import ToolBridge
from dotagent.opencode.server import OpenCodeServer
from dotagent.secrets import optional_secret, require_secret

log = get_logger(__name__)


class ModelRef(FrozenModel):
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_model_ref(text: str) -> ModelRef:
    """Split ``provider:model`` at the first colon (model names may contain colons, e.g. ``qwen3:8b``)."""
    split: tuple[str, str] | None = split_model_ref(text)
    if split is None:
        raise ConfigError(
            "a model reference must look like '<provider>:<model>'",
            {"value": text, "providers": list(KNOWN_PROVIDERS)},
        )
    return ModelRef(provider=split[0], model=split[1])


def offers_fast_mode(ref: ModelRef) -> bool:
    """Whether the model ``ref`` names has fast mode (item 223): some of Anthropic's, as its page names them."""
    return ref.provider == "anthropic" and ref.model in FAST_MODE_MODELS


class Pause(FrozenModel):
    """A model that cannot answer for a while: why, and until when (seconds since the epoch)."""

    reason: str
    until: float


# The longest pause: a provider that names a longer wait (or an endless one) is asked again after a day.
MAX_PAUSE_S: float = 86_400.0


def unavailable_reason(error: Exception) -> str | None:
    """Why the model whose call raised ``error`` cannot answer for a while: the quota or the rate of its provider is
    spent, the credits of its account are (OpenRouter's 402), the plan does not include the model (any other HTTP 402),
    its key was refused (HTTP 401), the access to it was refused (HTTP 403), or it has no key. ``None`` for an error
    that says nothing about that, such as a transport or protocol error. The connector has retried what can be retried
    before such an error reaches its caller."""
    if isinstance(error, ProviderCreditError):
        return "out of credits"
    if isinstance(error, ProviderQuotaError):
        return "not in the plan" if error.status == 402 else "quota exhausted"
    if isinstance(error, ProviderRateLimitError):
        return "rate limited"
    if isinstance(error, ProviderAuthError):
        return "key refused"
    if isinstance(error, ProviderAccessError):
        return "access refused"
    if isinstance(error, MissingSecretError):
        return "no key"
    return None


def pause_for(error: Exception, cooldown_s: float, now: float) -> Pause | None:
    """The pause of the model whose call raised ``error`` at ``now``: for the time a quota or rate error named (at most
    :data:`MAX_PAUSE_S`), else for ``cooldown_s``; ``None`` when the error says nothing about the model being unable
    to answer."""
    reason: str | None = unavailable_reason(error)
    if reason is None:
        return None
    named: float | None = (
        error.retry_after_s if isinstance(error, ProviderQuotaError | ProviderRateLimitError) else None
    )
    wait: float = cooldown_s if named is None or not named > 0 else min(named, MAX_PAUSE_S)
    return Pause(reason=reason, until=now + wait)


def _answered(
    ref: ModelRef, request: ChatRequest, origin: CallOrigin, seconds: float, first: float | None, reply: ModelReply
) -> ModelCall:
    """A call that got its reply, with the tokens its provider reported and how long its first word took."""
    return ModelCall(
        run_id=origin.run_id,
        purpose=origin.purpose,
        model=ref.label,
        session_key=request.session_key,
        seconds=seconds,
        first_token_seconds=first,
        input_tokens=reply.usage.input_tokens,
        output_tokens=reply.usage.output_tokens,
        cached_tokens=reply.usage.cached_tokens,
        error=None,
    )


def _failed(
    ref: ModelRef, request: ChatRequest, origin: CallOrigin, seconds: float, first: float | None, error: str
) -> ModelCall:
    """A call that ended without a reply: no provider reported tokens for it."""
    return ModelCall(
        run_id=origin.run_id,
        purpose=origin.purpose,
        model=ref.label,
        session_key=request.session_key,
        seconds=seconds,
        first_token_seconds=first,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        error=error,
    )


def _first(moments: Sequence[float]) -> float | None:
    """The first moment noted, or ``None`` when none was."""
    return moments[0] if moments else None


class ModelRouter:
    """Owns the provider connectors and sends each request to the one its model reference names. Every call it sends
    is measured and handed to ``recorder``.

    A model whose call failed with an error that says it cannot answer for a while (:func:`pause_for`) is paused until
    a call to it is answered, the key of its provider changes (:meth:`retire`) or the pause ends. Each model has its
    own pause: a provider's local and cloud models, or its models with limits of their own, are not held back by one
    another, at the cost of one failed call for each model of a provider whose key or quota fails them all. The
    router still sends a paused model every call it is given: the pauses only tell :mod:`dotagent.agent.helpers`
    which helper or standby model to choose. ``login_env`` is the environment of the login shell, which the OpenCode
    server of the ``opencode`` provider starts with (:mod:`dotagent.opencode.server`)."""

    def __init__(self, config: AppConfig, recorder: CallRecorder, login_env: Mapping[str, str]) -> None:
        self._config: AppConfig = config
        # The context sizes chosen for models (item 250), which runs read as they start and a side call as it is made:
        # a model whose own window is larger than its provider is asked for by default gets the window its size needs
        # (item 520, :meth:`window_of`).
        self.context_sizes: ContextSizes = ContextSizes(config.models.context)
        self._recorder: CallRecorder = recorder
        self._login_env: dict[str, str] = dict(login_env)
        self._providers: dict[str, ChatProvider] = {}
        self._pauses: dict[str, Pause] = {}
        # The names served by a connector given through `register`; `retire` leaves them alone.
        self._registered: set[str] = set()
        # The connectors `retire` took out of service, closed with the others at `aclose`.
        self._retired: list[ChatProvider] = []
        # One connector is made, retired or registered at a time for each provider, and not for all of them (item 558):
        # finding a key can take seconds (a Keychain that does not answer), and holds back only the calls that wait for
        # that provider's connector, not those of a provider whose connector exists.
        self._making: dict[str, asyncio.Lock] = {}
        # How often :meth:`aclose` ran, so that a connector made while it ran is closed rather than left behind.
        self._closings: int = 0
        # What reads whether a provider's key is set, told each time the provider is retired (:meth:`on_retire`).
        self._retire_listeners: list[Callable[[str], Awaitable[None]]] = []

    def _maker(self, name: str) -> asyncio.Lock:
        """The lock of the provider ``name`` (a known one: :data:`KNOWN_PROVIDERS` bounds the locks)."""
        lock: asyncio.Lock | None = self._making.get(name)
        if lock is None:
            lock = self._making[name] = asyncio.Lock()
        return lock

    async def _create(self, name: str) -> ChatProvider:
        match name:
            case "ollama":
                key: str | None = await asyncio.to_thread(optional_secret, self._config.providers.ollama.api_key_env)
                return OllamaProvider(self._config.providers.ollama, key)
            case "openai":
                openai = self._config.providers.openai
                api_key: str = await asyncio.to_thread(require_secret, openai.api_key_env, "the openai provider")
                organization: str | None = await asyncio.to_thread(optional_secret, openai.organization_env)
                project: str | None = await asyncio.to_thread(optional_secret, openai.project_env)
                return OpenAIProvider(openai, api_key, organization, project)
            case "opencode_go":
                go = self._config.providers.opencode_go
                go_key: str = await asyncio.to_thread(require_secret, go.api_key_env, "the opencode_go provider")
                return OpenCodeGoProvider(go, go_key)
            case "anthropic":
                claude = self._config.providers.anthropic
                claude_key: str = await asyncio.to_thread(require_secret, claude.api_key_env, "the anthropic provider")
                return AnthropicProvider(claude, claude_key)
            case "openrouter":
                many = self._config.providers.openrouter
                many_key: str = await asyncio.to_thread(require_secret, many.api_key_env, "the openrouter provider")
                return OpenRouterProvider(many, many_key)
            case "opencode":
                return OpenCodeProvider(OpenCodeServer(self._config.providers.opencode, self._login_env), ToolBridge())
            case _:
                raise ConfigError("unknown provider", {"provider": name})

    async def provider(self, name: str) -> ChatProvider:
        """The connector of the provider ``name``: the one in service, which every call takes without waiting for
        another provider's, or a new one, made once even when several calls ask for it at the same time."""
        existing: ChatProvider | None = self._providers.get(name)
        if existing is not None:
            return existing
        if name not in KNOWN_PROVIDERS:
            raise ConfigError("unknown provider", {"provider": name, "providers": list(KNOWN_PROVIDERS)})
        async with self._maker(name):
            existing = self._providers.get(name)
            if existing is not None:
                return existing
            closings: int = self._closings
            created: ChatProvider = await self._create(name)
            if closings != self._closings:
                # The router closed while the key was being found: nobody would close this connector.
                await created.aclose()
                raise ConfigError("the model router was closed", {"provider": name})
            self._providers[name] = created
            return created

    async def register(self, name: str, provider: ChatProvider) -> None:
        """Serve the provider name ``name`` with ``provider`` instead of a connector made from the configuration (the
        scripted model of the evals and of the tests). It has to come before the name is used for the first time."""
        if name not in KNOWN_PROVIDERS:
            raise ConfigError("unknown provider", {"provider": name, "providers": list(KNOWN_PROVIDERS)})
        async with self._maker(name):
            if name in self._providers:
                raise ConfigError("a connector for this provider exists already", {"provider": name})
            self._providers[name] = provider
            self._registered.add(name)

    async def retire(self, name: str) -> None:
        """Make the next request to the provider ``name`` create a new connector, with the key in use then, and end the
        pauses of its models, which the new key may answer. The old connector is closed at ``aclose``, so a request it
        is serving is not cut. A connector given through ``register`` stays in service."""
        self._pauses = {
            label: pause for label, pause in self._pauses.items() if parse_model_ref(label).provider != name
        }
        async with self._maker(name):
            if name not in self._registered:
                retired: ChatProvider | None = self._providers.pop(name, None)
                if retired is not None:
                    self._retired.append(retired)
        for listener in self._retire_listeners:
            await listener(name)

    def on_retire(self, listener: Callable[[str], Awaitable[None]]) -> None:
        """Have ``listener`` called with a provider's name each time :meth:`retire` retires it, as a key of the
        provider is saved or removed: what reads whether the key is set reads it again (item 558)."""
        self._retire_listeners.append(listener)

    async def complete(self, ref: ModelRef, request: ChatRequest, sink: DeltaSink, origin: CallOrigin) -> ModelReply:
        """Send ``request`` to the provider of ``ref`` and hand the recorder what the call cost: its seconds and tokens,
        or, for a call that failed, the type of its error (:data:`CANCELLED` for one that a stop or the caller's
        timeout cut off; a connector that cannot be made, such as one without its key, fails the call too). The
        seconds include the retries the connector makes and its waits between them. A failure is raised after it is
        recorded; after a reply, a recorder that fails raises its own error, as any failed write of the run does. A
        failure that says the model cannot answer for a while pauses the model, and a reply ends its pause."""
        started: float = time.monotonic()
        # When the first word of the reply came (item 404); a retry's reset is not a word.
        first: list[float] = []

        async def timed(delta: StreamDelta) -> None:
            if not first and delta.kind != "reset":
                first.append(time.monotonic() - started)
            await sink(delta)

        try:
            provider: ChatProvider = await self.provider(ref.provider)
            reply: ModelReply = await provider.complete(request, timed)
        except asyncio.CancelledError:
            await self._record_failure(
                _failed(ref, request, origin, time.monotonic() - started, _first(first), CANCELLED)
            )
            raise
        except Exception as error:
            self._pause(ref, error)
            await self._record_failure(
                _failed(ref, request, origin, time.monotonic() - started, _first(first), type(error).__name__)
            )
            raise
        self._pauses.pop(ref.label, None)
        await self._recorder(_answered(ref, request, origin, time.monotonic() - started, _first(first), reply))
        return reply

    def _pause(self, ref: ModelRef, error: Exception) -> None:
        """Pause the model of ``ref`` after ``error``; the newest error decides how long, as the provider's latest
        word."""
        pause: Pause | None = pause_for(error, self._config.models.helper_cooldown_s, time.time())
        if pause is None:
            return
        self._pauses[ref.label] = pause
        log.warning("model_paused", provider=ref.provider, model=ref.label, reason=pause.reason, until=pause.until)

    def paused(self, now: float) -> dict[str, Pause]:
        """The models (``provider:model`` labels) that cannot answer for a while at ``now`` (seconds since the epoch),
        with why and until when."""
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
                exc_info=error,
            )

    async def model_info(self, ref: ModelRef) -> ModelInfo:
        provider: ChatProvider = await self.provider(ref.provider)
        return await provider.model_info(ref.model)

    async def window_of(self, ref: ModelRef) -> int | None:
        """The window a call of ``ref`` made outside a run's steps asks for (``ChatRequest.window``), as a run that
        started now would: its provider's, or the one the context size chosen for it now needs (item 520). A run's own
        steps ask for the window it planned with as it started, which a later choice does not change. Ollama is the one
        provider that takes a window, so a model of another is asked nothing of the kind, and its info is not read."""
        if ref.provider != "ollama":
            return None
        info: ModelInfo = await self.model_info(ref)
        return grown_window(info.context_length, info.own_context_length, self.context_sizes.of(ref.label))

    async def aclose(self) -> None:
        self._closings += 1
        providers: list[ChatProvider] = [*self._providers.values(), *self._retired]
        self._providers.clear()
        self._retired.clear()
        for provider in providers:
            await provider.aclose()
