"""Web tools: search the web and read a page, PDF or text file without starting the browser.

What a page or a search result says is written by third parties, so it only appears inside a ``<web_content>`` fence
with a fresh random nonce; the line above the fence is produced by Dot.
"""

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from dotagent.agent.helpers import Unavailable, paused_model, unavailable_text
from dotagent.browser.safety import hides_secrets, url_flags
from dotagent.browser.session import RenderedDocument
from dotagent.browser.snapshot import fence_untrusted
from dotagent.config import AppConfig
from dotagent.core.errors import BrowserError, ProviderError, ToolExecutionError, ToolInputError, WebError
from dotagent.core.text import text_window
from dotagent.core.types import JsonObject
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, Flag
from dotagent.secrets import require_secret
from dotagent.tools.base import (
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_path_tool,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.browser import checked_url
from dotagent.tools.context import ToolContext
from dotagent.tools.files import read_flags, resolve_path
from dotagent.web.crawl import (
    CrawledPage,
    CrawlResult,
    Renderer,
    Scope,
    ScopeKind,
    crawl,
    looks_like_shell,
    page_links,
    scope_of,
)
from dotagent.web.extract import Extraction, FieldSpec, extract
from dotagent.web.ladder import SearchEngine, SearchOutcome
from dotagent.web.pdf import pdf_to_text
from dotagent.web.search import AnthropicSearch, ReadPage, engine_attempts, search, search_ladder
from dotagent.web.text import DocumentKind, ReadablePage, decode_body, document_kind, html_to_text
from dotagent.web.types import REFUSED_STATUS, FetchedDocument, SearchHit

_NONCE_BYTES: int = 8
_HEADROOM_CHARS: int = 1200  # room next to the text for the header, the fence lines and the hints
_SUMMARY_CHARS: int = 240
_SHOWN_URL_CHARS: int = 200
_ERROR_EXCERPT_CHARS: int = 1500
_FETCH_TIMEOUT_S: float = 90.0
_SEARCH_TIMEOUT_S: float = 90.0
_CRAWL_TIMEOUT_S: float = 900.0
_EXTRACT_TIMEOUT_S: float = 600.0
_CRAWL_FOLDER: str = "crawl"
_MAX_SOURCES: int = 20
_MAX_FIELDS: int = 20
# A plain request turned away: the page is read in the browser instead when the run may read quietly (items 59, 98).
_UNREADABLE_STATUS: frozenset[int] = REFUSED_STATUS
# What the result says when the browser was refused too (item 98): the next rung, in one line.
_NEXT_RUNG: str = (
    "The site refuses this machine. Next: another source for the same facts; web_search with the address as the "
    "query, for the hosted search's snippet of the page; for a login wall, browser_open and ask the owner to Take over."
)
# A page that is not there (item 56): the result says so and lists the front page's links that may be the one meant.
_NOT_THERE_STATUS: frozenset[int] = frozenset({404, 410})
_LIKELY_LINKS: int = 12
# The shortest word of an address that counts when the front page's links are matched against it.
_PATH_WORD_CHARS: int = 3
_PATH_WORDS: re.Pattern[str] = re.compile(r"[a-z0-9]+")
# The most results web_search returns; a larger number asked is lowered to it, and the result says so.
_MAX_RESULTS: int = 10


def _nonce() -> str:
    return secrets.token_hex(_NONCE_BYTES)


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _text_budget(output_chars: int) -> int:
    return max(output_chars - _HEADROOM_CHARS, 1000)


def _action(tool: str, summary: str, target: str, flags: tuple[Flag, ...]) -> ActionInfo:
    return ActionInfo(
        tool=tool, kind="network", risk="low", summary=_clip(summary, _SUMMARY_CHARS), target=target, flags=flags
    )


@dataclass(frozen=True)
class _Readable:
    title: str
    description: str
    text: str


def _read_html(document: FetchedDocument) -> ReadablePage:
    return html_to_text(decode_body(document.body, document.charset), document.url)


async def _readable(document: FetchedDocument, kind: DocumentKind, max_text_bytes: int) -> _Readable:
    """The document as text. Pages and text files are read up to ``max_text_bytes``; a PDF needs all of its bytes."""
    cut: FetchedDocument = (
        document.model_copy(update={"body": document.body[:max_text_bytes], "truncated": True})
        if kind != "pdf" and len(document.body) > max_text_bytes
        else document
    )
    match kind:
        case "html":
            page: ReadablePage = await asyncio.to_thread(_read_html, cut)
            return _Readable(title=page.title, description=page.description, text=page.text)
        case "pdf":
            return _Readable(title="", description="", text=await asyncio.to_thread(pdf_to_text, cut.body, cut.url))
        case "text":
            return _Readable(title="", description="", text=decode_body(cut.body, cut.charset))
        case "binary":
            return _Readable(title="", description="", text="")


def _cut_at(document: FetchedDocument, kind: DocumentKind, max_text_bytes: int) -> int | None:
    """How many bytes of the document the text was made from when the rest was left out, else ``None``."""
    if kind == "pdf":
        return len(document.body) if document.truncated else None
    return (
        min(len(document.body), max_text_bytes) if document.truncated or len(document.body) > max_text_bytes else None
    )


def _needs_javascript(document: FetchedDocument, kind: DocumentKind, readable: _Readable) -> bool:
    return kind == "html" and looks_like_shell(document.body, len(readable.text))


def render_document(
    shown_url: str,
    document: FetchedDocument,
    readable: _Readable,
    cut_at_bytes: int | None,
    start: int,
    budget: int,
    nonce: str,
) -> str:
    """A fetched document as the model reads it: a header line from Dot, the text in a fence, then what to do next."""
    chunk, end = text_window(readable.text, start, budget)
    total: int = len(readable.text)
    header: str = (
        f"Fetched {shown_url}: HTTP {document.status}, {document.media_type or 'unknown type'}, {total} characters"
    )
    if document.redirected_from:
        header += f", after {len(document.redirected_from)} redirect(s)"
    inner: list[str] = []
    if start == 0:
        inner.extend(
            f"{label}: {value}"
            for label, value in (("Title", readable.title), ("Description", readable.description))
            if value
        )
        if inner:
            inner.append("")
    inner.append(chunk if chunk else "(no readable text)")
    parts: list[str] = [
        f"{header}; showing characters {start}-{end}.",
        fence_untrusted(document.url, "\n".join(inner), nonce),
    ]
    if end < total:
        parts.append(f"More: call web_fetch again with start={end} to continue this document.")
    if cut_at_bytes is not None:
        parts.append(
            f"The download was cut at {cut_at_bytes} bytes (web size limits); the rest cannot be read this way."
        )
    return "\n".join(parts)


def path_words(url: str) -> frozenset[str]:
    """The words of an address's path and query (lower case, letters and digits, three characters or more): what a
    guessed address says about the page meant."""
    parts: tuple[str, ...] = urlsplit(url)[2:4]
    return frozenset(word for word in _PATH_WORDS.findall(" ".join(parts).lower()) if len(word) >= _PATH_WORD_CHARS)


def front_page(url: str) -> str:
    """The front page of the host of ``url``."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def likely_links(links: tuple[str, ...], asked: str, limit: int) -> tuple[str, ...]:
    """Of the front page's ``links``, the ones on the same host that share a word with the address asked for, the
    address itself left out, at most ``limit`` in page order: the pages a guessed address may have meant."""
    wanted: frozenset[str] = path_words(asked)
    host: str = urlsplit(asked).netloc.lower()
    found: list[str] = []
    for link in links:
        if link == asked or urlsplit(link).netloc.lower() != host or not wanted & path_words(link):
            continue
        found.append(link)
        if len(found) == limit:
            break
    return tuple(found)


def render_http_failure(
    shown_url: str,
    document: FetchedDocument,
    excerpt: str,
    nonce: str,
    likely: tuple[str, ...],
    front_problem: str,
    browser_problem: str,
) -> str:
    """The result of a fetch the web answered with an error status. A page that is not there says so and lists
    ``likely``, the front page's links that may be the page meant (``front_problem`` says why the front page could
    not be read, when it could not). ``browser_problem`` says why the run's browser could not read a refused page
    instead, when it was asked and could not (item 98)."""
    lines: list[str] = [f"web_fetch got HTTP {document.status} from {shown_url}."]
    if browser_problem:
        lines.append(browser_problem)
    if document.status in _UNREADABLE_STATUS:
        lines.append(f"Some sites turn plain requests away. {_NEXT_RUNG}")
    if document.status in _NOT_THERE_STATUS:
        lines.append(
            "The page is not there. Do not try other addresses on a hunch: find the page with web_search (site:host "
            "words) or web_crawl of the host, or pick one of the links below."
        )
        if likely:
            lines.append("Links on the host's front page that share a word with the address asked for:")
            lines.extend(f"- {link}" for link in likely)
        elif front_problem:
            lines.append(f"The host's front page could not be read either: {front_problem}")
        else:
            lines.append("The host's front page links to nothing that shares a word with the address asked for.")
    if excerpt:
        lines.append(fence_untrusted(document.url, excerpt, nonce))
    return "\n".join(lines)


async def _front_page_links(ctx: ToolContext, url: str, max_bytes: int) -> tuple[tuple[str, ...], str]:
    """The front page's links that may be the page ``url`` meant, and the problem when the front page could not be
    read (an error status, or a web error, which is reported, not hidden)."""
    root: str = front_page(url)
    if root == url:
        return (), ""
    try:
        document: FetchedDocument = await ctx.runtime.web.get(root, max_bytes)
    except WebError as error:
        return (), error.message
    if not 200 <= document.status < 300:
        return (), f"it answered HTTP {document.status}"
    if document_kind(document.media_type, document.body[:512]) != "html":
        return (), "it is not an HTML page"
    links: tuple[str, ...] = await asyncio.to_thread(
        page_links, decode_body(document.body[:max_bytes], document.charset), document.url
    )
    return likely_links(links, url, _LIKELY_LINKS), ""


class FetchArgs(ToolArgs):
    url: str = Field(
        description="Address of the page, PDF or text file. A bare host such as example.com gets https://."
    )
    start: int = Field(
        default=0, ge=0, description="Character offset to continue a long document; the end of a result says which."
    )


def browser_document(rendered: RenderedDocument) -> FetchedDocument:
    """A page read in the browser, in the shape of a plain fetch, so that it is read and rendered the same way."""
    return FetchedDocument(
        url=rendered.url,
        status=rendered.status,
        media_type="text/html",
        charset="utf-8",
        body=rendered.html.encode("utf-8"),
        truncated=False,
        redirected_from=(),
    )


def render_browser_failure(shown_url: str, why: str, status: int, excerpt: str, nonce: str) -> str:
    """The result when Dot's browser got an error status too: both answers, then what the page said."""
    lines: list[str] = [f"web_fetch got HTTP {status} from {shown_url} in Dot's browser too ({why}).", _NEXT_RUNG]
    if excerpt:
        lines.append(fence_untrusted(shown_url, excerpt, nonce))
    return "\n".join(lines)


