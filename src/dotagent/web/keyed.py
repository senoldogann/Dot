"""The search APIs that take a key of their own (item 655): Brave's Search API, Tavily, Exa and Serper, each a rung of
web_search when Settings › Web search puts it in the order and its key is set; and the answer of the search of the
web-research server that comes with Dot, read into hits as the other rungs' are.

A key goes in the request's header and nowhere else: not in an address, a log line, an error or an answer. An answer
that limits the key (HTTP 429) benches the rung as an engine that limits this machine is; a key the API refuses (401,
403) is said to be, so that Settings › Web search can tell the owner which one to look at."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dotagent.core.errors import WebError, WebThrottledError
from dotagent.core.text import redact_known
from dotagent.core.types import JsonObject
from dotagent.secrets import optional_secret
from dotagent.web.client import WebClient
from dotagent.web.ladder import SearchEngine
from dotagent.web.text import decode_body
from dotagent.web.types import FetchedDocument, SearchHit

# The variable that holds the key of each search API, as Dot's key file and Settings › Web search name it.
SEARCH_KEYS: Mapping[SearchEngine, str] = {
    "brave_api": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
    "serper": "SERPER_API_KEY",
}
_BRAVE_API_URL: str = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_URL: str = "https://api.tavily.com/search"
_EXA_URL: str = "https://api.exa.ai/search"
_SERPER_URL: str = "https://google.serper.dev/search"
# Brave's API gives at most 20 results a request, the others at most 20 we ask for.
_MOST_RESULTS: int = 20
_API_BYTES: int = 1_000_000
_ERROR_BODY_CHARS: int = 300
_TITLE_CHARS: int = 200
_SNIPPET_CHARS: int = 600
_TAGS: re.Pattern[str] = re.compile(r"<[^>]+>")
_SPACES: re.Pattern[str] = re.compile(r"\s+")


async def keyed_engines() -> frozenset[SearchEngine]:
    """The search APIs whose keys are set, wherever Dot reads keys."""
    found: list[SearchEngine] = []
    for engine, variable in SEARCH_KEYS.items():
        if await asyncio.to_thread(optional_secret, variable) is not None:
            found.append(engine)
    return frozenset(found)


# The web-research server's search, as web_search's rung asks it: the query and how many results, its answer.
WebResearchSearch = Callable[[str, int], Awaitable[JsonObject]]


def _plain(text: str, limit: int) -> str:
    """``text`` with its HTML marks taken out (Brave marks the query's words) and its spaces run together."""
    return _SPACES.sub(" ", _TAGS.sub("", text)).strip()[:limit]


def _checked(document: FetchedDocument, label: str, query: str, key: str) -> bytes:
    """The body of an API's answer that succeeded; a limited key benches the rung, a refused key says so. The start of
    a body that is no answer goes into the error with the key masked, should the API echo it."""
    if document.status == 429:
        raise WebThrottledError(f"{label} is limiting this key (HTTP 429)", {"query": query, "status": 429})
    if document.status in {401, 403}:
        raise WebError(
            f"{label} refused the key (HTTP {document.status}): check it in Settings › Web search",
            {"query": query, "status": document.status},
        )
    if document.status != 200:
        raise WebError(
            f"{label} did not answer the search",
            {
                "query": query,
                "status": document.status,
                "body": redact_known(decode_body(document.body, document.charset), (key,))[:_ERROR_BODY_CHARS],
            },
        )
    return document.body


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _BraveResult(_Loose):
    title: str = ""
    url: str
    description: str = ""


class _BraveWeb(_Loose):
    results: list[_BraveResult] = Field(default_factory=list)


class _BraveAnswer(_Loose):
    web: _BraveWeb = Field(default_factory=_BraveWeb)


class _TavilyResult(_Loose):
    title: str = ""
    url: str
    content: str = ""


class _TavilyAnswer(_Loose):
    results: list[_TavilyResult] = Field(default_factory=list)


class _ExaResult(_Loose):
    title: str | None = None
    url: str
    highlights: list[str] = Field(default_factory=list)
    publishedDate: str | None = None


class _ExaAnswer(_Loose):
    results: list[_ExaResult] = Field(default_factory=list)


class _SerperResult(_Loose):
    title: str = ""
    link: str
    snippet: str = ""
    date: str | None = None


class _SerperAnswer(_Loose):
    organic: list[_SerperResult] = Field(default_factory=list)


class _WebResearchResult(_Loose):
    title: str | None = None
    url: str
    snippet: str | None = None
    date: str | None = None


class _WebResearchAnswer(_Loose):
    results: list[_WebResearchResult] = Field(default_factory=list)


def _parsed[M: BaseModel](model: type[M], body: bytes, label: str, query: str) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise WebError(
            f"{label} answered with a body that does not match its documented shape",
            {"query": query, "error": str(exc)[:_ERROR_BODY_CHARS]},
        ) from exc


def _dated(date: str | None, text: str) -> str:
    """A snippet with the result's date before it, when the API gave one (``2026-10-24 · …``)."""
    day: str = (date or "")[:10]
    return f"{day} · {text}" if day else text


async def search_brave_api(client: WebClient, key: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Brave's Search API (``GET /res/v1/web/search``, the key in ``X-Subscription-Token``)."""
    url: str = f"{_BRAVE_API_URL}?q={quote_plus(query)}&count={min(max_results, _MOST_RESULTS)}"
    body: bytes = _checked(
        await client.get_json(url, {"X-Subscription-Token": key}, _API_BYTES), "Brave's Search API", query, key
    )
    answer: _BraveAnswer = _parsed(_BraveAnswer, body, "Brave's Search API", query)
    return tuple(
        SearchHit(title=_plain(hit.title, _TITLE_CHARS), url=hit.url, snippet=_plain(hit.description, _SNIPPET_CHARS))
        for hit in answer.web.results[:max_results]
    )


async def search_tavily(client: WebClient, key: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Tavily's search API (``POST /search``, a bearer key)."""
    request: bytes = json.dumps({"query": query, "max_results": min(max_results, _MOST_RESULTS)}).encode()
    body: bytes = _checked(
        await client.post_json(_TAVILY_URL, {"Authorization": f"Bearer {key}"}, request, _API_BYTES),
        "Tavily",
        query,
        key,
    )
    answer: _TavilyAnswer = _parsed(_TavilyAnswer, body, "Tavily", query)
    return tuple(
        SearchHit(title=_plain(hit.title, _TITLE_CHARS), url=hit.url, snippet=_plain(hit.content, _SNIPPET_CHARS))
        for hit in answer.results[:max_results]
    )


async def search_exa(client: WebClient, key: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Exa's search API (``POST /search``, the key in ``x-api-key``), with a highlight of each page as its snippet."""
    request: bytes = json.dumps(
        {
            "query": query,
            "numResults": min(max_results, _MOST_RESULTS),
            "contents": {"highlights": {"numSentences": 2, "highlightsPerUrl": 1}},
        }
    ).encode()
    body: bytes = _checked(await client.post_json(_EXA_URL, {"x-api-key": key}, request, _API_BYTES), "Exa", query, key)
    answer: _ExaAnswer = _parsed(_ExaAnswer, body, "Exa", query)
    return tuple(
        SearchHit(
            title=_plain(hit.title or hit.url, _TITLE_CHARS),
            url=hit.url,
            snippet=_dated(hit.publishedDate, _plain(" ".join(hit.highlights), _SNIPPET_CHARS)),
        )
        for hit in answer.results[:max_results]
    )


async def search_serper(client: WebClient, key: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Serper's Google search API (``POST /search``, the key in ``X-API-KEY``)."""
    request: bytes = json.dumps({"q": query, "num": min(max_results, _MOST_RESULTS)}).encode()
    body: bytes = _checked(
        await client.post_json(_SERPER_URL, {"X-API-KEY": key}, request, _API_BYTES), "Serper", query, key
    )
    answer: _SerperAnswer = _parsed(_SerperAnswer, body, "Serper", query)
    return tuple(
        SearchHit(
            title=_plain(hit.title, _TITLE_CHARS),
            url=hit.link,
            snippet=_dated(hit.date, _plain(hit.snippet, _SNIPPET_CHARS)),
        )
        for hit in answer.organic[:max_results]
    )


def web_research_hits(answer: JsonObject, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """The hits of the web-research server's compact search answer (its ``results``, each with its date)."""
    try:
        parsed: _WebResearchAnswer = _WebResearchAnswer.model_validate(answer)
    except ValidationError as exc:
        raise WebError(
            "the web-research search answered with a body that does not match its shape",
            {"query": query, "error": str(exc)[:_ERROR_BODY_CHARS]},
        ) from exc
    return tuple(
        SearchHit(
            title=_plain(hit.title or hit.url, _TITLE_CHARS),
            url=hit.url,
            snippet=_dated(hit.date, _plain(hit.snippet or "", _SNIPPET_CHARS)),
        )
        for hit in parsed.results[:max_results]
    )
