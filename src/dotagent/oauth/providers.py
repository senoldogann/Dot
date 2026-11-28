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
    """One sign-in: ``scopes`` what a sign-in asks for, ``draft_scopes`` what is added when the owner allows drafts
    and sending for an account (item 2047); ``extra`` are the parameters of its authorization request besides the
    common ones;
    ``profile_url`` says who signed in, read with the new access token; ``revoke_url`` is where a token is given back
    to end its grant at the provider (``None``: it has none for a client like Dot's, item 558); ``permissions`` is the
    page of the owner's account where the apps that may use it are listed and taken away (item 1312)."""

    id: ProviderId
    name: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    draft_scopes: tuple[str, ...]
    client_id_name: str
    client_secret_name: str | None
    loopback: str
    extra: tuple[tuple[str, str], ...]
    profile_url: str
    revoke_url: str | None
    permissions: str


GOOGLE: Provider = Provider(
    id="google",
    name="Google",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    # What the account tools do is read: no more is asked at a sign-in (item 558), so that a token read out of the
    # Keychain cannot send, delete or change either.
    scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ),
    # Asked only when the owner allows drafts and sending for an account (item 2047). Gmail has no scope for drafts
    # alone: ``gmail.compose`` makes, changes and sends them (and no other mail is changed or deleted with it).
    draft_scopes=("https://www.googleapis.com/auth/gmail.compose",),
    client_id_name="DOT_GOOGLE_CLIENT_ID",
    client_secret_name="DOT_GOOGLE_CLIENT_SECRET",
    loopback="127.0.0.1",
    # A refresh token every time the owner says yes, so that a second sign-in after a lost one still gets one.
    extra=(("access_type", "offline"), ("prompt", "consent")),
    profile_url="https://openidconnect.googleapis.com/v1/userinfo",
    revoke_url="https://oauth2.googleapis.com/revoke",
    permissions="myaccount.google.com/permissions",
)
MICROSOFT: Provider = Provider(
    id="microsoft",
    name="Microsoft",
    authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scopes=("openid", "email", "offline_access", "User.Read", "Mail.Read", "Calendars.Read"),
    # Asked only when the owner allows drafts and sending for an account (item 2047): Graph makes a draft only with
    # Mail.ReadWrite, which may change and delete any mail too, and sends one with Mail.Send.
    draft_scopes=("Mail.ReadWrite", "Mail.Send"),
    client_id_name="DOT_MS_CLIENT_ID",
    client_secret_name=None,
    loopback="localhost",
    extra=(("prompt", "select_account"),),
    profile_url="https://graph.microsoft.com/v1.0/me",
    revoke_url=None,
    permissions="account.live.com/consent/Manage",
)
PROVIDERS: dict[ProviderId, Provider] = {GOOGLE.id: GOOGLE, MICROSOFT.id: MICROSOFT}
