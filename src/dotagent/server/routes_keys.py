"""Routes for the API keys of the model providers: where the key each one uses comes from, and saving or removing the
one in Dot's ``.env`` file. A key goes in and never comes back out: a view carries at most its last four characters."""

import asyncio

from fastapi import APIRouter

from dotagent.config import AppConfig
from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import env_file_path
from dotagent.doctor import CheckResult, check_provider_key
from dotagent.secrets import SecretState, remove_env_value, secret_state, write_env_value
from dotagent.server.context import ServerContext
from dotagent.server.views import KeyRequest, KeysView, KeyView

log = get_logger(__name__)


def providers_by_key(config: AppConfig) -> dict[str, str]:
    """The provider of each key, by the name of its variable, in the order the Settings page lists them: the providers
    whose models need a key first, Ollama (whose models the local server runs without one) last."""
    providers = config.providers
    return {
        providers.openai.api_key_env: "openai",
        providers.anthropic.api_key_env: "anthropic",
        providers.opencode_go.api_key_env: "opencode_go",
        providers.ollama.api_key_env: "ollama",
    }


def _view(provider: str, name: str) -> KeyView:
    state: SecretState = secret_state(name)
    return KeyView(provider=provider, name=name, source=state.source, saved=state.saved, tail=state.tail)


def keys_router(ctx: ServerContext) -> APIRouter:
    """A saved or removed key reaches the running daemon at once: the provider's connector is retired, and the next
    request makes a new one with the key in use then."""
    router: APIRouter = APIRouter(prefix="/api")
    config: AppConfig = ctx.runtime.config
    providers: dict[str, str] = providers_by_key(config)
    # One change of the file at a time: each reads it, changes one entry and writes it back.
    writing: asyncio.Lock = asyncio.Lock()

    def provider_of(name: str) -> str:
        provider: str | None = providers.get(name)
        if provider is None:
            raise NotFoundError("no provider takes a key of this name", {"name": name, "names": list(providers)})
        return provider

    @router.get("/keys")
    async def keys() -> KeysView:
        views: list[KeyView] = [await asyncio.to_thread(_view, provider, name) for name, provider in providers.items()]
        return KeysView(env_file=str(env_file_path()), keys=views)

    @router.post("/keys/{name}")
    async def save_key(name: str, request: KeyRequest) -> KeyView:
        provider: str = provider_of(name)
        async with writing:
            await asyncio.to_thread(write_env_value, env_file_path(), name, request.value)
        await ctx.runtime.router.retire(provider)
        log.info("key_saved", provider=provider, name=name)
        return await asyncio.to_thread(_view, provider, name)

    @router.delete("/keys/{name}")
    async def remove_key(name: str) -> KeyView:
        """Remove the key saved in Dot. A key from the environment or the Keychain is not Dot's to remove: 404."""
        provider: str = provider_of(name)
        async with writing:
            removed: bool = await asyncio.to_thread(remove_env_value, env_file_path(), name)
        if not removed:
            raise NotFoundError(
                "Dot's .env file has no entry for this key", {"name": name, "env_file": str(env_file_path())}
            )
        await ctx.runtime.router.retire(provider)
        log.info("key_removed", provider=provider, name=name)
        return await asyncio.to_thread(_view, provider, name)

    @router.post("/keys/{name}/check")
    async def check_key(name: str) -> CheckResult:
        """Ask the provider whether it takes the key in use: one call that costs nothing, or for Ollama's key one
        search of its hosted web search."""
        return await check_provider_key(config, provider_of(name))

    return router
