"""The adapter that keeps the models' prices (item 1856): reads ``prices.json`` from Dot's home, fetches the catalogue
again when it is a day old (a conditional request with its ETag), and holds the book the estimates read.

A fetch is tried :data:`TRIES` times with a growing wait, each failure logged as a warning, then the last error is
raised; the loop (:func:`keep_prices`) logs it as an error and tries again an hour later. Until a fetch succeeds the
book Dot had stays, its date shown beside every estimate; with none at all every estimate is unknown.
"""

import asyncio
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from dotagent.core.errors import PricingError
from dotagent.core.logs import get_logger
from dotagent.core.paths import prices_path
from dotagent.llm.pricing import PriceBook, catalogue_url, read_catalogue

log = get_logger(__name__)

TRIES: int = 3
FIRST_WAIT_S: float = 2.0
TIMEOUT_S: float = 30.0
# The book is fetched again once it is this old, and a failed fetch is tried again after the second.
REFRESH_AFTER_S: float = 24 * 3600.0
RETRY_AFTER_S: float = 3600.0
_SHOWN_CHARS: int = 300


def read_book(path: Path) -> PriceBook | None:
    """The book kept at ``path``, ``None`` when there is none; a :class:`PricingError` when it cannot be read."""
    if not path.exists():
        return None
    try:
        return PriceBook.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise PricingError("the kept prices cannot be read", {"path": str(path), "error": str(error)}) from error


def write_book(path: Path, book: PriceBook) -> None:
    """Keep ``book`` at ``path``, replacing the file at once so that a reader never sees half of it."""
    partial: Path = path.with_suffix(".partial")
    partial.write_text(book.model_dump_json(), encoding="utf-8")
    partial.replace(path)


async def _fetch_once(client: httpx.AsyncClient, url: str, before: PriceBook | None, now: float) -> PriceBook:
    """One conditional request for the catalogue: the book it makes, or ``before`` with a new date when the catalogue
    did not change (304)."""
    headers: dict[str, str] = {}
    if before is not None and before.etag is not None and before.source == url:
        headers["If-None-Match"] = before.etag
    response: httpx.Response = await client.get(url, headers=headers)
    if response.status_code == 304 and before is not None:
        return before.model_copy(update={"fetched_at": now})
    if response.status_code != 200:
        raise PricingError(
            "the price list answered with an error",
            {"url": url, "status": response.status_code, "body": response.text[:_SHOWN_CHARS]},
        )
    try:
        prices = read_catalogue(response.text)
    except ValueError as error:
        raise PricingError(
            "the price list is not one Dot can read",
            {"url": url, "error": str(error), "body": response.text[:_SHOWN_CHARS]},
        ) from error
    return PriceBook(fetched_at=now, source=url, etag=response.headers.get("etag"), prices=prices)


async def fetch_book(url: str, before: PriceBook | None, now: float) -> PriceBook:
    """The book from the catalogue at ``url``, tried :data:`TRIES` times with a wait that doubles from
    :data:`FIRST_WAIT_S`; each failure is logged, and the last one raised as a :class:`PricingError`."""
    last: PricingError | None = None
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        for attempt in range(1, TRIES + 1):
            try:
                return await _fetch_once(client, url, before, now)
            except httpx.HTTPError as error:
                last = PricingError(
                    "the price list could not be fetched", {"url": url, "error": f"{type(error).__name__}: {error}"}
                )
            except PricingError as error:
                last = error
            log.warning("prices_fetch_failed", attempt=attempt, tries=TRIES, error=last.message, context=last.context)
            if attempt < TRIES:
                await asyncio.sleep(FIRST_WAIT_S * 2 ** (attempt - 1))
    assert last is not None
    raise last


class PriceList:
    """The book the estimates read, kept in Dot's home and fetched again when it is old."""

    def __init__(self, path: Path, url: str) -> None:
        self._path: Path = path
        self._url: str = url
        self._book: PriceBook | None = None

    @property
    def book(self) -> PriceBook | None:
        return self._book

    @property
    def url(self) -> str:
        return self._url

    async def load(self) -> None:
        """Read the book kept on disk, when there is one. One that cannot be read is logged as an error and fetched
        again by :func:`keep_prices` (it is a copy of the public list): the daemon starts all the same."""
        try:
            self._book = await asyncio.to_thread(read_book, self._path)
        except PricingError as error:
            log.error("prices_not_read", error=error.message, context=error.context)
            self._book = None

    def due(self, now: float) -> bool:
        """Whether the book should be fetched again: fetching is on and it is missing or a day old."""
        return self._url != "" and (self._book is None or now - self._book.fetched_at >= REFRESH_AFTER_S)

    async def refresh(self, now: float) -> PriceBook:
        """Fetch the book again and keep it; a :class:`PricingError` when it could not be (the old book stays)."""
        book: PriceBook = await fetch_book(self._url, self._book, now)
        await asyncio.to_thread(write_book, self._path, book)
        self._book = book
        log.info("prices_fetched", url=self._url, providers=len(book.prices), etag=book.etag)
        return book


def open_price_list() -> PriceList:
    """The price list of Dot's home, at the configured catalogue address (empty: never fetched)."""
    return PriceList(prices_path(), catalogue_url())


async def keep_prices(prices: PriceList, stopping: asyncio.Event) -> None:
    """Fetch the book whenever it is due, until ``stopping`` is set: a failure is logged as an error and tried again
    an hour later, with the book Dot had kept meanwhile."""
    while not stopping.is_set():
        wait: float = REFRESH_AFTER_S
        now: float = time.time()
        if prices.due(now):
            try:
                await prices.refresh(now)
            except PricingError as error:
                log.error("prices_not_fetched", error=error.message, context=error.context)
                wait = RETRY_AFTER_S
        elif prices.book is not None:
            wait = max(60.0, REFRESH_AFTER_S - (now - prices.book.fetched_at))
        if prices.url == "":
            return
        try:
            async with asyncio.timeout(wait):
                await stopping.wait()
        except TimeoutError:
            continue
