"""Model routing: ``provider:model`` references resolved to connectors that are created on first use."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress

from dotagent.agent.sizes import ContextSizes
from dotagent.claude_code.models import fast_model
from dotagent.config import AppConfig
from dotagent.core.errors import (
    ConfigError,
    DotError,
    MissingSecretError,
    ProviderAccessError,
    ProviderAuthError,
    ProviderCreditError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderWindowError,
)
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.llm import reach
from dotagent.llm.anthropic import FAST_MODE_MODELS, AnthropicProvider
from dotagent.llm.antigravity import AntigravityProvider
from dotagent.llm.calls import CANCELLED, CallOrigin, CallRecorder, ModelCall
from dotagent.llm.claude_code import CLAUDE_CODE_PROVIDER, ClaudeCodeProvider
from dotagent.llm.google import GoogleProvider
from dotagent.llm.google_image import GeneratedImage, ImageRequest
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

# How long a connector that a key change took out of service waits after its last call that the router knows of ended
# before it is closed (item 1314): the router counts the calls it makes itself (``complete``, ``model_info``), and a
# caller that took the connector from ``provider`` before the key changed, and keeps it for a listing, has this long.
_RETIRED_GRACE_S: float = 30.0


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
    """Whether the model ``ref`` names has fast mode (item 223): some of Anthropic's, as its page names them, and Opus
    on the owner's Claude plan that Claude Code lists with it (items 1014 and 1367)."""
    if ref.provider == CLAUDE_CODE_PROVIDER:
        return fast_model(ref.model)
    return ref.provider == "anthropic" and ref.model in FAST_MODE_MODELS


class Pause(FrozenModel):
    """A model that cannot answer for a while: why, and until when (seconds since the epoch)."""

    reason: str
    until: float


# The longest pause: a provider that names a longer wait (or an endless one) is asked again after a day.
MAX_PAUSE_S: float = 86_400.0

# The reasons of a pause that only the owner can end, by paying or by changing the plan: the credits or the balance of
# an account spent, a quota used up, a model the plan does not pay for (item 1207). A rate limit, a plan's usage window
# that is spent ("usage window spent") and a refused key are not among them: the first two pass on their own, the
# third is told where the key is saved.
MONEY_REASONS: frozenset[str] = frozenset({"out of credits", "quota exhausted", "not in the plan"})


def unavailable_reason(error: Exception) -> str | None:
    """Why the model whose call raised ``error`` cannot answer for a while: the quota or the rate of its provider is
    spent (OpenRouter's 402 for credits held by its other requests still running among the rates), the credits of
    its account are (OpenRouter's other 402 that speaks of credits), the plan does not include the model (any other
    HTTP 402), its key was refused (HTTP 401), the access to it was refused (HTTP 403), or it has no key. ``None`` for
    an error that says nothing about that, such as a transport or protocol error. The connector has retried what can
    be retried before such an error reaches its caller."""
    if isinstance(error, ProviderCreditError):
        return "out of credits"
    if isinstance(error, ProviderWindowError):
        return "usage window spent"
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
        cache_write_tokens=reply.usage.cache_write_tokens,
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