async def _fetch_in_browser(ctx: ToolContext, args: FetchArgs, url: str, shown_url: str, why: str) -> ToolResult:
    """Read ``url`` in the run's browser because the plain request did not do (``why`` says what it got), and render
    the page the way a plain fetch is rendered, with a first line that says how it was read. A browser that cannot
    open the page raises :class:`BrowserError`; the caller falls back to the plain answer and says so."""
    config: AppConfig = ctx.runtime.config
    nonce: str = _nonce()
    rendered: RenderedDocument = await ctx.browser.read_page(url)
    document: FetchedDocument = browser_document(rendered)
    readable: _Readable = await _readable(document, "html", config.web.fetch_max_bytes)
    if not 200 <= document.status < 300:
        return mark_untrusted(
            fail(render_browser_failure(shown_url, why, document.status, readable.text[:_ERROR_EXCERPT_CHARS], nonce))
        )
    if args.start > len(readable.text):
        return fail(f"start={args.start} is past the end of the document, which has {len(readable.text)} characters.")
    cut_at: int | None = _cut_at(document, "html", config.web.fetch_max_bytes)
    body: str = render_document(
        shown_url, document, readable, cut_at, args.start, _text_budget(ctx.output_chars), nonce
    )
    return mark_untrusted(ok(f"Read in Dot's browser: {why}.\n{body}"))


