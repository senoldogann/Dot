"""The adapter over WebDriverAgent's HTTP API on the phone (the XCUITest runner app whose server listens on port 8100),
item 1196.

One ``httpx.AsyncClient`` is reused for every request. The address that answers is found from ``[phone] wda_urls`` in
order and kept until it stops answering. Only what needs no WebDriver session is asked: whether the server answers
(``/status``) and whether the phone is locked (``/wda/locked``), which the owner's iPhone answered on 2026-11-15, locked
or not. These are reads: a request that fails to get through is retried with backoff (a warning each time) and then
raised. What the phone answered goes to the log, never into an error: a run reads the error of ``phone_status`` as Dot's
own words, and an address on the home network may be answered by another device.
"""

from collections.abc import Sequence
from typing import Literal

import httpx
from pydantic import JsonValue

from dotagent.core.errors import PhoneError, PhoneUnreachableError
from dotagent.core.logs import get_logger
from dotagent.core.retry import RetryPolicy, retry_async
from dotagent.phone.types import PhoneProbe

log = get_logger(__name__)

_PROBE_TIMEOUT_S: float = 4.0
_BODY_CHARS: int = 600
_RETRY: RetryPolicy = RetryPolicy(attempts=3, base_delay_s=0.5, max_delay_s=4.0)

type Method = Literal["GET"]


def _is_retryable(error: Exception) -> bool:
    return isinstance(error, httpx.TransportError)


def _no_hint(error: Exception) -> float | None:
    return None


def _clip(text: str) -> str:
    return text if len(text) <= _BODY_CHARS else f"{text[:_BODY_CHARS]}…"


def _value_of(body: JsonValue) -> JsonValue:
    return body.get("value") if isinstance(body, dict) else None


class WdaClient:
    """WebDriverAgent as the daemon reaches it: ``urls`` in order, ``timeout_s`` for a request."""

    def __init__(self, urls: Sequence[str], timeout_s: float) -> None:
        self._urls: tuple[str, ...] = tuple(url.rstrip("/") for url in urls)
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=timeout_s)
        self._base: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _answers(self, url: str) -> bool:
        """Whether ``url`` answers ``/status`` with 200: one out of reach, or whose answer cannot be read, does not."""
        try:
            response: httpx.Response = await self._http.get(f"{url}/status", timeout=_PROBE_TIMEOUT_S)
        except httpx.TransportError:
            return False
        except httpx.HTTPError as error:
            log.warning("wda_status_unreadable", url=url, error=f"{type(error).__name__}: {error}")
            return False
        return response.status_code == 200

    async def find_base(self) -> str:
        """The first of the addresses that answers ``/status``, kept for the calls after it. Raises
        :class:`PhoneUnreachableError` when none does."""
        for url in self._urls:
            if await self._answers(url):
                self._base = url
                return url
        self._base = None
        raise PhoneUnreachableError("no address of WebDriverAgent on the phone answered", {"urls": list(self._urls)})

    async def _get(self, path: str) -> JsonValue:
        """``path`` read from the address that answers, as JSON: retried while it does not get through, then raised as
        :class:`PhoneUnreachableError`; a refusal, a body that is no JSON or an answer that cannot be read is a
        :class:`PhoneError` with the status, and the body is logged."""
        base: str = self._base or await self.find_base()
        method: Method = "GET"
        url: str = f"{base}{path}"

        async def once() -> httpx.Response:
            return await self._http.request(method, url)

        try:
            response: httpx.Response = await retry_async(once, _is_retryable, _no_hint, _RETRY, f"wda {method} {path}")
        except httpx.TransportError as error:
            self._base = None
            raise PhoneUnreachableError(
                "WebDriverAgent did not answer",
                {"method": method, "url": url, "error": f"{type(error).__name__}: {error}"},
            ) from error
        except httpx.HTTPError as error:
            log.warning("wda_unreadable", method=method, url=url, error=f"{type(error).__name__}: {error}")
            raise PhoneError(
                "WebDriverAgent's answer could not be read",
                {"method": method, "url": url, "error": f"{type(error).__name__}: {error}"},
            ) from error
        context: dict[str, JsonValue] = {"method": method, "url": url, "status": response.status_code}
        if response.status_code >= 400:
            log.warning("wda_refused", method=method, url=url, status=response.status_code, body=_clip(response.text))
            raise PhoneError("WebDriverAgent refused the request (its answer is in the daemon's log)", context)
        try:
            parsed: JsonValue = response.json()
        except ValueError as error:
            log.warning("wda_not_json", method=method, url=url, status=response.status_code, body=_clip(response.text))
            raise PhoneError("WebDriverAgent answered with something that is not JSON", context) from error
        return parsed

    async def locked(self) -> bool:
        """Whether the phone is locked, as ``/wda/locked`` says with a true or a false."""
        value: JsonValue = _value_of(await self._get("/wda/locked"))
        if not isinstance(value, bool):
            log.warning("wda_locked_unclear", url=self._base, value=repr(value)[:_BODY_CHARS])
            raise PhoneError(
                "WebDriverAgent did not say whether the phone is locked",
                {"url": self._base, "kind": type(value).__name__},
            )
        return value

    async def probe(self) -> PhoneProbe:
        """Where the phone answers and whether it is locked: ``url`` is ``None`` when no address answers, and ``locked``
        is ``None`` too when the address that answers does not say (a refusal, an answer that is no JSON or neither a
        true nor a false, each logged with what came where it was read)."""
        try:
            base: str = await self.find_base()
        except PhoneUnreachableError:
            return PhoneProbe(url=None, locked=None)
        try:
            return PhoneProbe(url=base, locked=await self.locked())
        except PhoneUnreachableError:
            return PhoneProbe(url=None, locked=None)
        except PhoneError as error:
            log.info("phone_lock_state_unclear", url=base, error=str(error))
            return PhoneProbe(url=base, locked=None)
