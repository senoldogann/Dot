"""Routes of the official sign-ins of Google and Microsoft (item 422): the providers, whether the owner's client is
set and the accounts signed in; a sign-in started for a browser on this Mac; an account signed out. The browser comes
back to ``/oauth/callback`` (:mod:`dotagent.server.routes_mcp`), which hands it to the accounts first. No token, code
or address is logged."""

import time

from fastapi import APIRouter, Request

from dotagent.core.errors import SignInError
from dotagent.oauth.accounts import ProviderView
from dotagent.oauth.providers import ProviderId
from dotagent.server.context import ServerContext
from dotagent.server.views import SignInLink

# The names of this Mac's own loopback address, which a provider sends the browser back to.
_LOOPBACK: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})


def on_this_mac(host: str | None) -> bool:
    """Whether a request's ``Host`` names this Mac's loopback address (with or without the daemon's port)."""
    if host is None:
        return False
    name: str = host.split("]", 1)[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    return name.lower() in _LOOPBACK


def accounts_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/accounts")
    async def accounts() -> list[ProviderView]:
        return list(await ctx.runtime.accounts.providers())

    @router.post("/accounts/{provider}/start")
    async def start(provider: ProviderId, request: Request) -> SignInLink:
        """The address of the provider's page that asks for a yes, for a browser on this Mac: the provider sends it
        back to this Mac's own address, which a phone does not reach."""
        if not on_this_mac(request.headers.get("host")):
            raise SignInError(
                "sign in on the Mac itself: the sign-in comes back to the Mac's own address, which a phone does not "
                "reach",
                {"provider": provider},
            )
        return SignInLink(link=await ctx.runtime.accounts.start(provider, ctx.port, time.time()))

    @router.delete("/accounts/{provider}/{address}")
    async def sign_out(provider: ProviderId, address: str) -> list[ProviderView]:
        await ctx.runtime.accounts.forget(provider, address)
        return list(await ctx.runtime.accounts.providers())

    return router
