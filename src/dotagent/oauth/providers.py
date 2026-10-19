"""Where Google and Microsoft send the owner to say yes, where Dot trades the code for tokens, the access it asks for,
and the names of the client the owner registered with each, which Dot reads as it reads its keys (environment, then
``<home>/.env``, then the Keychain's ``dot-agent`` service): ``DOT_GOOGLE_CLIENT_ID`` and ``DOT_GOOGLE_CLIENT_SECRET``
(a Desktop app client of Google Auth Platform), ``DOT_MS_CLIENT_ID`` (an app registration of Microsoft Entra, a public
client). Each sends the browser back to the daemon's ``/oauth/callback`` on the loopback name it asks of a desktop app:
Google's ``127.0.0.1``, Microsoft's ``localhost`` (whose port it does not compare)."""

from typing import Literal

from dotagent.core.types import FrozenModel

type ProviderId = Literal["google", "microsoft"]


class Provider(FrozenModel):
    """One sign-in: ``extra`` are the parameters of its authorization request besides the common ones;
    ``profile_url`` says who signed in, read with the new access token."""

    id: ProviderId
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_name: str
    client_secret_name: str | None
    loopback: str
    extra: tuple[tuple[str, str], ...]
    profile_url: str


GOOGLE: Provider = Provider(
    id="google",
    name="Google",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
    ),
    client_id_name="DOT_GOOGLE_CLIENT_ID",
    client_secret_name="DOT_GOOGLE_CLIENT_SECRET",
    loopback="127.0.0.1",
    # A refresh token every time the owner says yes, so that a second sign-in after a lost one still gets one.
    extra=(("access_type", "offline"), ("prompt", "consent")),
    profile_url="https://openidconnect.googleapis.com/v1/userinfo",
)
MICROSOFT: Provider = Provider(
    id="microsoft",
    name="Microsoft",
    authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scopes=("openid", "email", "offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"),
    client_id_name="DOT_MS_CLIENT_ID",
    client_secret_name=None,
    loopback="localhost",
    extra=(("prompt", "select_account"),),
    profile_url="https://graph.microsoft.com/v1.0/me",
)
PROVIDERS: dict[ProviderId, Provider] = {GOOGLE.id: GOOGLE, MICROSOFT.id: MICROSOFT}
