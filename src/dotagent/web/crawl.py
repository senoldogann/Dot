"""The crawler: fetch the pages of a site within a scope, render the ones that show little text without JavaScript, and
return one clean text document per page with the links between them.

The pure parts decide what to do: :func:`in_scope` whether a link belongs to the crawl, :func:`page_links` the links of
a page, :func:`plan_frontier` which of them to visit next, :func:`looks_like_shell` whether a page needs a browser,
:func:`robots_rules` what a site's ``robots.txt`` allows. :func:`crawl` runs them against the web client (public
addresses only, the browser's host lists, the size limits of ``[web]``) with the pacing, concurrency and limits of
``[web.crawl]``; the pages that need JavaScript are rendered one at a time in Dot's own browser through ``render``, a
callable the tool hands in so that this module does not depend on the browser.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from dotagent.config import CrawlConfig, WebConfig
from dotagent.core.errors import BrowserError, WebError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.web.client import WebClient
from dotagent.web.gate import RequestGate
from dotagent.web.pdf import pdf_to_text
from dotagent.web.text import DocumentKind, ReadablePage, decode_body, document_kind, html_to_text
from dotagent.web.types import REFUSED_STATUS, FetchedDocument

log = get_logger(__name__)

type ScopeKind = Literal["host", "prefix"]
type RenderMode = Literal["auto", "never", "always"]
type RobotsMode = Literal["report", "obey"]
# A renderer: the page's visible text after JavaScript ran, or the error that kept the browser from showing it.
type Renderer = Callable[[str, int], Awaitable[str]]

# A page with less text than this and at least this many script tags is a JavaScript shell: a browser shows the rest.
THIN_PAGE_CHARS: int = 400
SCRIPT_TAGS_OF_AN_APP: int = 3
_ROBOTS_MAX_BYTES: int = 200_000
_LINK_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_MAX_LINKS_PER_PAGE: int = 500


class Scope(FrozenModel):
    """What the crawl may visit: the pages of the start address's host, or those under its path."""

    kind: ScopeKind
    url: str


class CrawledPage(FrozenModel):
    """One page of the crawl: its address, how it was read, its text (title and description first) and the links it
    holds within the scope. ``rendered`` says a browser showed it; ``problem`` what kept the browser from doing so."""

    url: str
    depth: int
    status: int
    kind: DocumentKind
    title: str
    description: str
    text: str
    links: tuple[str, ...]
    rendered: bool
    truncated: bool
    problem: str


class SkippedPage(FrozenModel):
    url: str
    reason: str


class RobotsReport(FrozenModel):
    """What ``robots.txt`` said: whether one was read for each host, and the addresses it disallows that the crawl
    met (fetched all the same with ``robots = "report"``, left out with ``"obey"``)."""

    hosts: tuple[str, ...]
    disallowed: tuple[str, ...]


class CrawlResult(FrozenModel):
    """The pages in the order they were read, the addresses left out and why, what ``robots.txt`` said, and whether a
    limit cut the crawl."""

    start: str
    scope: Scope
    pages: tuple[CrawledPage, ...]
    skipped: tuple[SkippedPage, ...]
    robots: RobotsReport
    frontier_left: int
    seconds: float


