"""Web tools: search the web and read a page, PDF or text file without starting the browser.

What a page or a search result says is written by third parties, so it only appears inside a ``<web_content>`` fence
with a fresh random nonce; the line above the fence is produced by Dot.
"""

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from dotagent.browser.safety import url_flags
from dotagent.browser.snapshot import fence_untrusted
from dotagent.config import AppConfig
from dotagent.core.errors import ProviderError, ToolInputError, WebError
from dotagent.core.types import JsonObject
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, Flag
from dotagent.secrets import require_secret
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
from dotagent.tools.browser import checked_url
from dotagent.tools.context import ToolContext
from dotagent.web.crawl import CrawledPage, CrawlResult, Renderer, Scope, ScopeKind, crawl, looks_like_shell, scope_of
from dotagent.web.extract import Extraction, FieldSpec, extract
from dotagent.web.pdf import pdf_to_text
from dotagent.web.search import search_duckduckgo, search_ollama
from dotagent.web.text import DocumentKind, ReadablePage, decode_body, document_kind, html_to_text
from dotagent.web.types import FetchedDocument, SearchHit

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
_WINDOW_SLACK: float = 0.8  # a window ends at a line or word break found in its last fifth
_UNREADABLE_STATUS: frozenset[int] = frozenset({401, 403, 429, 503})


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


def window(text: str, start: int, budget: int) -> tuple[str, int]:
    """The part of ``text`` that starts at ``start`` and fits ``budget`` characters, ended at a line or word break when
    one is close; returns the part and the offset where the next part starts."""
    limit: int = min(start + budget, len(text))
    if limit < len(text):
        floor: int = start + int(budget * _WINDOW_SLACK)
        for separator in ("\n", " "):
            found: int = text.rfind(separator, floor, limit)
            if found != -1:
                limit = found + 1
                break
    return text[start:limit], limit


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
    chunk, end = window(readable.text, start, budget)
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


def render_http_failure(shown_url: str, document: FetchedDocument, excerpt: str, nonce: str) -> str:
    lines: list[str] = [f"web_fetch got HTTP {document.status} from {shown_url}."]
    if document.status in _UNREADABLE_STATUS:
        lines.append("Some sites turn plain requests away; browser_open uses a real Chrome and may get through.")
    if excerpt:
        lines.append(fence_untrusted(document.url, excerpt, nonce))
    return "\n".join(lines)


class FetchArgs(ToolArgs):
    url: str = Field(
        description="Address of the page, PDF or text file. A bare host such as example.com gets https://."
    )
    start: int = Field(
        default=0, ge=0, description="Character offset to continue a long document; the end of a result says which."
    )


