"""Calls to the APIs of an account signed in (item 424), as the account, with its access token.

A token the provider turns away before its time (401) is refreshed once and the call made again. A call that could not
reach the provider, or that it answered with 429 or a 5xx, is made again after the time the provider asks for: these
calls only read, so a second one changes nothing. Any other answer but 200 is an :class:`AccountApiError` with the
provider's own words for it; nothing here logs or keeps a token."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from dotagent.connectors.types import ProviderAnswer
from dotagent.core.errors import AccountApiError
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.oauth.accounts import Accounts
from dotagent.oauth.providers import ProviderId

_RETRY: RetryPolicy = RetryPolicy(attempts=3, base_delay_s=1.0, max_delay_s=10.0)
# The answers that say "not now": the call may be made again.
_BUSY: frozenset[int] = frozenset({429, 500, 502, 503, 504})
# The longest wait a Retry-After is followed for.
_LONGEST_WAIT_S: float = 30.0


class _Busy(Exception):
    """The provider answered with one of :data:`_BUSY`."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"the provider answered {response.status_code}")
        self.response: httpx.Response = response


def _again(error: Exception) -> bool:
    return isinstance(error, _Busy | httpx.TransportError)


def wait_asked(response: httpx.Response) -> float | None:
    """The seconds a ``Retry-After`` header asks for, up to :data:`_LONGEST_WAIT_S`; None for none or a date."""
    said: str = response.headers.get("Retry-After", "").strip()
    if not said.isdigit():
        return None
    return min(float(said), _LONGEST_WAIT_S)


def _wait_of(error: Exception) -> float | None:
    return wait_asked(error.response) if isinstance(error, _Busy) else None


def path_of(url: str) -> str:
    """The path of ``url``, for an error: its query, the owner's words, is left out."""
    return urlsplit(url).path


def refused(service: str, url: str, response: httpx.Response) -> AccountApiError:
    """The error of an answer that is not 200, with the provider's own words: Google and Microsoft both say them as
    ``{"error": {"code": …, "message": …}}``."""
    words: str = ""
    code: str = ""
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error: object = body.get("error")
        if isinstance(error, dict):
            words = str(error.get("message", ""))
            code = str(error.get("status", error.get("code", "")))
    return AccountApiError(
        f"{service} refused the call ({response.status_code}{f', {code}' if code else ''})"
        + (f": {truncate_middle(words, 300)}" if words else ""),
        {
            "path": path_of(url),
            "status": response.status_code,
            "body": redact_secrets(truncate_middle(response.text, 300)),
        },
    )


def read_answer[M: ProviderAnswer](model: type[M], answer: object, service: str, url: str) -> M:
    """``answer`` read as ``model``; an error that says ``service`` answered with what Dot does not read."""
    try:
        return model.model_validate(answer)
    except ValidationError as error:
        raise AccountApiError(
            f"{service} answered with what Dot does not read", {"path": path_of(url), "errors": error.error_count()}
        ) from error


@dataclass(frozen=True)
class AccountApi:
    """The calls of the account ``address`` at ``provider``."""

    accounts: Accounts
    provider: ProviderId
    address: str

    async def get(
        self, service: str, url: str, params: Sequence[tuple[str, str]], headers: Mapping[str, str]
    ) -> object:
        """The JSON ``service`` answers a GET of ``url`` with; ``service`` names the API in errors (``Gmail``)."""
        token: str = await self.accounts.access_token(self.provider, self.address)
        response: httpx.Response = await self._get(service, url, params, headers, token)
        if response.status_code == 401:
            token = await self.accounts.refreshed_token(self.provider, self.address, token)
            response = await self._get(service, url, params, headers, token)
        if response.status_code != 200:
            raise refused(service, url, response)
        try:
            return response.json()
        except ValueError as error:
            raise AccountApiError(f"{service}'s answer was not JSON", {"path": path_of(url)}) from error

    async def _get(
        self, service: str, url: str, params: Sequence[tuple[str, str]], headers: Mapping[str, str], token: str
    ) -> httpx.Response:
        async def once() -> httpx.Response:
            async with self.accounts.client() as client:
                response: httpx.Response = await client.get(
                    url, params=list(params), headers={**headers, "Authorization": f"Bearer {token}"}
                )
            if response.status_code in _BUSY:
                raise _Busy(response)
            return response

        try:
            return await retry_async(once, _again, _wait_of, _RETRY, f"{service.lower().replace(' ', '_')}_get")
        except _Busy as busy:
            raise refused(service, url, busy.response) from busy
        except httpx.HTTPError as error:
            raise AccountApiError(
                f"{service} could not be reached", {"path": path_of(url), "error": type(error).__name__}
            ) from error