class _LinkCollector(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__()
        self.base: str = base
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base":
            href: str | None = dict(attrs).get("href")
            if href:
                self.base = urljoin(self.base, href)
        if tag != "a":
            return
        target: str | None = dict(attrs).get("href")
        if target and len(self.links) < _MAX_LINKS_PER_PAGE:
            self.links.append(target)


def normalized(url: str) -> str:
    """``url`` without its fragment, with an empty path as ``/``: the form the crawl counts addresses in."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def page_links(html: str, base_url: str) -> tuple[str, ...]:
    """The http(s) links of ``html`` as absolute addresses without fragments, each once, in page order."""
    collector: _LinkCollector = _LinkCollector(base_url)
    collector.feed(html)
    found: dict[str, None] = {}
    for target in collector.links:
        try:
            absolute: str = normalized(urljoin(collector.base, target.strip()))
        except ValueError:
            continue
        if urlsplit(absolute).scheme in _LINK_SCHEMES:
            found[absolute] = None
    return tuple(found)


def scope_of(kind: ScopeKind, url: str) -> Scope:
    """The scope a crawl from ``url`` has: its host, or its path's folder (the address itself when it is a folder)."""
    parts = urlsplit(normalized(url))
    if kind == "host":
        return Scope(kind=kind, url=urlunsplit((parts.scheme, parts.netloc, "/", "", "")))
    folder: str = parts.path if parts.path.endswith("/") else parts.path.rsplit("/", 1)[0] + "/"
    return Scope(kind=kind, url=urlunsplit((parts.scheme, parts.netloc, folder, "", "")))


def in_scope(url: str, scope: Scope) -> bool:
    """Whether ``url`` belongs to the crawl: the same host, and under the folder for a prefix scope."""
    target = urlsplit(url)
    bound = urlsplit(scope.url)
    if (target.scheme, target.netloc.lower()) != (bound.scheme, bound.netloc.lower()):
        return False
    return scope.kind == "host" or (target.path or "/").startswith(bound.path)


def plan_frontier(links: Sequence[str], scope: Scope, seen: Sequence[str]) -> tuple[str, ...]:
    """The links to visit next: within the scope, not seen before, each once, in order."""
    known: set[str] = set(seen)
    planned: dict[str, None] = {}
    for link in links:
        if link not in known and in_scope(link, scope):
            planned[link] = None
    return tuple(planned)


def looks_like_shell(body: bytes, text_chars: int) -> bool:
    """Whether a page is a JavaScript shell: little readable text and the script tags of an application."""
    return text_chars < THIN_PAGE_CHARS and body.lower().count(b"<script") >= SCRIPT_TAGS_OF_AN_APP


def robots_rules(text: str) -> RobotFileParser:
    """The rules of a ``robots.txt``; an empty or unreadable file allows everything."""
    parser: RobotFileParser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser


def _host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _robots_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


class _Crawl:
    """The state of one crawl: what was seen, the per-host gates, the robots rules per host."""

    def __init__(
        self, web: WebClient, config: WebConfig, user_agent: str, renderer: Renderer | None, quiet: Renderer | None
    ) -> None:
        self.web: WebClient = web
        self.config: WebConfig = config
        self.crawl: CrawlConfig = config.crawl
        self.user_agent: str = user_agent
        self.renderer: Renderer | None = renderer
        # A reader of pages in a tab the user does not work in (item 98), for the pages that refuse the plain request.
        self.quiet: Renderer | None = quiet
        self.gates: dict[str, RequestGate] = {}
        self.robots: dict[str, RobotFileParser | None] = {}
        self.disallowed: list[str] = []
        self.slots: asyncio.Semaphore = asyncio.Semaphore(config.crawl.concurrency)

    def _gate(self, host: str) -> RequestGate:
        gate: RequestGate | None = self.gates.get(host)
        if gate is None:
            gate = RequestGate(self.crawl.per_host_spacing_s)
            self.gates[host] = gate
        return gate

    async def _rules(self, url: str) -> RobotFileParser | None:
        """The robots rules of the host of ``url``, read once; ``None`` when the site has no readable robots.txt."""
        host: str = _host_of(url)
        if host in self.robots:
            return self.robots[host]
        rules: RobotFileParser | None = None
        try:
            await self._gate(host).turn()
            document: FetchedDocument = await self.web.get(_robots_url(url), _ROBOTS_MAX_BYTES)
            if 200 <= document.status < 300:
                rules = robots_rules(decode_body(document.body, document.charset))
        except WebError as error:
            log.info("robots_unreadable", host=host, error=error.message)
        self.robots[host] = rules
        return rules

    async def allowed(self, url: str) -> bool:
        """Whether robots.txt allows ``url``; a disallowed address is recorded for the report."""
        rules: RobotFileParser | None = await self._rules(url)
        if rules is None or rules.can_fetch(self.user_agent, url):
            return True
        self.disallowed.append(url)
        return False

    async def _read_refused(self, document: FetchedDocument, depth: int) -> CrawledPage | SkippedPage:
        """A page that turned the plain request away, read in the browser (item 98): its text, no links (the crawl
        does not follow a page it could not fetch); when the browser could not show it, left out with both reasons."""
        try:
            text: str = await self.quiet(document.url, self.crawl.page_max_chars) if self.quiet else ""
        except BrowserError as error:
            return SkippedPage(
                url=document.url, reason=f"HTTP {document.status}; the browser could not read it: {error.message}"
            )
        if not text.strip():
            return SkippedPage(url=document.url, reason=f"HTTP {document.status}; the browser showed an empty page")
        return CrawledPage(
            url=document.url,
            depth=depth,
            status=document.status,
            kind="html",
            title="",
            description="",
            text=text[: self.crawl.page_max_chars],
            links=(),
            rendered=True,
            truncated=len(text) > self.crawl.page_max_chars,
            problem=f"the plain request got HTTP {document.status}, read in the browser",
        )

    async def _render(self, url: str) -> tuple[str, str]:
        """The visible text a browser shows for ``url``, or the problem that kept it from showing one."""
        if self.renderer is None:
            return "", "no browser was available to render the page"
        try:
            return await self.renderer(url, self.crawl.page_max_chars), ""
        except BrowserError as error:
            return "", f"the browser could not render the page: {error.message}"

    async def read(self, url: str, depth: int) -> CrawledPage | SkippedPage:
        """Fetch ``url``, read it as text, render it when it needs JavaScript, and collect its links."""
        async with self.slots:
            await self._gate(_host_of(url)).turn()
            try:
                document: FetchedDocument = await self.web.get(
                    url, max(self.config.fetch_max_bytes, self.config.pdf_max_bytes)
                )
            except WebError as error:
                return SkippedPage(url=url, reason=error.message)
        if document.status in REFUSED_STATUS and self.quiet is not None:
            return await self._read_refused(document, depth)
        if not 200 <= document.status < 300:
            return SkippedPage(url=url, reason=f"HTTP {document.status}")
        kind: DocumentKind = document_kind(document.media_type, document.body[:512])
        page: ReadablePage
        links: tuple[str, ...] = ()
        match kind:
            case "html":
                html: str = decode_body(document.body[: self.config.fetch_max_bytes], document.charset)
                page = await asyncio.to_thread(html_to_text, html, document.url)
                links = await asyncio.to_thread(page_links, html, document.url)
            case "pdf":
                page = ReadablePage(
                    title="", description="", text=await asyncio.to_thread(pdf_to_text, document.body, document.url)
                )
            case "text":
                page = ReadablePage(title="", description="", text=decode_body(document.body, document.charset))
            case "binary":
                return SkippedPage(url=url, reason=f"not a page, a PDF or a text file ({document.media_type})")
        text: str = page.text
        rendered: bool = False
        problem: str = ""
        wants_render: bool = kind == "html" and (
            self.crawl.render == "always"
            or (self.crawl.render == "auto" and looks_like_shell(document.body, len(text)))
        )
        if wants_render:
            shown, problem = await self._render(document.url)
            if shown:
                text, rendered = shown, True
        return CrawledPage(
            url=document.url,
            depth=depth,
            status=document.status,
            kind=kind,
            title=page.title,
            description=page.description,
            text=text[: self.crawl.page_max_chars],
            links=links,
            rendered=rendered,
            truncated=document.truncated or len(text) > self.crawl.page_max_chars,
            problem=problem,
        )


async def crawl(
    web: WebClient,
    config: WebConfig,
    start: str,
    scope: Scope,
    max_pages: int,
    max_depth: int,
    renderer: Renderer | None,
    quiet: Renderer | None,
) -> CrawlResult:
    """Crawl from ``start`` within ``scope``, breadth first, at most ``max_pages`` pages and ``max_depth`` links deep
    (the start is depth 0). ``renderer`` shows a page in a browser when the crawl decides one is needed; ``None`` when
    no browser can be used, which the page's ``problem`` then says. ``quiet`` reads a page that refused the plain
    request in a tab the user does not work in (item 98); ``None`` leaves such a page out."""
    started: float = time.monotonic()
    state: _Crawl = _Crawl(web, config, config.user_agent, renderer, quiet)
    first: str = normalized(start)
    seen: dict[str, None] = {first: None}
    frontier: list[str] = [first]
    pages: list[CrawledPage] = []
    skipped: list[SkippedPage] = []
    depth: int = 0
    while frontier and len(pages) < max_pages and depth <= max_depth:
        batch: list[str] = frontier[: max_pages - len(pages)]
        frontier = frontier[len(batch) :]
        fetchable: list[str] = []
        for url in batch:
            if config.crawl.robots == "obey" and not await state.allowed(url):
                skipped.append(SkippedPage(url=url, reason="robots.txt disallows it"))
            else:
                if config.crawl.robots == "report":
                    await state.allowed(url)
                fetchable.append(url)
        results: list[CrawledPage | SkippedPage] = list(
            await asyncio.gather(*(state.read(url, depth) for url in fetchable))
        )
        next_links: list[str] = []
        for result in results:
            if isinstance(result, SkippedPage):
                skipped.append(result)
            else:
                pages.append(result)
                next_links.extend(result.links)
        if not frontier:
            depth += 1
            frontier = list(plan_frontier(next_links, scope, tuple(seen)))
            seen.update(dict.fromkeys(frontier))
        else:
            # The level was cut by the page limit: what is left of it still counts as the frontier.
            fresh: tuple[str, ...] = plan_frontier(next_links, scope, tuple(seen))
            seen.update(dict.fromkeys(fresh))
            frontier.extend(fresh)
    return CrawlResult(
        start=first,
        scope=scope,
        pages=tuple(pages),
        skipped=tuple(skipped),
        robots=RobotsReport(
            hosts=tuple(host for host, rules in state.robots.items() if rules is not None),
            disallowed=tuple(dict.fromkeys(state.disallowed)),
        ),
        frontier_left=len(frontier),
        seconds=time.monotonic() - started,
    )