def _browser_problem(error: BrowserError) -> str:
    return f"Dot's browser could not read it either: {error.message}."


def _fetch_tool() -> RegisteredTool:
    def classify(args: FetchArgs, ctx: ToolContext) -> ActionInfo:
        url: str = checked_url(args.url, ctx.runtime.config)
        return _action("web_fetch", f"Read {url}", url, url_flags(url))

    async def run(args: FetchArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        url: str = checked_url(args.url, config)
        shown_url: str = _clip(url, _SHOWN_URL_CHARS)
        # Why the run's browser could not read the page when it was asked to (item 98); empty until it was.
        browser_problem: str = ""
        # A host that refused this run's plain request once is not asked plainly again (item 98).
        if ctx.refused_hosts.remembers(url) and ctx.browser.quiet_reads:
            try:
                return await _fetch_in_browser(
                    ctx, args, url, shown_url, "the host turned an earlier plain request away"
                )
            except BrowserError as error:
                browser_problem = _browser_problem(error)
        document: FetchedDocument = await ctx.runtime.web.get(
            url, max(config.web.fetch_max_bytes, config.web.pdf_max_bytes)
        )
        kind: DocumentKind = document_kind(document.media_type, document.body[:512])
        nonce: str = _nonce()
        if not 200 <= document.status < 300:
            excerpt: _Readable = (
                await _readable(document, kind, config.web.fetch_max_bytes)
                if kind in ("html", "text")
                else _Readable("", "", "")
            )
            if document.status in _UNREADABLE_STATUS:
                ctx.refused_hosts.note(url)
            if document.status in _UNREADABLE_STATUS and ctx.browser.quiet_reads and not browser_problem:
                try:
                    return await _fetch_in_browser(
                        ctx, args, url, shown_url, f"the plain request got HTTP {document.status}"
                    )
                except BrowserError as error:
                    browser_problem = _browser_problem(error)
            likely, front_problem = (
                await _front_page_links(ctx, url, config.web.fetch_max_bytes)
                if document.status in _NOT_THERE_STATUS
                else ((), "")
            )
            return mark_untrusted(
                fail(
                    render_http_failure(
                        shown_url,
                        document,
                        excerpt.text[:_ERROR_EXCERPT_CHARS],
                        nonce,
                        likely,
                        front_problem,
                        browser_problem,
                    )
                )
            )
        if kind == "binary":
            return fail(
                f"web_fetch cannot show {shown_url}: it is {document.media_type or 'of an unknown type'} "
                f"({len(document.body)} bytes), not a page, a PDF or a text file."
            )
        readable: _Readable = await _readable(document, kind, config.web.fetch_max_bytes)
        if _needs_javascript(document, kind, readable) and ctx.browser.quiet_reads and not browser_problem:
            try:
                return await _fetch_in_browser(
                    ctx, args, url, shown_url, "the page shows little text without JavaScript"
                )
            except BrowserError as error:
                browser_problem = _browser_problem(error)
        if args.start > len(readable.text):
            return fail(
                f"start={args.start} is past the end of the document, which has {len(readable.text)} characters."
            )
        cut_at: int | None = _cut_at(document, kind, config.web.fetch_max_bytes)
        text: str = render_document(
            shown_url, document, readable, cut_at, args.start, _text_budget(ctx.output_chars), nonce
        )
        if _needs_javascript(document, kind, readable):
            text += "\nNote: this page shows little text without JavaScript; browser_open renders it in a real Chrome."
            if browser_problem:
                text += f" {browser_problem}"
        return mark_untrusted(ok(text))

    return make_tool(
        name="web_fetch",
        description=(
            "Download a page, PDF or text file and return it as clean text, with headings, lists, tables and link "
            "addresses kept. Fast and light: no browser, no JavaScript, no login. A long document comes in parts; pass "
            "start to continue. A page that turns the plain request away or needs JavaScript is read in a tab of the "
            "run's Chrome that you never see, and the result says so. For a login or clicking, use browser_open."
        ),
        args_model=FetchArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_FETCH_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def render_hits(hits: tuple[SearchHit, ...]) -> str:
    blocks: list[str] = []
    for position, hit in enumerate(hits, start=1):
        lines: list[str] = [f"{position}. {hit.title}", f"   {hit.url}"]
        if hit.snippet:
            lines.append(f"   {hit.snippet}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class SearchArgs(ToolArgs):
    query: str = Field(
        min_length=2,
        max_length=400,
        description="What to look for, worded the way you would type it into a search engine.",
    )
    max_results: int = Field(
        default=8, ge=1, description="How many results to return, 1 to 10; a larger number is lowered to 10."
    )


def wanted_results(asked: int) -> tuple[int, str]:
    """How many results a search returns for the number asked, and the note the header carries when it was lowered."""
    if asked <= _MAX_RESULTS:
        return asked, ""
    return _MAX_RESULTS, f" (max_results {asked} lowered to {_MAX_RESULTS}, the most a search returns)"


def skipped_note(outcome: SearchOutcome) -> str:
    """What the header says about the rungs the search went past, and about an answer that came from memory."""
    parts: list[str] = [f"{entry.engine} skipped: {entry.reason}" for entry in outcome.skipped]
    if outcome.age_s > 0:
        parts.append(f"the same query was answered {max(1, round(outcome.age_s / 60))} min ago; this is that answer")
    return f" ({'; '.join(parts)})" if parts else ""


def _page_reader(ctx: ToolContext) -> ReadPage | None:
    """A reader of pages in Dot's own visible Chrome for the browser rung, or ``None`` when the run's browser is the
    user's or headless (a headless Chrome meets the engine's CAPTCHA)."""
    if not ctx.browser.human_like:
        return None

    async def read(url: str) -> str:
        try:
            return (await ctx.browser.read_page(url)).html
        except BrowserError as error:
            raise WebError(f"Dot's browser could not open the search: {error.message}", dict(error.context)) from error

    return read


def _search_tool() -> RegisteredTool:
    def rungs(ctx: ToolContext) -> tuple[SearchEngine, ...]:
        config: AppConfig = ctx.runtime.config
        return search_ladder(
            config.web.search_backend,
            ctx.runtime.search_key_present,
            config.web.anthropic_search if ctx.runtime.anthropic_key_present else None,
            ctx.browser.human_like,
            bool(config.web.searxng_url),
        )

    async def anthropic_relay(config: AppConfig) -> AnthropicSearch:
        """The key and the model of the Anthropic rung; a [models] search that is not an Anthropic model is refused."""
        ref: ModelRef = parse_model_ref(config.models.search)
        if ref.provider != "anthropic":
            raise ToolExecutionError(
                "[models] search must name an Anthropic model for the anthropic search engine",
                {"search": config.models.search},
            )
        key: str = await asyncio.to_thread(
            require_secret, config.providers.anthropic.api_key_env, "web_search with the anthropic search engine"
        )
        return AnthropicSearch(api_key=key, model=ref.model)

    def classify(args: SearchArgs, ctx: ToolContext) -> ActionInfo:
        flags: tuple[Flag, ...] = ("exfiltration",) if looks_like_key(args.query) else ()
        return _action("web_search", f"Search the web for {args.query}", "/".join(rungs(ctx)), flags)

    async def run(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        wanted, lowered = wanted_results(args.max_results)
        ladder: tuple[SearchEngine, ...] = rungs(ctx)
        key: str | None = (
            await asyncio.to_thread(
                require_secret, config.providers.ollama.api_key_env, "web_search with the ollama search engine"
            )
            if "ollama" in ladder
            else None
        )
        relay: AnthropicSearch | None = await anthropic_relay(config) if "anthropic" in ladder else None
        outcome: SearchOutcome = await search(
            ctx.runtime.web,
            ladder,
            engine_attempts(ctx.runtime.web, key, relay, _page_reader(ctx), config.web.searxng_url or None),
            args.query,
            wanted,
        )
        if not outcome.hits:
            return ok(
                f"No results for {_clip(args.query, _SHOWN_URL_CHARS)!r} from {outcome.engine}{skipped_note(outcome)}. "
                "Try other words or a broader query."
            )
        header: str = (
            f"{len(outcome.hits)} results for {_clip(args.query, _SHOWN_URL_CHARS)!r} from {outcome.engine}{lowered}"
            f"{skipped_note(outcome)}. Read the best ones with web_fetch."
        )
        return mark_untrusted(
            ok("\n".join((header, fence_untrusted("web_search", render_hits(outcome.hits), _nonce()))))
        )

    return make_tool(
        name="web_search",
        description=(
            "Search the web and get titles, addresses and short snippets. Use it to find pages or recent facts, then "
            "read the best results with web_fetch. Several engines stand behind it, tried in turn, so a search that "
            "fails outright is rare; the result names the engine that answered."
        ),
        args_model=SearchArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_SEARCH_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def page_markdown(page: CrawledPage) -> str:
    """One crawled page as a document: a heading with the title, the address and the description, then the text."""
    lines: list[str] = [f"# {page.title or page.url}", f"Source: {page.url}"]
    if page.description:
        lines.append(f"Description: {page.description}")
    if page.rendered:
        lines.append("Read from the browser after JavaScript ran.")
    if page.problem:
        lines.append(f"Note: {page.problem}")
    if page.truncated:
        lines.append("The text was cut at the size limit.")
    lines.extend(("", page.text if page.text else "(no readable text)"))
    return "\n".join(lines)


def render_index(result: CrawlResult, files: tuple[Path, ...]) -> str:
    """The index of a crawl: one line per page (its number, depth, size, how it was read and, when the pages were
    written to files, its file), the addresses left out, what robots.txt said and whether a limit cut the crawl."""
    lines: list[str] = [
        f"Crawled {len(result.pages)} pages from {result.start} ({result.scope.kind} scope, {result.scope.url}) in "
        f"{result.seconds:.1f} s."
    ]
    for number, page in enumerate(result.pages, start=1):
        how: str = "rendered" if page.rendered else page.kind
        note: str = f"; {page.problem}" if page.problem else ""
        file: str = f"  -> {files[number - 1]}" if files else ""
        lines.append(
            f"{number}. [{page.depth}] {page.url}  {page.title or '(no title)'}  {len(page.text)} chars, "
            f"{how}{note}{file}"
        )
    if result.skipped:
        lines.append(f"Left out ({len(result.skipped)}):")
        lines.extend(f"- {entry.url}: {entry.reason}" for entry in result.skipped)
    if result.robots.disallowed:
        lines.append(
            f"robots.txt disallows {len(result.robots.disallowed)} of these addresses: "
            + ", ".join(result.robots.disallowed[:10])
            + (" …" if len(result.robots.disallowed) > 10 else "")
        )
    elif result.robots.hosts:
        lines.append("robots.txt was read and disallows none of these addresses.")
    else:
        lines.append("No robots.txt was readable.")
    if result.frontier_left:
        lines.append(f"{result.frontier_left} more addresses were found within the scope but not read (limits).")
    return "\n".join(lines)


def _crawl_files(root: Path, result: CrawlResult) -> tuple[Path, ...]:
    """Write every page of the crawl as a Markdown file under ``root`` and the index next to them."""
    root.mkdir(parents=True, exist_ok=False)
    files: list[Path] = []
    for number, page in enumerate(result.pages, start=1):
        file: Path = root / f"{number:03d}.md"
        file.write_text(page_markdown(page), encoding="utf-8")
        files.append(file)
    written: tuple[Path, ...] = tuple(files)
    (root / "index.md").write_text(render_index(result, written) + "\n", encoding="utf-8")
    return written


def _renderer_for(ctx: ToolContext) -> Renderer:
    """A page shown in the run's browser tab: its visible text after JavaScript ran."""

    async def render(url: str, max_chars: int) -> str:
        await ctx.browser.open_url(ctx.thread_id, url, hides_secrets(ctx.approvals))
        return (await ctx.browser.read_text(ctx.thread_id, None, 0, max_chars)).text

    return render


def _quiet_renderer_for(ctx: ToolContext) -> Renderer | None:
    """A page read in a tab the user does not work in (item 98), as text; ``None`` when the run's browser cannot."""
    if not ctx.browser.quiet_reads:
        return None

    async def render(url: str, max_chars: int) -> str:
        rendered: RenderedDocument = await ctx.browser.read_page(url)
        if not 200 <= rendered.status < 300:
            raise BrowserError(f"the browser got HTTP {rendered.status} too", {"url": url})
        page: ReadablePage = await asyncio.to_thread(html_to_text, rendered.html, rendered.url)
        return page.text[:max_chars]

    return render


class CrawlArgs(ToolArgs):
    url: str = Field(description="Where the crawl starts.")
    scope: ScopeKind = Field(
        default="prefix", description="prefix: the pages under the start address's folder; host: its whole host."
    )
    max_pages: int = Field(default=20, ge=1, le=500, description="The most pages to read.")
    max_depth: int = Field(default=2, ge=0, le=10, description="How many links deep to follow.")


def _crawl_tool() -> RegisteredTool:
    def classify(args: CrawlArgs, ctx: ToolContext) -> ActionInfo:
        url: str = checked_url(args.url, ctx.runtime.config)
        return _action(
            "web_crawl", f"Crawl {url} ({args.scope} scope, up to {args.max_pages} pages)", url, url_flags(url)
        )

    async def run(args: CrawlArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        url: str = checked_url(args.url, config)
        scope: Scope = scope_of(args.scope, url)
        max_pages: int = min(args.max_pages, config.web.crawl.max_pages)
        max_depth: int = min(args.max_depth, config.web.crawl.max_depth)
        renderer: Renderer | None = None if config.web.crawl.render == "never" else _renderer_for(ctx)
        result: CrawlResult = await crawl(
            ctx.runtime.web, config.web, url, scope, max_pages, max_depth, renderer, _quiet_renderer_for(ctx)
        )
        if not result.pages:
            return fail(render_index(result, ()))
        nonce: str = _nonce()
        inline: str = "\n\n".join(fence_untrusted(page.url, page_markdown(page), nonce) for page in result.pages)
        budget: int = _text_budget(ctx.output_chars)
        if len(inline) <= budget:
            return mark_untrusted(ok(f"{render_index(result, ())}\n\n{inline}"))
        host: str = scope.url.split("//", 1)[-1].split("/", 1)[0].replace(":", "-")
        root: Path = ctx.workspace / _CRAWL_FOLDER / f"{host}-{time.strftime('%Y%m%d-%H%M%S')}-{nonce[:6]}"
        files: tuple[Path, ...] = await asyncio.to_thread(_crawl_files, root, result)
        return mark_untrusted(
            ok(
                f"{render_index(result, files)}\n\nThe pages are {len(inline)} characters in all, more than one result "
                f"holds, so each is a Markdown file under {root} (index.md lists them). Read the ones you need with "
                "read_file, or pass their paths to web_extract."
            )
        )

    return make_tool(
        name="web_crawl",
        description=(
            "Read many pages of one site in one call, following the links within the scope, and get one clean text "
            "document per page (a page that needs JavaScript is rendered; PDFs are read; pages that do not fit one "
            "result become Markdown files in the workspace). Use it instead of many web_fetch calls."
        ),
        args_model=CrawlArgs,
        modes=ALL_MODES,
        # The crawl may render pages in the run's browser tab, which no other call may use meanwhile.
        parallel_safe=False,
        timeout_s=_CRAWL_TIMEOUT_S,
        classify=classify,
        run=run,
    )


class FieldArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$", description="The key.")
    description: str = Field(min_length=1, max_length=300, description="What the field means.")


class ExtractArgs(ToolArgs):
    sources: list[str] = Field(
        min_length=1, max_length=_MAX_SOURCES, description="Web addresses, or files in the workspace (a crawl's)."
    )
    fields: list[FieldArgs] = Field(min_length=1, max_length=_MAX_FIELDS, description="The fields to extract.")


def _local_file(source: str, ctx: ToolContext) -> Path | None:
    """The file a source names, resolved as the file tools resolve a path, or ``None`` for a web address. An address
    with a scheme is one. A file of the workspace by that name is read, even one whose name looks like a host
    (``notes.md``); otherwise a name whose part before its first dot has no slash is a host (``example.com/page``), and
    any other names a file (``crawl/001.md``, ``~/notes.txt``). Blocking."""
    if source.startswith(("http://", "https://")):
        return None
    path: Path = resolve_path(source, ctx)
    return path if "/" in source.split(".", 1)[0] or path.is_file() else None


def _source_flags(sources: list[str], ctx: ToolContext) -> tuple[Flag, ...]:
    """The flags of reading the files among ``sources``. Their text goes to the fast model, so a file in a protected
    location is a secret's, as it is for read_file."""
    files: list[Path] = [path for path in (_local_file(source, ctx) for source in sources) if path is not None]
    return tuple(sorted({flag for path in files for flag in read_flags(path, ctx)}))


async def _source_text(source: str, ctx: ToolContext) -> str:
    """The text of a source: a page, PDF or text file fetched from the web, or a file (:func:`_local_file`)."""
    config: AppConfig = ctx.runtime.config
    local: Path | None = await asyncio.to_thread(_local_file, source, ctx)
    if local is None:
        url: str = checked_url(source, config)
        document: FetchedDocument = await ctx.runtime.web.get(
            url, max(config.web.fetch_max_bytes, config.web.pdf_max_bytes)
        )
        if not 200 <= document.status < 300:
            raise WebError(f"HTTP {document.status}", {"url": _clip(url, _SHOWN_URL_CHARS)})
        kind: DocumentKind = document_kind(document.media_type, document.body[:512])
        if kind == "binary":
            raise WebError("not a page, a PDF or a text file", {"url": _clip(url, _SHOWN_URL_CHARS)})
        return (await _readable(document, kind, config.web.fetch_max_bytes)).text
    try:
        return await asyncio.to_thread(local.read_text, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolInputError(f"{source} cannot be read as a text file: {exc}", {"source": source}) from exc


def render_extractions(extractions: tuple[Extraction, ...], problems: tuple[tuple[str, str], ...]) -> str:
    """The extractions as JSON, one object per source with every field's value, quote and whether the quote is in
    the source, then the sources that could not be read or extracted."""
    data: list[JsonObject] = [
        {
            "source": extraction.source,
            "fields": {
                name: {"value": field.value, "quote": field.quote, "verified": field.verified}
                for name, field in extraction.fields.items()
            },
        }
        for extraction in extractions
    ]
    unverified: int = sum(
        1 for extraction in extractions for field in extraction.fields.values() if field.value and not field.verified
    )
    lines: list[str] = [
        f"Extracted {len(extractions)} of {len(extractions) + len(problems)} sources; "
        f"{unverified} values whose quote is not in the source (verified: false): treat those as claims.",
        json.dumps(data, ensure_ascii=False, indent=1),
    ]
    if problems:
        lines.append("Not extracted:")
        lines.extend(f"- {source}: {problem}" for source, problem in problems)
    return "\n".join(lines)


def _extract_tool() -> RegisteredTool:
    def classify(args: ExtractArgs, ctx: ToolContext) -> ActionInfo:
        names: str = ", ".join(field.name for field in args.fields)
        return _action(
            "web_extract",
            f"Extract {names} from {len(args.sources)} sources",
            args.sources[0],
            _source_flags(args.sources, ctx),
        )

    async def run(args: ExtractArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        fields: tuple[FieldSpec, ...] = tuple(
            FieldSpec(name=field.name, description=field.description) for field in args.fields
        )
        fast: str = ctx.runtime.roles.fast()
        extractions: list[Extraction] = []
        problems: list[tuple[str, str]] = []
        for source in args.sources:
            # A fast model the router has paused is not asked, nor the source read, until the pause ends (item 240).
            now: float = time.time()
            paused: Unavailable | None = paused_model(ctx.runtime.router, parse_model_ref(fast).label, now)
            if paused is not None:
                problems.append(
                    (source, f"{unavailable_text(paused, now)} cannot answer now; read it with web_fetch or read_file")
                )
                continue
            try:
                text: str = await _source_text(source, ctx)
                extractions.append(
                    await extract(
                        ctx.runtime.router,
                        fast,
                        config.agent.model_call_timeout_s,
                        f"{ctx.thread_id}:extract",
                        ctx.run_id,
                        source,
                        text,
                        fields,
                    )
                )
            except (WebError, ToolInputError, ProviderError) as error:
                problems.append((source, error.message))
        text_out: str = render_extractions(tuple(extractions), tuple(problems))
        result: ToolResult = ok(text_out) if extractions else fail(text_out)
        return mark_untrusted(result)

    return make_path_tool(
        name="web_extract",
        description=(
            "Pull named fields out of pages or files as JSON on the fast model, so the pages never enter your "
            "context: per source, each field's value and the words it comes from, checked against the source."
        ),
        args_model=ExtractArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_EXTRACT_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_web_tools() -> tuple[RegisteredTool, ...]:
    return (_search_tool(), _fetch_tool(), _crawl_tool(), _extract_tool())
