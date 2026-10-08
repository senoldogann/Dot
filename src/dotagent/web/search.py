"""Web search backends: the HTML page of DuckDuckGo (no key) and the hosted search API of Ollama (needs a key)."""

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.core.errors import WebError, WebThrottledError
from dotagent.core.retry import retry_async
from dotagent.web.client import WebClient
from dotagent.web.text import decode_body
from dotagent.web.types import SearchHit

_DUCKDUCKGO_URL: str = "https://html.duckduckgo.com/html/"
_DUCKDUCKGO_HOSTS: frozenset[str] = frozenset({"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"})
_OLLAMA_SEARCH_URL: str = "https://ollama.com/api/web_search"
_OLLAMA_MAX_RESULTS: int = 10
_PAGE_BYTES: int = 1_000_000
_API_BYTES: int = 1_000_000
_MAX_SNIPPET_CHARS: int = 600
_MAX_TITLE_CHARS: int = 200
_ERROR_BODY_CHARS: int = 500
# DuckDuckGo answers 202 with a bot check instead of results; 429 is the plain rate limit.
_THROTTLED_STATUS: frozenset[int] = frozenset({202, 429})
# After the bot check no search of this machine starts for this long, so a burst of waiting searches does not keep
# the engine angry.
_THROTTLE_PAUSE_S: float = 8.0


def _flat(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


@dataclass
class _Draft:
    """One result while its markup is still being read."""

    advertisement: bool
    href: str = ""
    title: list[str] = field(default_factory=list)
    snippet: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResultsPage:
    hits: tuple[SearchHit, ...]
    announced_no_results: bool


def result_address(href: str) -> str | None:
    """The page a DuckDuckGo link leads to: ``/l/?uddg=…`` wrappers are opened up, ad redirects and its own pages
    are dropped."""
    absolute: str = f"https:{href}" if href.startswith("//") else href
    try:
        parts = urlsplit(absolute)
        host: str | None = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if host not in _DUCKDUCKGO_HOSTS:
        return absolute
    if parts.path != "/l/":
        return None
    targets: list[str] = parse_qs(parts.query).get("uddg", [])
    return targets[0] if targets and targets[0].startswith(("http://", "https://")) else None


class _ResultsParser(HTMLParser):
    """Reads the result blocks of ``html.duckduckgo.com``: a ``result`` div with a ``result__a`` title link and a
    ``result__snippet`` link."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.drafts: list[_Draft] = []
        self.announced_no_results: bool = False
        self._capture: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str] = {name: value or "" for name, value in attrs}
        classes: tuple[str, ...] = tuple(attributes.get("class", "").split())
        if tag == "div" and "no-results" in classes:
            self.announced_no_results = True
        elif tag == "div" and "result" in classes:
            self.drafts.append(_Draft(advertisement="result--ad" in classes))
            self._capture = None
        elif tag == "a" and self.drafts:
            draft: _Draft = self.drafts[-1]
            if "result__a" in classes:
                draft.href = attributes.get("href", "")
                self._capture = draft.title
            elif "result__snippet" in classes:
                self._capture = draft.snippet

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture.append(data)


def parse_duckduckgo(html: str) -> ResultsPage:
    """The organic results of a DuckDuckGo HTML result page, in order, each address once."""
    parser: _ResultsParser = _ResultsParser()
    parser.feed(html)
    parser.close()
    hits: dict[str, SearchHit] = {}
    for draft in parser.drafts:
        address: str | None = None if draft.advertisement else result_address(draft.href)
        title: str = _flat("".join(draft.title), _MAX_TITLE_CHARS)
        if address is not None and title and address not in hits:
            hits[address] = SearchHit(
                title=title, url=address, snippet=_flat("".join(draft.snippet), _MAX_SNIPPET_CHARS)
            )
    return ResultsPage(hits=tuple(hits.values()), announced_no_results=parser.announced_no_results)


def _is_throttled(exc: Exception) -> bool:
    return isinstance(exc, WebThrottledError)


def _no_hint(exc: Exception) -> float | None:
    return None


async def _search_duckduckgo_once(client: WebClient, query: str, max_results: int) -> tuple[SearchHit, ...]:
    await client.search_gate.turn()
    url: str = f"{_DUCKDUCKGO_URL}?q={quote_plus(query)}"
    document = await client.get(url, _PAGE_BYTES)
    if document.status in _THROTTLED_STATUS:
        client.search_gate.cool_down(_THROTTLE_PAUSE_S)
        raise WebThrottledError(
            "DuckDuckGo is limiting the searches of this machine (its bot check). Wait a while, read known pages with "
            'web_fetch instead, or set web.search_backend = "ollama" (needs OLLAMA_API_KEY)',
            {"query": query, "status": document.status, "tries": client.search_retry.attempts},
        )
    if document.status != 200:
        raise WebError("DuckDuckGo did not answer the search", {"query": query, "status": document.status})
    page: ResultsPage = parse_duckduckgo(decode_body(document.body, document.charset))
    if not page.hits and not page.announced_no_results:
        raise WebError(
            "DuckDuckGo answered with a page that has no results and no empty-result notice (a bot check or a "
            "changed page layout)",
            {"query": query, "status": document.status, "bytes": len(document.body)},
        )
    return page.hits[:max_results]


async def search_duckduckgo(client: WebClient, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through the HTML page of DuckDuckGo. Searches start one after another at a steady pace, and one that meets
    the bot check of the engine is tried again after a pause; when the tries are used up the last error is raised. A page
    with neither results nor an empty-result notice (a changed layout) is an error rather than an empty answer."""

    async def attempt() -> tuple[SearchHit, ...]:
        return await _search_duckduckgo_once(client, query, max_results)

    return await retry_async(attempt, _is_throttled, _no_hint, client.search_retry, "duckduckgo search")


class _OllamaResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    title: str
    url: str
    content: str


class _OllamaAnswer(BaseModel):
    """``results`` is ``null`` rather than an empty array when nothing matched."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    results: list[_OllamaResult] | None


async def search_ollama(client: WebClient, api_key: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through the hosted web search API of Ollama (``POST /api/web_search``, bearer key, at most 10 results)."""
    body: bytes = json.dumps({"query": query, "max_results": min(max_results, _OLLAMA_MAX_RESULTS)}).encode()
    document = await client.post_json(_OLLAMA_SEARCH_URL, {"Authorization": f"Bearer {api_key}"}, body, _API_BYTES)
    if document.status != 200:
        raise WebError(
            "the Ollama search API did not answer the search",
            {
                "query": query,
                "status": document.status,
                "body": decode_body(document.body, document.charset)[:_ERROR_BODY_CHARS],
            },
        )
    try:
        answer: _OllamaAnswer = _OllamaAnswer.model_validate_json(document.body)
    except ValidationError as exc:
        raise WebError(
            "the Ollama search API answered with a body that does not match its documented shape",
            {"query": query, "status": document.status, "error": str(exc)[:_ERROR_BODY_CHARS]},
        ) from exc
    entries: list[_OllamaResult] = answer.results if answer.results is not None else []
    return tuple(
        SearchHit(
            title=_flat(entry.title, _MAX_TITLE_CHARS), url=entry.url, snippet=_flat(entry.content, _MAX_SNIPPET_CHARS)
        )
        for entry in entries[:max_results]
    )