def _image_call(
    ref: ModelRef, origin: CallOrigin, seconds: float, picture: GeneratedImage | None, error: str | None
) -> ModelCall:
    """A picture call as it was measured: the tokens Google reported for it, none for one that gave no picture."""
    return ModelCall(
        run_id=origin.run_id,
        purpose=origin.purpose,
        model=ref.label,
        session_key="image",
        seconds=seconds,
        first_token_seconds=None,
        input_tokens=0 if picture is None else picture.usage.input_tokens,
        output_tokens=0 if picture is None else picture.usage.output_tokens,
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
        # The connectors `retire` took out of service: each is closed once the calls the router sends it have ended and
        # a short grace is over (:meth:`_close_when_idle`), and the rest with the others at `aclose`.
        self._retired: list[ChatProvider] = []
        # How many of the router's own calls each connector (by identity) is serving, and the tasks that close a retired
        # connector once it is idle.
        self._leases: dict[int, int] = {}
        self._closers: set[asyncio.Task[None]] = set()
        # Set by :meth:`aclose`, which closes every connector itself: a retired connector waiting out its grace stops
        # waiting.
        self._swept: asyncio.Event = asyncio.Event()
        # One connector is made, retired or registered at a time for each provider, and not for all of them (item 558):
        # finding a key can take seconds (a Keychain that does not answer), and holds back only the calls that wait for
        # that provider's connector, not those of a provider whose connector exists.
        self._making: dict[str, asyncio.Lock] = {}
        # How often :meth:`aclose` ran, so that a connector made while it ran is closed rather than left behind.
        self._closings: int = 0
        # What reads whether a provider's key is set, told each time the provider is retired (:meth:`on_retire`).
        self._retire_listeners: list[Callable[[str], Awaitable[None]]] = []
        # What is told each time a model begins a pause for one of :data:`MONEY_REASONS` (:meth:`on_money_pause`).
        self._money_listeners: list[Callable[[ModelRef, Pause, str], Awaitable[None]]] = []

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
            case "google":
                gemini = self._config.providers.google
                gemini_key: str = await asyncio.to_thread(require_secret, gemini.api_key_env, "the google provider")
                return GoogleProvider(gemini, gemini_key)
            case "antigravity":
                return AntigravityProvider(self._config.providers.antigravity)
            case "opencode":
                return OpenCodeProvider(OpenCodeServer(self._config.providers.opencode, self._login_env), ToolBridge())
            case "claude_code":
                return ClaudeCodeProvider(self._config.providers.claude_code, self._login_env, ToolBridge())
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

    @asynccontextmanager
    async def _leased(self, name: str) -> AsyncIterator[ChatProvider]:
        """The connector of ``name`` for one call of the router's own, counted while the call lasts: a connector retired
        meanwhile is closed after its last call ends (item 1314), not under it."""
        provider: ChatProvider = await self.provider(name)
        # Counted before anything can wait: a retire cannot come between the connector and its count.
        self._leases[id(provider)] = self._leases.get(id(provider), 0) + 1
        try:
            yield provider
        finally:
            left: int = self._leases[id(provider)] - 1
            if left > 0:
                self._leases[id(provider)] = left
            else:
                del self._leases[id(provider)]
                if provider in self._retired:
                    self._close_when_idle(provider)

    def _close_when_idle(self, provider: ChatProvider) -> None:
        """Close the retired ``provider`` once :data:`_RETIRED_GRACE_S` have passed with no call of the router on it."""
        task: asyncio.Task[None] = asyncio.create_task(
            self._close_after_grace(provider, self._swept), name="retired-connector"
        )
        self._closers.add(task)
        task.add_done_callback(self._closers.discard)

    async def _close_after_grace(self, provider: ChatProvider, swept: asyncio.Event) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(swept.wait(), _RETIRED_GRACE_S)
        # Whoever takes it off the list closes it, so that it is closed once: this, or :meth:`aclose`.
        if self._leases.get(id(provider), 0) == 0 and provider in self._retired:
            self._retired.remove(provider)
            await provider.aclose()

    async def retire(self, name: str) -> None:
        """Make the next request to the provider ``name`` create a new connector, with the key in use then, and end the
        pauses of its models, which the new key may answer. The old connector is closed once the calls it is serving
        have ended (and a short grace, for a caller that holds it outside the router's count), so a request it is
        serving is not cut; ``aclose`` closes what is left. A connector given through ``register`` stays in service."""
        self._pauses = {
            label: pause for label, pause in self._pauses.items() if parse_model_ref(label).provider != name
        }
        async with self._maker(name):
            if name not in self._registered:
                retired: ChatProvider | None = self._providers.pop(name, None)
                if retired is not None:
                    self._retired.append(retired)
                    if self._leases.get(id(retired), 0) == 0:
                        self._close_when_idle(retired)
        for listener in self._retire_listeners:
            await listener(name)

    def on_retire(self, listener: Callable[[str], Awaitable[None]]) -> None:
        """Have ``listener`` called with a provider's name each time :meth:`retire` retires it, as a key of the
        provider is saved or removed: what reads whether the key is set reads it again (item 558)."""
        self._retire_listeners.append(listener)

    def on_money_pause(self, listener: Callable[[ModelRef, Pause, str], Awaitable[None]]) -> None:
        """Have ``listener`` called with the model, its pause and the provider's own words each time a model that was
        answering begins a pause for one of :data:`MONEY_REASONS` (item 1207): a balance spent stops every run and
        schedule on the model, and nothing else tells the owner."""
        self._money_listeners.append(listener)

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
            async with self._leased(ref.provider) as provider:
                reply: ModelReply = await provider.complete(request, timed)
        except asyncio.CancelledError:
            await self._record_failure(
                _failed(ref, request, origin, time.monotonic() - started, _first(first), CANCELLED)
            )
            raise
        except Exception as error:
            begun: Pause | None = self._pause(ref, error)
            await self._record_failure(
                _failed(ref, request, origin, time.monotonic() - started, _first(first), type(error).__name__)
            )
            if begun is not None and begun.reason in MONEY_REASONS:
                await self._tell_money(ref, begun, error.message if isinstance(error, DotError) else str(error))
            raise
        self._pauses.pop(ref.label, None)
        await self._recorder(_answered(ref, request, origin, time.monotonic() - started, _first(first), reply))
        return reply

    async def generate_image(self, request: ImageRequest, origin: CallOrigin) -> GeneratedImage:
        """One picture from Google's picture model on the Google key (``generate_image``'s photo path), measured and
        recorded like a chat call: its seconds, and the tokens Google reported, or the type of the error that ended it.
        Raises :class:`MissingSecretError` when there is no key, and the connector's own error otherwise."""
        started: float = time.monotonic()
        ref: ModelRef = ModelRef(provider="google", model=request.model)
        try:
            async with self._leased(ref.provider) as provider:
                if not isinstance(provider, GoogleProvider):
                    raise ConfigError("the google provider is not Google's connector", {"provider": provider.name})
                picture: GeneratedImage = await provider.generate_image(request)
        except asyncio.CancelledError:
            await self._record_failure(_image_call(ref, origin, time.monotonic() - started, None, CANCELLED))
            raise
        except Exception as error:
            self._pause(ref, error)
            await self._record_failure(_image_call(ref, origin, time.monotonic() - started, None, type(error).__name__))
            raise
        self._pauses.pop(ref.label, None)
        await self._recorder(_image_call(ref, origin, time.monotonic() - started, picture, None))
        return picture

    def _pause(self, ref: ModelRef, error: Exception) -> Pause | None:
        """Pause the model of ``ref`` after ``error``; the newest error decides how long, as the provider's latest
        word. Returns the pause when it begins one or changes why the model is paused (a 402 after a rate limit),
        ``None`` when the error pauses nothing or the model was paused already for the same reason."""
        now: float = time.time()
        pause: Pause | None = pause_for(error, self._config.models.helper_cooldown_s, now)
        if pause is None:
            return None
        before: Pause | None = self._pauses.get(ref.label)
        self._pauses[ref.label] = pause
        log.warning("model_paused", provider=ref.provider, model=ref.label, reason=pause.reason, until=pause.until)
        return pause if before is None or before.until <= now or before.reason != pause.reason else None

    async def _tell_money(self, ref: ModelRef, pause: Pause, words: str) -> None:
        """Tell the listeners of :meth:`on_money_pause`. The caller gets the call's own error: a listener that fails is
        logged instead, as a failed record of the call is."""
        for listener in self._money_listeners:
            try:
                await listener(ref, pause, words)
            except Exception as error:
                log.error("money_pause_not_told", model=ref.label, reason=pause.reason, exc_info=error)

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

    def address(self, ref: ModelRef) -> str | None:
        """The address of the server a call of ``ref`` goes to (item 769): its provider's ``base_url``; ``None`` for
        OpenCode's agent, a server of Dot's own, and for a provider served by a connector given through
        :meth:`register` (the scripted model of the evals and of the tests), whose calls go nowhere it names."""
        if ref.provider in self._registered:
            return None
        providers = self._config.providers
        match ref.provider:
            case "ollama":
                return providers.ollama.base_url
            case "openai":
                return providers.openai.base_url
            case "opencode_go":
                return providers.opencode_go.base_url
            case "anthropic":
                return providers.anthropic.base_url
            case "openrouter":
                return providers.openrouter.base_url
            case "google":
                return providers.google.base_url
            case _:
                return None

    async def reaches(self, ref: ModelRef) -> bool:
        """Whether this Mac can connect to the server of ``ref`` now (:func:`~dotagent.llm.reach.reaches`); yes when it
        has no address to check (:meth:`address`)."""
        address: str | None = self.address(ref)
        return address is None or await reach.reaches(address)

    async def model_info(self, ref: ModelRef) -> ModelInfo:
        async with self._leased(ref.provider) as provider:
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
        """Close every connector, those retired and still waiting out their grace among them."""
        self._closings += 1
        swept: asyncio.Event = self._swept
        self._swept = asyncio.Event()
        swept.set()
        closers: list[asyncio.Task[None]] = list(self._closers)
        providers: list[ChatProvider] = [*self._providers.values(), *self._retired]
        self._providers.clear()
        self._retired.clear()
        for provider in providers:
            await provider.aclose()
        # A retired connector a closer had already taken off the list is being closed by it: wait for that to end.
        for closer in closers:
            await closer
