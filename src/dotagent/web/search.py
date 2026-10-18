"""Web search as a ladder of engines, so that one engine's bot check is a rung skipped, not a failed call.

The rungs, in the order ``auto`` climbs them: a SearXNG instance of the owner's own when ``[web] searxng_url`` names
one (its engines do the work, no key and no bot check from Dot's side), the hosted search of Ollama (needs a key, has no
bot check), the HTML page of DuckDuckGo, the HTML page of Brave Search (both scraped, no key), and DuckDuckGo's HTML
page opened in Dot's own Chrome, where its bot check runs and passes. An engine that limits this machine is benched
for a while, so the searches that follow skip it at once instead of waiting on it; the one answer is remembered for a
while, so the same query asked again costs no engine anything. The result names the engine that answered and every rung
skipped, with its reason.

Bing is not a rung: without a session its HTML page answers some queries with the results of another query, and a
wrong answer is worse than a skipped rung (measured 2026-10-11: three of six queries).
"""

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dotagent.core.errors import WebError, WebThrottledError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.web.client import WebClient
from dotagent.web.ladder import BENCH_S, EngineBench, SearchEngine, SearchOutcome, Skipped
from dotagent.web.text import decode_body
from dotagent.web.types import SearchHit

log = get_logger(__name__)

# What the configuration may say: ``auto`` is the whole ladder (SearXNG when its URL is set, the hosted search of
# Ollama when its key is present, the browser rung when the run's browser is Dot's own Chrome); a named engine is that
# engine alone.
SearchChoice = Literal["auto", "searxng", "duckduckgo", "brave", "ollama", "anthropic"]
# Where the Anthropic rung stands when its key is present: after Ollama's free search (auto), before it (first), or
# nowhere (off); ``None`` at the call site means the key is absent.
AnthropicRung = Literal["auto", "first", "off"]

# One try of one engine: the hits for a query, at most the number asked.
type Attempt = Callable[[str, int], Awaitable[tuple[SearchHit, ...]]]
# A page read in Dot's own Chrome: the HTML after JavaScript ran. The web tools hand it in so that this module does
# not depend on the browser.
type ReadPage = Callable[[str], Awaitable[str]]

_DUCKDUCKGO_URL: str = "https://html.duckduckgo.com/html/"
_DUCKDUCKGO_HOSTS: frozenset[str] = frozenset({"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"})
_BRAVE_URL: str = "https://search.brave.com/search"
_BRAVE_HOSTS: frozenset[str] = frozenset({"search.brave.com"})
_OLLAMA_SEARCH_URL: str = "https://ollama.com/api/web_search"
_OLLAMA_MAX_RESULTS: int = 10
# The JSON answer of a SearXNG instance, and the time an instance has to compose one.
_SEARXNG_ANSWER_BYTES: int = 2_000_000
_SEARXNG_TIMEOUT_S: float = 20.0
_ANTHROPIC_MESSAGES_URL: str = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION: str = "2023-06-01"
_ANTHROPIC_SEARCH_TOOL: str = "web_search_20250305"
# Room for the one sentence the relay writes after the search; the results come in the tool block, not in the text.
_ANTHROPIC_MAX_TOKENS: int = 400
_ANTHROPIC_SYSTEM: str = (
    "You are a search relay. Call web_search exactly once with the user's words as the query, then answer in one "
    "short sentence that cites the results. Do not answer from memory."
)
# The error codes of the server tool that say the service is limiting this key or is down: the rung sits out.
_ANTHROPIC_BENCHED_CODES: frozenset[str] = frozenset({"too_many_requests", "unavailable"})
_PAGE_BYTES: int = 1_000_000
_API_BYTES: int = 1_000_000
_MAX_SNIPPET_CHARS: int = 600
_MAX_TITLE_CHARS: int = 200
_ERROR_BODY_CHARS: int = 500
# DuckDuckGo answers 202 with a bot check instead of results; 429 is the plain rate limit of both scraped engines (Brave
# reaches it after a handful of searches in quick succession), and 403 a machine turned away.
_DUCKDUCKGO_THROTTLED_STATUS: frozenset[int] = frozenset({202, 429})
_BRAVE_THROTTLED_STATUS: frozenset[int] = frozenset({403, 429})


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


def brave_address(href: str) -> str | None:
    """The page a Brave result link leads to: an absolute web address; Brave's own pages are dropped."""
    absolute: str = f"https:{href}" if href.startswith("//") else href
    try:
        parts = urlsplit(absolute)
        host: str | None = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or host in _BRAVE_HOSTS:
        return None
    return absolute


