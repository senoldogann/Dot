"""The owner's GitHub notifications (item 571), read with the personal access token Dot keeps when the owner adds GitHub
on Customize › Connectors: one request for the unread ones, a conditional one after the first (``If-Modified-Since``
with what GitHub's last answer called ``Last-Modified``; a 304 costs nothing against GitHub's limits), and no more often
than GitHub's ``X-Poll-Interval`` says. GitHub's answer is read here, at the edge, into the models below."""

import datetime as dt
from collections.abc import Mapping

import httpx
from pydantic import Field

from dotagent.connectors.http import path_of, read_answer
from dotagent.connectors.types import ProviderAnswer
from dotagent.core.errors import AccountApiError
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.oauth.accounts import Accounts

GITHUB_NAME: str = "GitHub"
NOTIFICATIONS: str = "https://api.github.com/notifications"
# The name Dot's ``.env`` keeps the personal access token under when it was given on Customize › Connectors:
# ``MCP_<service>_<name>`` for the service ``github`` and its secret ``GITHUB_PAT`` (``kept_secret_names`` of
# ``server/routes_mcp.py``, whose result a test holds this to).
GITHUB_TOKEN_NAME: str = "MCP_GITHUB_GITHUB_PAT"
_PAGE: int = 50
_API_VERSION: str = "2022-11-28"
_WORDS_CHARS: int = 300
_NOT_READABLE: str = (
    "GitHub's notifications need a classic personal access token with the notifications scope: a fine-grained token "
    "cannot read them"
)


class _Subject(ProviderAnswer):
    title: str = ""
    type: str = ""


class _Repository(ProviderAnswer):
    full_name: str = ""


class _Thread(ProviderAnswer):
    id: str
    reason: str = ""
    updated_at: dt.datetime
    subject: _Subject = Field(default_factory=_Subject)
    repository: _Repository = Field(default_factory=_Repository)


class _Threads(ProviderAnswer):
    items: tuple[_Thread, ...] = ()


class Notification(FrozenModel):
    """One unread notification: its thread, why it came (``reason``: ``review_requested``, ``mention``, ``assign``,
    ``ci_activity``…), when it was last updated, what it is about and where."""

    id: str
    reason: str
    updated_at: dt.datetime
    title: str
    kind: str
    repository: str


class Notifications(FrozenModel):
    """What one request found. ``changed`` is false for a 304: nothing is new since ``modified``. ``poll_s`` is the
    least GitHub asks to wait before the next request, ``None`` when it did not say."""

    notifications: tuple[Notification, ...]
    modified: str | None
    poll_s: float | None
    changed: bool


def _refusal(response: httpx.Response) -> AccountApiError:
    """The error of an answer that is neither 200 nor 304, with GitHub's own words (``{"message": …}``) and what the
    owner can do about the statuses that mean the token cannot be used."""
    words: str = ""
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        words = str(body.get("message", ""))
    hint: str = ""
    if response.status_code == httpx.codes.UNAUTHORIZED:
        hint = ": the token is wrong, expired or revoked; add GitHub again on Customize › Connectors"
    elif response.status_code in (httpx.codes.FORBIDDEN, httpx.codes.NOT_FOUND):
        hint = f": {_NOT_READABLE}, and a token needs access to notifications at all"
    return AccountApiError(
        f"{GITHUB_NAME} refused the call ({response.status_code}){hint}"
        + (f"; it said: {truncate_middle(redact_secrets(words), _WORDS_CHARS)}" if words else ""),
        {"path": path_of(NOTIFICATIONS), "status": response.status_code},
    )


def _poll_s(headers: Mapping[str, str]) -> float | None:
    said: str = headers.get("X-Poll-Interval", "").strip()
    return float(said) if said.isdigit() else None


async def read_notifications(accounts: Accounts, token: str, modified: str | None) -> Notifications:
    """The unread notifications of the owner, the newest first, as GitHub gives them (one page of 50); with
    ``modified`` (the last answer's ``Last-Modified``) a request that finds nothing new is answered 304 and returns
    none. An :class:`AccountApiError` says what GitHub answered, or that it could not be reached; the token is never
    in it."""
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    if modified is not None:
        headers["If-Modified-Since"] = modified
    try:
        async with accounts.client() as client:
            response: httpx.Response = await client.get(NOTIFICATIONS, params={"per_page": str(_PAGE)}, headers=headers)
    except httpx.HTTPError as error:
        raise AccountApiError(
            f"{GITHUB_NAME} could not be reached", {"path": path_of(NOTIFICATIONS), "error": type(error).__name__}
        ) from error
    poll_s: float | None = _poll_s(response.headers)
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return Notifications(notifications=(), modified=modified, poll_s=poll_s, changed=False)
    if response.status_code != httpx.codes.OK:
        raise _refusal(response)
    try:
        body: object = response.json()
    except ValueError as error:
        raise AccountApiError(f"{GITHUB_NAME}'s answer was not JSON", {"path": path_of(NOTIFICATIONS)}) from error
    found: _Threads = read_answer(_Threads, {"items": body}, GITHUB_NAME, NOTIFICATIONS)
    return Notifications(
        notifications=tuple(
            Notification(
                id=thread.id,
                reason=thread.reason,
                updated_at=thread.updated_at,
                title=thread.subject.title,
                kind=thread.subject.type,
                repository=thread.repository.full_name,
            )
            for thread in found.items
        ),
        modified=response.headers.get("Last-Modified", modified),
        poll_s=poll_s,
        changed=True,
    )
