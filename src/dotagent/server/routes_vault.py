"""Routes of the owner's 1Password (item 493), Customize › Connectors › 1Password: where it stands (``op`` installed,
the vaults the service account reads, with how many logins each holds); Connect, which makes the vault "Shared with Dot"
and a service account that only reads it, with the owner's ``op`` through the 1Password app; a service account token
pasted instead; and Disconnect. Connect and a pasted token are taken on this Mac alone: the 1Password app asks the
owner there. No token and no value of the vault is logged or answered."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr

from dotagent.core.errors import InvalidValueError, SignInError
from dotagent.core.logs import get_logger
from dotagent.core.paths import env_file_path
from dotagent.core.types import FrozenModel
from dotagent.secrets import remove_env_value, write_env_value
from dotagent.server.context import ServerContext
from dotagent.server.routes_accounts import on_this_mac
from dotagent.vault.onepassword import TOKEN_NAME, TOKEN_PATTERN, VaultError, VaultView, find_app, kept_token

log = get_logger(__name__)


class VaultState(FrozenModel):
    """Where 1Password stands for Dot: ``cli`` is the ``op`` Dot runs (``None`` while it is not installed),
    ``connected`` whether Dot holds a service account's token, ``vaults`` what that token reads, and ``error`` why they
    could not be read (``None`` when they were, or when there is no token)."""

    cli: str | None
    # Whether 1Password's own app is on this Mac, which Connect goes through (item 791).
    app: bool = False
    connected: bool
    vaults: tuple[VaultView, ...]
    error: str | None


class VaultToken(FrozenModel):
    """A service account token the owner made at 1password.com, pasted on the page."""

    token: SecretStr = Field(min_length=24, max_length=4096)


def vault_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    async def state() -> VaultState:
        cli = ctx.runtime.vault.cli()
        app: bool = await asyncio.to_thread(find_app) is not None
        token: str | None = await asyncio.to_thread(kept_token)
        if token is None:
            return VaultState(cli=None if cli is None else str(cli), app=app, connected=False, vaults=(), error=None)
        try:
            vaults: tuple[VaultView, ...] = await ctx.runtime.vault.vaults(token)
        except VaultError as error:
            return VaultState(
                cli=None if cli is None else str(cli), app=app, connected=True, vaults=(), error=error.message
            )
        return VaultState(cli=None if cli is None else str(cli), app=app, connected=True, vaults=vaults, error=None)

    async def keep(token: str) -> VaultState:
        """Keep ``token`` once it reads a vault: one that reads none is no use, and one ``op`` refuses is not kept."""
        vaults: tuple[VaultView, ...] = await ctx.runtime.vault.vaults(token)
        if not vaults:
            raise InvalidValueError(
                "the service account reads no vault: share a vault with it in 1Password, then connect again", {}
            )
        await asyncio.to_thread(write_env_value, env_file_path(), TOKEN_NAME, token)
        ctx.runtime.vault.forget()
        log.info("vault_token_kept", vaults=len(vaults))
        return await state()

    def on_the_mac(request: Request) -> None:
        if not on_this_mac(request):
            raise SignInError(
                "connect 1Password on the Mac itself: the 1Password app asks you there, and a token is pasted there",
                {},
            )

    @router.get("/vault")
    async def vault_state() -> VaultState:
        return await state()

    @router.post("/vault/connect")
    async def connect(request: Request) -> VaultState:
        """Make the vault "Shared with Dot" and a service account that only reads it, through the owner's ``op`` and
        the 1Password app, which asks the owner first; then keep its token."""
        on_the_mac(request)
        return await keep(await ctx.runtime.vault.connect())

    @router.post("/vault/token")
    async def paste(request: Request, body: VaultToken) -> VaultState:
        """Keep a service account token the owner made, once it reads a vault."""
        on_the_mac(request)
        token: str = body.token.get_secret_value().strip()
        if TOKEN_PATTERN.fullmatch(token) is None:
            raise InvalidValueError("that is not a 1Password service account token, which starts with ops_", {})
        return await keep(token)

    @router.delete("/vault")
    async def disconnect() -> VaultState:
        """Forget the service account's token: runs fill no login from 1Password any more. The service account and the
        vault stay in 1Password, whose own page removes them."""
        await asyncio.to_thread(remove_env_value, env_file_path(), TOKEN_NAME)
        ctx.runtime.vault.forget()
        log.info("vault_token_forgotten")
        return await state()

    return router
