"""The tokens of an account and the account itself, read from what a token endpoint and a profile answered."""

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.core.errors import SignInError
from dotagent.core.types import FrozenModel
from dotagent.oauth.providers import ProviderId

_EXPIRY_S: float = 3600.0


class Tokens(FrozenModel):
    """``expires_at`` in seconds since the epoch; ``refresh_token`` None when the provider gave none."""

    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str


class Account(FrozenModel):
    """An account signed in: its provider, its address, and when it was signed in to."""

    provider: ProviderId
    address: str
    connected_at: float


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    access_token: str
    refresh_token: str | None = None
    expires_in: float | None = None
    scope: str | None = None


class _Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str | None = None
    mail: str | None = None
    userPrincipalName: str | None = None


def tokens_from(answer: object, now: float, kept_refresh: str | None) -> Tokens:
    """The tokens a token endpoint answered at ``now``; a refresh that brings no new refresh token keeps
    ``kept_refresh`` (Google gives one only at the first yes)."""
    try:
        parsed: _Answer = _Answer.model_validate(answer)
    except ValidationError as error:
        raise SignInError("the token endpoint's answer could not be read", {"errors": error.error_count()}) from error
    return Tokens(
        access_token=parsed.access_token,
        refresh_token=parsed.refresh_token or kept_refresh,
        expires_at=now + (parsed.expires_in if parsed.expires_in is not None else _EXPIRY_S),
        scope=parsed.scope or "",
    )


def address_from(profile: object) -> str:
    """The address a profile names: Google's ``email``, Microsoft's ``mail`` or else ``userPrincipalName``."""
    try:
        parsed: _Profile = _Profile.model_validate(profile)
    except ValidationError as error:
        raise SignInError("who signed in could not be read", {"errors": error.error_count()}) from error
    address: str | None = parsed.email or parsed.mail or parsed.userPrincipalName
    if address is None or address.strip() == "":
        raise SignInError("the profile names no address", {})
    return address.strip()