def _fetch_tool() -> RegisteredTool:
    def classify(args: FetchArgs, ctx: ToolContext) -> ActionInfo:
        url: str = checked_url(args.url, ctx.runtime.config.browser)
        return _action("web_fetch", f"Read {url}", url, url_flags(url))

    async def run(args: FetchArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        url: str = checked_url(args.url, config.browser)
        document: FetchedDocument = await ctx.runtime.web.get(
            url, max(config.web.fetch_max_bytes, config.web.pdf_max_bytes)
        )
        kind: DocumentKind = document_kind(document.media_type, document.body[:512])
        shown_url: str = _clip(url, _SHOWN_URL_CHARS)
        nonce: str = _nonce()
        if not 200 <= document.status < 300:
            excerpt: _Readable = (
                await _readable(document, kind, config.web.fetch_max_bytes)
                if kind in ("html", "text")
                else _Readable("", "", "")
            )
            return mark_untrusted(
                fail(render_http_failure(shown_url, document, excerpt.text[:_ERROR_EXCERPT_CHARS], nonce))
            )
        if kind == "binary":
            return fail(
                f"web_fetch cannot show {shown_url}: it is {document.media_type or 'of an unknown type'} "
                f"({len(document.body)} bytes), not a page, a PDF or a text file."
            )
        readable: _Readable = await _readable(document, kind, config.web.fetch_max_bytes)
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
        return mark_untrusted(ok(text))

    return make_tool(
        name="web_fetch",
        description=(
            "Download a page, PDF or text file and return it as clean text, with headings, lists, tables and link "
            "addresses kept. Fast and light: no browser, no JavaScript, no login. A long document comes in parts; pass "
            "start to continue. When the page needs JavaScript, a login or clicking, use browser_open instead."
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
    max_results: int = Field(default=8, ge=1, le=10, description="How many results to return.")


def _search_tool() -> RegisteredTool:
    def classify(args: SearchArgs, ctx: ToolContext) -> ActionInfo:
        flags: tuple[Flag, ...] = ("exfiltration",) if looks_like_key(args.query) else ()
        backend: str = ctx.runtime.search_backend
        return _action("web_search", f"Search the web for {args.query}", backend, flags)

    async def run(args: SearchArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        hits: tuple[SearchHit, ...]
        match ctx.runtime.search_backend:
            case "duckduckgo":
                hits = await search_duckduckgo(ctx.runtime.web, args.query, args.max_results)
            case "ollama":
                key: str = await asyncio.to_thread(
                    require_secret, config.providers.ollama.api_key_env, "web_search with the ollama search backend"
                )
                hits = await search_ollama(ctx.runtime.web, key, args.query, args.max_results)
        if not hits:
            return ok(f"No results for {_clip(args.query, _SHOWN_URL_CHARS)!r}. Try other words or a broader query.")
        header: str = f"{len(hits)} results for {_clip(args.query, _SHOWN_URL_CHARS)!r} ({ctx.runtime.search_backend}). Read the best ones with web_fetch."
        return mark_untrusted(ok("\n".join((header, fence_untrusted("web_search", render_hits(hits), _nonce())))))

    return make_tool(
        name="web_search",
        description=(
            "Search the web and get titles, addresses and short snippets. Use it to find pages or recent facts, then "
            "read the best results with web_fetch. Needs no browser."
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
            f"{number}. [{page.depth}] {page.url}  {page.title or '(no title)'}  {len(page.text)} chars, {how}{note}{file}"
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
        await ctx.runtime.browser.open_url(ctx.thread_id, url)
        return (await ctx.runtime.browser.read_text(ctx.thread_id, None, 0, max_chars)).text

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
        url: str = checked_url(args.url, ctx.runtime.config.browser)
        return _action(
            "web_crawl", f"Crawl {url} ({args.scope} scope, up to {args.max_pages} pages)", url, url_flags(url)
        )

    async def run(args: CrawlArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        url: str = checked_url(args.url, config.browser)
        scope: Scope = scope_of(args.scope, url)
        max_pages: int = min(args.max_pages, config.web.crawl.max_pages)
        max_depth: int = min(args.max_depth, config.web.crawl.max_depth)
        renderer: Renderer | None = None if config.web.crawl.render == "never" else _renderer_for(ctx)
        result: CrawlResult = await crawl(ctx.runtime.web, config.web, url, scope, max_pages, max_depth, renderer)
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


async def _source_text(source: str, ctx: ToolContext) -> str:
    """The text of a source: a page, PDF or text file fetched from the web, or a file of the workspace."""
    config: AppConfig = ctx.runtime.config
    if source.startswith(("http://", "https://")) or "/" not in source.split(".", 1)[0]:
        url: str = checked_url(source, config.browser)
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
        return await asyncio.to_thread(_read_local, source, ctx.workspace)
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolInputError(f"{source} cannot be read as a text file: {exc}", {"source": source}) from exc


def _read_local(source: str, workspace: Path) -> str:
    """The text of a file named by ``source``: an absolute path, ``~``, or a path under the workspace."""
    path: Path = Path(source).expanduser()
    return (path if path.is_absolute() else workspace / path).read_text(encoding="utf-8")


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
        return _action("web_extract", f"Extract {names} from {len(args.sources)} sources", args.sources[0], ())

    async def run(args: ExtractArgs, ctx: ToolContext) -> ToolResult:
        config: AppConfig = ctx.runtime.config
        fields: tuple[FieldSpec, ...] = tuple(
            FieldSpec(name=field.name, description=field.description) for field in args.fields
        )
        extractions: list[Extraction] = []
        problems: list[tuple[str, str]] = []
        for source in args.sources:
            try:
                text: str = await _source_text(source, ctx)
                extractions.append(
                    await extract(
                        ctx.runtime.router,
                        config.models.fast,
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

    return make_tool(
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