class _BraveParser(HTMLParser):
    """Reads the result blocks of ``search.brave.com/search``: a ``div[data-type="web"]`` holding the result's link, a
    ``title`` div and a ``description`` div (the class names of the page carry build hashes; the data attribute and
    these two words are what stays). The page has no empty-result notice this parser knows, so a page without web
    results is reported as such and the engine benched briefly."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.drafts: list[_Draft] = []
        self._depth: int = 0
        self._capture: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str] = {name: value or "" for name, value in attrs}
        classes: tuple[str, ...] = tuple(attributes.get("class", "").split())
        if tag == "div" and attributes.get("data-type") == "web":
            self.drafts.append(_Draft(advertisement=False))
            self._depth = 1
            self._capture = None
            return
        if self._depth == 0:
            return
        draft: _Draft = self.drafts[-1]
        if tag == "div":
            self._depth += 1
            if "title" in classes:
                self._capture = draft.title
            elif "description" in classes or "snippet-description" in classes:
                self._capture = draft.snippet
        elif tag == "a" and not draft.href and attributes.get("href", "").startswith(("http://", "https://", "//")):
            draft.href = attributes["href"]

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag == "div":
            self._capture = None
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture.append(data)


def parse_brave(html: str) -> ResultsPage:
    """The web results of a Brave Search result page, in order, each address once."""
    parser: _BraveParser = _BraveParser()
    parser.feed(html)
    parser.close()
    hits: dict[str, SearchHit] = {}
    for draft in parser.drafts:
        address: str | None = brave_address(draft.href)
        title: str = _flat("".join(draft.title), _MAX_TITLE_CHARS)
        if address is not None and title and address not in hits:
            hits[address] = SearchHit(
                title=title, url=address, snippet=_flat("".join(draft.snippet), _MAX_SNIPPET_CHARS)
            )
    return ResultsPage(hits=tuple(hits.values()), announced_no_results=False)


def searxng_search_url(base_url: str, query: str) -> str:
    """The JSON search address of a SearXNG instance for a query (the instance lists ``json`` in ``search.formats``)."""
    return f"{base_url.rstrip('/')}/search?q={quote_plus(query)}&format=json&safesearch=0"


def parse_searxng(payload: bytes) -> ResultsPage:
    """The hits of a SearXNG JSON answer, in the instance's order, each address once. An empty ``results`` array is the
    instance saying it matched nothing; a body of any other shape is an error, not an empty page."""
    try:
        answer: _SearxAnswer = _SearxAnswer.model_validate_json(payload)
    except ValidationError as exc:
        raise WebError(
            "the SearXNG instance answered without the JSON of a search",
            {"bytes": len(payload), "error": str(exc)[:_ERROR_BODY_CHARS]},
        ) from exc
    hits: dict[str, SearchHit] = {}
    for entry in answer.results:
        address: str | None = result_address(entry.url)
        title: str = _flat(entry.title, _MAX_TITLE_CHARS)
        if address is not None and title and address not in hits:
            hits[address] = SearchHit(title=title, url=address, snippet=_flat(entry.content, _MAX_SNIPPET_CHARS))
    if not answer.results and answer.unresponsive_engines:
        raise WebError(
            "the SearXNG instance's engines did not answer the search",
            {"unresponsive": ", ".join(f"{engine}: {why}" for engine, why in answer.unresponsive_engines[:3])},
        )
    return ResultsPage(hits=tuple(hits.values()), announced_no_results=not answer.results)


async def search_searxng(client: WebClient, base_url: str, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through a SearXNG instance's JSON API: its own engines (Google, Bing, DuckDuckGo, Brave, ...) compose the
    answer, so the quality is theirs and no key is spent. The instance is configuration, not a page a run picked, so a
    loopback instance is reached without the address gate; that is the usual case."""
    url: str = searxng_search_url(base_url, query)
    headers: dict[str, str] = {"User-Agent": client.user_agent, "Accept": "application/json"}
    timeout: httpx.Timeout = httpx.Timeout(10.0, connect=5.0, read=_SEARXNG_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            response: httpx.Response = await http.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise WebError("the SearXNG instance did not answer in time", {"query": query, "url": base_url}) from exc
    except httpx.HTTPError as exc:
        raise WebError(
            "the SearXNG instance could not be reached", {"query": query, "url": base_url, "error": str(exc)[:200]}
        ) from exc
    if response.status_code == 429:
        raise WebThrottledError(
            "the SearXNG instance is limiting the searches of this machine", {"query": query, "status": 429}
        )
    if response.status_code == 403:
        # SearXNG refuses a format its settings do not list; a limit of its own is a 429.
        raise WebError(
            "the SearXNG instance refused its JSON format: list json in search.formats of its settings.yml",
            {"query": query, "url": base_url, "status": 403},
        )
    if response.status_code != 200:
        raise WebError(
            "the SearXNG instance did not answer the search",
            {"status": response.status_code, "body": response.text[:_ERROR_BODY_CHARS]},
        )
    return parse_searxng(response.content[:_SEARXNG_ANSWER_BYTES]).hits[:max_results]


def search_ladder(
    choice: SearchChoice,
    ollama_key_present: bool,
    anthropic: AnthropicRung | None,
    browser_present: bool,
    searxng_present: bool,
) -> tuple[SearchEngine, ...]:
    """The engines a search climbs, in order. ``auto`` is the whole ladder: the owner's SearXNG instance when its URL is
    set, Ollama's hosted search when its key is present, the Anthropic rung where ``anthropic`` puts it (``None``: no
    key), DuckDuckGo, Brave, and DuckDuckGo in Dot's own Chrome when the run's browser is Dot's own. A named engine is
    that engine alone."""
    match choice:
        case "auto":
            searx: tuple[SearchEngine, ...] = ("searxng",) if searxng_present else ()
            ollama: tuple[SearchEngine, ...] = ("ollama",) if ollama_key_present else ()
            hosted: tuple[SearchEngine, ...] = () if anthropic is None or anthropic == "off" else ("anthropic",)
            head: tuple[SearchEngine, ...] = (*hosted, *ollama) if anthropic == "first" else (*ollama, *hosted)
            last: tuple[SearchEngine, ...] = ("browser",) if browser_present else ()
            return (*searx, *head, "duckduckgo", "brave", *last)
        case "searxng" | "duckduckgo" | "brave" | "ollama" | "anthropic":
            return (choice,)


def bench_note(remaining_s: float) -> str:
    return f"benched for {max(1, round(remaining_s / 60))} more min after it limited this machine"


async def climb(
    rungs: tuple[SearchEngine, ...],
    attempts: Mapping[SearchEngine, Attempt],
    bench: EngineBench,
    query: str,
    max_results: int,
    now: Callable[[], float],
) -> SearchOutcome:
    """Ask the engines of ``rungs`` in turn until one answers. A benched engine is skipped with the time it has left;
    one that limits this machine is benched for its ``BENCH_S`` and skipped; one that fails otherwise is skipped with
    its error. When every rung is skipped the error names each engine and its reason."""
    skipped: list[Skipped] = []
    for engine in rungs:
        remaining: float | None = bench.remaining(engine, now())
        if remaining is not None:
            skipped.append(Skipped(engine=engine, reason=bench_note(remaining)))
            continue
        try:
            hits: tuple[SearchHit, ...] = await attempts[engine](query, max_results)
        except WebThrottledError as error:
            bench.bench(engine, BENCH_S[engine], now())
            log.warning("search_engine_benched", engine=engine, seconds=BENCH_S[engine], error=error.message)
            skipped.append(Skipped(engine=engine, reason=error.message))
        except WebError as error:
            log.warning("search_engine_failed", engine=engine, error=error.message)
            skipped.append(Skipped(engine=engine, reason=error.message))
        else:
            return SearchOutcome(hits=hits, engine=engine, skipped=tuple(skipped), age_s=0.0)
    raise WebError(
        "; ".join(f"{entry.engine}: {entry.reason}" for entry in skipped),
        {"query": query, "engines": [entry.engine for entry in skipped]},
    )


async def search(
    client: WebClient,
    rungs: tuple[SearchEngine, ...],
    attempts: Mapping[SearchEngine, Attempt],
    query: str,
    max_results: int,
) -> SearchOutcome:
    """A query's outcome: the one remembered from the last ``MEMORY_S`` when there is one (cut to ``max_results``),
    else the ladder climbed and the outcome kept."""
    now: float = time.monotonic()
    remembered: SearchOutcome | None = client.search_memory.recall(query, now)
    if remembered is not None:
        return remembered.model_copy(update={"hits": remembered.hits[:max_results]})
    outcome: SearchOutcome = await climb(rungs, attempts, client.search_bench, query, max_results, time.monotonic)
    client.search_memory.keep(query, outcome, now)
    return outcome


class AnthropicSearch(FrozenModel):
    """What the Anthropic rung needs: the key and the model (``claude-haiku-5-5`` by default) that relays the search."""

    api_key: str
    model: str


def engine_attempts(
    client: WebClient,
    ollama_key: str | None,
    anthropic: AnthropicSearch | None,
    read_page: ReadPage | None,
    searxng_url: str | None,
) -> dict[SearchEngine, Attempt]:
    """The tries of the engines this run can ask: SearXNG's with the URL of the owner's instance, Ollama's with its
    key, Anthropic's with its key and model, the browser's with a page reader."""

    async def duckduckgo(query: str, max_results: int) -> tuple[SearchHit, ...]:
        return await search_duckduckgo(client, query, max_results)

    async def brave(query: str, max_results: int) -> tuple[SearchHit, ...]:
        return await search_brave(client, query, max_results)

    attempts: dict[SearchEngine, Attempt] = {"duckduckgo": duckduckgo, "brave": brave}
    if searxng_url is not None and searxng_url.strip():
        instance: str = searxng_url.strip()

        async def searxng(query: str, max_results: int) -> tuple[SearchHit, ...]:
            return await search_searxng(client, instance, query, max_results)

        attempts["searxng"] = searxng
    else:

        async def searxng_unconfigured(query: str, max_results: int) -> tuple[SearchHit, ...]:
            raise WebError(
                'search_backend = "searxng" needs [web] searxng_url (the address of a SearXNG instance)',
                {"query": query},
            )

        attempts["searxng"] = searxng_unconfigured
    if ollama_key is not None:
        key: str = ollama_key

        async def ollama(query: str, max_results: int) -> tuple[SearchHit, ...]:
            return await search_ollama(client, key, query, max_results)

        attempts["ollama"] = ollama
    if anthropic is not None:
        relay: AnthropicSearch = anthropic

        async def hosted(query: str, max_results: int) -> tuple[SearchHit, ...]:
            return await search_anthropic(client, relay, query, max_results)

        attempts["anthropic"] = hosted
    if read_page is not None:
        reader: ReadPage = read_page

        async def browser(query: str, max_results: int) -> tuple[SearchHit, ...]:
            return await search_in_browser(reader, query, max_results)

        attempts["browser"] = browser
    return attempts


def _results_or_error(page: ResultsPage, engine: str, query: str, size: int, max_results: int) -> tuple[SearchHit, ...]:
    """The hits of a result page; a page with neither results nor an empty-result notice is the engine's bot check or
    a changed layout (or, for Brave, which has no notice this module reads, a query nothing matches), so the engine
    sits out briefly and the next rung answers."""
    if not page.hits and not page.announced_no_results:
        raise WebThrottledError(
            f"{engine} answered with a page that has no results and no empty-result notice (a bot check, a changed "
            "page layout or a query nothing matches)",
            {"query": query, "bytes": size},
        )
    return page.hits[:max_results]


async def search_duckduckgo(client: WebClient, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through the HTML page of DuckDuckGo; the scraped searches start at a steady pace."""
    await client.search_gate.turn()
    document = await client.get(duckduckgo_url(query), _PAGE_BYTES)
    if document.status in _DUCKDUCKGO_THROTTLED_STATUS:
        raise WebThrottledError(
            "DuckDuckGo is limiting the searches of this machine (its bot check)",
            {"query": query, "status": document.status},
        )
    if document.status != 200:
        raise WebError("DuckDuckGo did not answer the search", {"query": query, "status": document.status})
    page: ResultsPage = parse_duckduckgo(decode_body(document.body, document.charset))
    return _results_or_error(page, "DuckDuckGo", query, len(document.body), max_results)


def duckduckgo_url(query: str) -> str:
    return f"{_DUCKDUCKGO_URL}?q={quote_plus(query)}"


def brave_url(query: str) -> str:
    return f"{_BRAVE_URL}?q={quote_plus(query)}&source=web"


async def search_brave(client: WebClient, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through the HTML page of Brave Search; the scraped searches start at a steady pace."""
    await client.search_gate.turn()
    document = await client.get(brave_url(query), _PAGE_BYTES)
    if document.status in _BRAVE_THROTTLED_STATUS:
        raise WebThrottledError(
            "Brave Search is limiting the searches of this machine", {"query": query, "status": document.status}
        )
    if document.status != 200:
        raise WebError("Brave Search did not answer the search", {"query": query, "status": document.status})
    page: ResultsPage = parse_brave(decode_body(document.body, document.charset))
    return _results_or_error(page, "Brave Search", query, len(document.body), max_results)


async def search_in_browser(read_page: ReadPage, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """Search through DuckDuckGo's HTML page opened in Dot's own Chrome, where its bot check runs and passes; the page
    is read like the plain one."""
    html: str = await read_page(duckduckgo_url(query))
    return _results_or_error(parse_duckduckgo(html), "DuckDuckGo in Dot's browser", query, len(html), max_results)


class _SearxResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    url: str
    title: str = ""
    content: str = ""


class _SearxAnswer(BaseModel):
    """An instance that matched nothing answers with an empty ``results`` array; one whose engines all refused names
    them in ``unresponsive_engines``, each a pair of the engine and the reason."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    results: list[_SearxResult] = Field(default_factory=list)
    unresponsive_engines: list[list[str]] = Field(default_factory=list)


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


class _AnthropicCitation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    url: str
    cited_text: str


class _AnthropicResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    url: str
    title: str
    page_age: str | None = None


class _AnthropicToolError(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    error_code: str


class _AnthropicBlock(BaseModel):
    """A block of the answer: a ``text`` with its citations, or a ``web_search_tool_result`` whose content is the list
    of results or one error object."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str
    text: str | None = None
    citations: list[_AnthropicCitation] | None = None
    content: list[_AnthropicResult] | _AnthropicToolError | None = None


class _AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    content: list[_AnthropicBlock]


def anthropic_search_body(model: str, query: str) -> bytes:
    """The one Messages request of a search: the relay prompt, the query and the server tool with one use."""
    return json.dumps(
        {
            "model": model,
            "max_tokens": _ANTHROPIC_MAX_TOKENS,
            "system": _ANTHROPIC_SYSTEM,
            "messages": [{"role": "user", "content": query}],
            "tools": [{"type": _ANTHROPIC_SEARCH_TOOL, "name": "web_search", "max_uses": 1}],
        }
    ).encode()


def parse_anthropic(body: bytes, query: str, max_results: int) -> tuple[SearchHit, ...]:
    """The hits of a Messages answer: the results of its ``web_search_tool_result`` block, each with the text the
    relay cited from it as the snippet (empty when it cited nothing from that page). A tool error that says the
    service limits this key or is down benches the rung; another one fails the search with its code."""
    try:
        message: _AnthropicMessage = _AnthropicMessage.model_validate_json(body)
    except ValidationError as exc:
        raise WebError(
            "the Anthropic API answered with a body that does not match its documented shape",
            {"query": query, "error": str(exc)[:_ERROR_BODY_CHARS]},
        ) from exc
    cited: dict[str, str] = {}
    results: list[_AnthropicResult] = []
    for block in message.content:
        if block.type == "web_search_tool_result":
            if isinstance(block.content, _AnthropicToolError):
                code: str = block.content.error_code
                if code in _ANTHROPIC_BENCHED_CODES:
                    raise WebThrottledError(
                        f"the Anthropic web search is limiting this key or is unavailable ({code})",
                        {"query": query, "code": code},
                    )
                raise WebError(f"the Anthropic web search refused the search ({code})", {"query": query, "code": code})
            results.extend(block.content or [])
        elif block.type == "text":
            for citation in block.citations or []:
                cited.setdefault(citation.url, citation.cited_text)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        age: str = "" if result.page_age is None else f" ({result.page_age})"
        hits.append(
            SearchHit(
                title=_flat(result.title, _MAX_TITLE_CHARS),
                url=result.url,
                snippet=_flat(f"{cited.get(result.url, '')}{age}".strip(), _MAX_SNIPPET_CHARS),
            )
        )
    return tuple(hits[:max_results])


async def search_anthropic(
    client: WebClient, relay: AnthropicSearch, query: str, max_results: int
) -> tuple[SearchHit, ...]:
    """Search through the Messages API's own ``web_search`` server tool, relayed by ``relay.model``: one request, one
    search (the API bills each search next to the tokens; a failed search is not billed)."""
    headers: dict[str, str] = {"x-api-key": relay.api_key, "anthropic-version": _ANTHROPIC_VERSION}
    document = await client.post_json(
        _ANTHROPIC_MESSAGES_URL, headers, anthropic_search_body(relay.model, query), _API_BYTES
    )
    if document.status == 429:
        raise WebThrottledError(
            "the Anthropic API is limiting this key (HTTP 429)", {"query": query, "status": document.status}
        )
    if document.status != 200:
        raise WebError(
            "the Anthropic API did not answer the search",
            {
                "query": query,
                "status": document.status,
                "body": decode_body(document.body, document.charset)[:_ERROR_BODY_CHARS],
            },
        )
    return parse_anthropic(document.body, query, max_results)
