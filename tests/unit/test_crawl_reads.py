"""The crawl reads on (item 554): a page that cannot be read (a character set nobody knows, a PDF that will not open) is
left out with its reason and costs none of the pages read; a level the page limit cuts goes on at its own depth, and
what it linked to waits for it; a read that fails leaves none of the others running."""

import asyncio
from urllib.parse import urlsplit

import pytest

from dotagent.config import WebConfig, load_default_config
from dotagent.web.crawl import CrawlResult, crawl, scope_of
from dotagent.web.types import FetchedDocument

SITE: str = "https://docs.example.com"
_HTML: str = "text/html; charset=utf-8"
# Text enough that no page of these is taken for a shell that needs a browser.
_WORDS: str = "A page with words in it. " * 30


def _page(links: tuple[str, ...] = ()) -> bytes:
    anchors: str = " ".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<!doctype html><title>T</title><p>{_WORDS}</p>{anchors}".encode()


class _Web:
    """The web client of a crawl, over a few pages: the answer to each path, 404 for any other."""

    def __init__(self, pages: dict[str, tuple[int, str, bytes]]) -> None:
        self.pages: dict[str, tuple[int, str, bytes]] = pages
        self.asked: list[str] = []

    async def get(self, url: str, max_bytes: int) -> FetchedDocument:
        path: str = urlsplit(url).path
        self.asked.append(path)
        status, content_type, body = self.pages.get(path, (404, "text/html", b"not found"))
        media_type, _, parameters = content_type.partition(";")
        charset: str | None = parameters.split("=", 1)[1].strip() if "charset" in parameters else None
        return FetchedDocument(
            url=url,
            status=status,
            media_type=media_type,
            charset=charset,
            body=body,
            truncated=False,
            redirected_from=(),
        )


def _config() -> WebConfig:
    web: WebConfig = load_default_config().web
    return web.model_copy(
        update={
            "crawl": web.crawl.model_copy(update={"per_host_spacing_s": 0.0, "render": "never", "robots": "report"})
        }
    )


async def _crawl(web: _Web, max_pages: int, max_depth: int, renderer: object = None) -> CrawlResult:
    return await crawl(
        web,  # type: ignore[arg-type]
        _config(),
        f"{SITE}/",
        scope_of("host", f"{SITE}/"),
        max_pages,
        max_depth,
        renderer,  # type: ignore[arg-type]
        None,
    )


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_read_is_left_out_with_its_reason_and_the_others_are_kept() -> None:
    web: _Web = _Web(
        {
            "/": (200, _HTML, _page(("/good", "/bogus", "/locked", "/also-good"))),
            "/good": (200, _HTML, _page()),
            "/also-good": (200, _HTML, _page()),
            "/bogus": (200, "text/html; charset=x-bogus-charset", _page()),
            "/locked": (200, "application/pdf", b"%PDF-1.4 cut short"),
        }
    )
    result: CrawlResult = await _crawl(web, max_pages=10, max_depth=1)
    assert sorted(urlsplit(page.url).path for page in result.pages) == ["/", "/also-good", "/good"]
    skipped: dict[str, str] = {urlsplit(entry.url).path: entry.reason for entry in result.skipped}
    assert "character set that is not known" in skipped["/bogus"], skipped
    assert "PDF could not be opened" in skipped["/locked"], skipped


@pytest.mark.asyncio
async def test_a_level_the_page_limit_cuts_stays_at_its_depth_and_what_it_linked_to_waits() -> None:
    """The audit's case: four pages, one link deep; of the home page's six links the first has a page of its own and
    four are not there. The page that page links to is two links deep and is not read, nor named as one level deep."""
    web: _Web = _Web(
        {
            "/": (200, _HTML, _page(tuple(f"/a{number}" for number in range(1, 7)))),
            "/a1": (200, _HTML, _page(("/deep",))),
            "/a6": (200, _HTML, _page()),
            "/deep": (200, _HTML, _page()),
        }
    )
    result: CrawlResult = await _crawl(web, max_pages=4, max_depth=1)
    assert [(urlsplit(page.url).path, page.depth) for page in result.pages] == [("/", 0), ("/a1", 1), ("/a6", 1)]
    assert "/deep" not in web.asked
    # What was found and not read is counted, the page two links deep among it.
    assert result.frontier_left == 1


@pytest.mark.asyncio
async def test_the_levels_are_read_in_order_and_a_depth_beyond_the_limit_is_not_read() -> None:
    web: _Web = _Web(
        {
            "/": (200, _HTML, _page(("/one",))),
            "/one": (200, _HTML, _page(("/two",))),
            "/two": (200, _HTML, _page(("/three",))),
            "/three": (200, _HTML, _page()),
        }
    )
    result: CrawlResult = await _crawl(web, max_pages=20, max_depth=2)
    assert [(urlsplit(page.url).path, page.depth) for page in result.pages] == [("/", 0), ("/one", 1), ("/two", 2)]
    assert result.frontier_left == 1 and "/three" not in web.asked


@pytest.mark.asyncio
async def test_a_read_that_fails_leaves_no_other_read_running() -> None:
    """The reads of a level run side by side; when one fails for a reason of its own, the others are cancelled with it
    and none goes on after the crawl ended with the failure (a render in the run's browser tab would hold it)."""
    cancelled: list[str] = []
    rendering: asyncio.Event = asyncio.Event()

    async def renderer(url: str, max_chars: int) -> str:
        rendering.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(url)
            raise
        return "never reached"

    class _Failing(_Web):
        async def get(self, url: str, max_bytes: int) -> FetchedDocument:
            if urlsplit(url).path == "/breaks":
                await rendering.wait()
                raise RuntimeError("a read that fails for a reason of its own")
            return await super().get(url, max_bytes)

    shell: bytes = b"<html>" + b"<script></script>" * 5 + b"<p>short</p></html>"
    web: _Failing = _Failing({"/": (200, _HTML, _page(("/shell", "/breaks"))), "/shell": (200, _HTML, shell)})
    config: WebConfig = _config()
    config = config.model_copy(update={"crawl": config.crawl.model_copy(update={"render": "auto"})})
    start: str = f"{SITE}/"
    with pytest.raises(RuntimeError, match="for a reason of its own"):
        await crawl(web, config, start, scope_of("host", start), 10, 1, renderer, None)  # type: ignore[arg-type]
    assert cancelled == [f"{SITE}/shell"]
