"""Routes for the API keys of the model providers and of the typed judge's service: where the key each one uses comes
from, and saving or removing the one in Dot's ``.env`` file. A key goes in and never comes back out: a view carries at
most its last four characters.

A paired phone reaches them too (item 2054; ``roadmap/2054.md`` says what must hold): within limits of its own
(:data:`PHONE_CHANGES`, :data:`PHONE_CHECK_EVERY_S`), and every key it saves or removes is told to the owner at once, so
that a phone that was taken cannot put a key of someone else's account in place of the owner's unseen."""

import asyncio
import time

from fastapi import APIRouter, Request

from dotagent.config import JUDGE_SERVICE, AppConfig, providers_by_key
from dotagent.core.errors import NotFoundError, TooManyRequests
from dotagent.core.logs import get_logger
from dotagent.core.paths import env_file_path
from dotagent.doctor import CheckResult, check_provider_key
from dotagent.notify.owner import tell_owner
from dotagent.secrets import SecretState, remove_env_value, secret_state, write_env_value
from dotagent.server.context import ServerContext
from dotagent.server.guard import PHONE_STATE
from dotagent.server.phones import PairedPhones, Phone
from dotagent.server.views import KeyCheck, KeyRequest, KeysView, KeyView

log = get_logger(__name__)

# How many keys a phone may save or remove in :data:`PHONE_CHANGES_WINDOW_S`, and how seldom it may ask a provider's
# check of one key: Ollama's check is one hosted search and TypeSafe's one small judgment. The installation's token has
# no such limit.
PHONE_CHANGES: int = 5
PHONE_CHANGES_WINDOW_S: float = 600.0
PHONE_CHECK_EVERY_S: float = 10.0
# The route of the web's page of the keys, which the tell of a phone's change opens.
KEYS_ROUTE: str = "#/settings/keys"
_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "opencode_go": "OpenCode Go",
    "openrouter": "OpenRouter",
    "google": "Google AI Studio",
    "ollama": "Ollama",
    JUDGE_SERVICE: "TypeSafe",
}


def _view(provider: str, name: str, check: KeyCheck | None) -> KeyView:
    state: SecretState = secret_state(name)
    return KeyView(provider=provider, name=name, source=state.source, saved=state.saved, tail=state.tail, check=check)


def wait_left(times: tuple[float, ...], now: float, most: int, window_s: float) -> float:
    """The seconds until one more request fits when ``times`` were made and at most ``most`` fit in ``window_s``: 0.0
    when it fits now."""
    recent: list[float] = [at for at in times if now - at < window_s]
    if len(recent) < most:
        return 0.0
    return window_s - (now - min(recent))


def recent_times(times: tuple[float, ...], now: float, window_s: float) -> tuple[float, ...]:
    """``times`` still inside ``window_s`` before ``now``, with ``now`` added: what a limit remembers of a request it
    let through."""
    return (*(at for at in times if now - at < window_s), now)


def phone_label(phones: list[Phone], phone_id: str) -> str:
    """The name of the listed phone ``phone_id`` as the owner's pairing gave it, else its id."""
    for phone in phones:
        if phone.id == phone_id:
            return phone.name
    return phone_id


def _phone_of(http: Request) -> str | None:
    """The id of the phone whose key made ``http``, ``None`` for the installation's token."""
    phone: object = getattr(http.state, PHONE_STATE, None)
    return phone if isinstance(phone, str) else None


def keys_router(ctx: ServerContext, phones: PairedPhones) -> APIRouter:
    """A saved or removed key reaches the running daemon at once: the provider's connector (or the judge's) is
    retired, and the next request makes a new one with the key in use then."""
    router: APIRouter = APIRouter(prefix="/api")
    config: AppConfig = ctx.runtime.config
    providers: dict[str, str] = providers_by_key(config)
    # One change of the file at a time: each reads it, changes one entry and writes it back.
    writing: asyncio.Lock = asyncio.Lock()
    # The last check of each key since the daemon started, by the variable's name; a change of the key forgets it.
    checks: dict[str, KeyCheck] = {}
    # When each phone changed a key, and when a phone last asked each key's check.
    changes: dict[str, tuple[float, ...]] = {}
    asked: dict[str, tuple[float, ...]] = {}

    async def retire(provider: str) -> None:
        if provider == JUDGE_SERVICE:
            await ctx.runtime.judge.retire()
        else:
            await ctx.runtime.router.retire(provider)

    def provider_of(name: str) -> str:
        provider: str | None = providers.get(name)
        if provider is None:
            raise NotFoundError("no provider takes a key of this name", {"name": name, "names": list(providers)})
        return provider

    def admit(kept: dict[str, tuple[float, ...]], who: str, most: int, window_s: float, what: str) -> None:
        """Refuse with 429 a request past ``most`` in ``window_s`` for ``who``, else remember it."""
        now: float = time.time()
        wait: float = wait_left(kept.get(who, ()), now, most, window_s)
        if wait > 0.0:
            raise TooManyRequests(
                f"a phone may {what}: try again in a moment", {"retry_after_s": round(wait, 1), "most": most}
            )
        kept[who] = recent_times(kept.get(who, ()), now, window_s)

    async def tell_phone_change(phone: str, provider: str, done: str) -> None:
        """Tell the owner that the phone ``phone`` ``done`` (saved, removed) the key of ``provider``, and nothing of
        the key itself."""
        label: str = _LABELS.get(provider, provider)
        name: str = phone_label(phones.listed(), phone)
        await tell_owner(
            ctx.runtime.bus,
            f"The {label} key was {done} from a phone",
            f"{name} {done} the {label} key. If it was not you, remove that phone on your Mac and put your own key "
            "back.",
            KEYS_ROUTE,
            "system",
            "",
        )

    @router.get("/keys")
    async def keys() -> KeysView:
        views: list[KeyView] = [
            await asyncio.to_thread(_view, provider, name, checks.get(name)) for name, provider in providers.items()
        ]
        return KeysView(env_file=str(env_file_path()), keys=views)

    @router.post("/keys/{name}")
    async def save_key(name: str, request: KeyRequest, http: Request) -> KeyView:
        provider: str = provider_of(name)
        phone: str | None = _phone_of(http)
        if phone is not None:
            admit(changes, phone, PHONE_CHANGES, PHONE_CHANGES_WINDOW_S, f"change {PHONE_CHANGES} keys in 10 minutes")
        async with writing:
            await asyncio.to_thread(write_env_value, env_file_path(), name, request.value)
            checks.pop(name, None)
        await retire(provider)
        log.info("key_saved", provider=provider, name=name, phone=phone)
        if phone is not None:
            await tell_phone_change(phone, provider, "saved")
        return await asyncio.to_thread(_view, provider, name, None)

    @router.delete("/keys/{name}")
    async def remove_key(name: str, http: Request) -> KeyView:
        """Remove the key saved in Dot. A key from the environment or the Keychain is not Dot's to remove: 404."""
        provider: str = provider_of(name)
        phone: str | None = _phone_of(http)
        if phone is not None:
            admit(changes, phone, PHONE_CHANGES, PHONE_CHANGES_WINDOW_S, f"change {PHONE_CHANGES} keys in 10 minutes")
        async with writing:
            removed: bool = await asyncio.to_thread(remove_env_value, env_file_path(), name)
            if removed:
                checks.pop(name, None)
        if not removed:
            raise NotFoundError(
                "Dot's .env file has no entry for this key", {"name": name, "env_file": str(env_file_path())}
            )
        await retire(provider)
        log.info("key_removed", provider=provider, name=name, phone=phone)
        if phone is not None:
            await tell_phone_change(phone, provider, "removed")
        return await asyncio.to_thread(_view, provider, name, checks.get(name))

    @router.post("/keys/{name}/check")
    async def check_key(name: str, http: Request) -> CheckResult:
        """Ask the provider whether it takes the key in use: one call that costs nothing, or for Ollama's key one
        search of its hosted web search. The answer is kept as the key's last check."""
        provider: str = provider_of(name)
        if _phone_of(http) is not None:
            admit(asked, name, 1, PHONE_CHECK_EVERY_S, "ask a key's check once in 10 seconds")
        result: CheckResult = await check_provider_key(config, provider)
        checks[name] = KeyCheck(ok=result.ok, detail=result.detail, at=time.time())
        return result

    return router
