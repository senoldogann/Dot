"""A crawl whose start address redirects (item 1298): the scope follows the start to the address the site answers
from, a page that a redirect carries out of the scope is left out, and the links outside the scope are named. The web
client is a stand-in that answers pages and redirects, so none of it reaches the network."""

from urllib.parse import urlsplit

import pytest

from dotagent.config import WebConfig, load_default_config
from dotagent.tools.web import render_index
from dotagent.web.crawl import CrawlResult, Scope, crawl, in_scope, rebased, same_site, scope_of
from dotagent.web.types import FetchedDocument

_HTML: str = "text/html; charset=utf-8"
_WORDS: str = "A page with words in it. " * 30


def _page(*links: str) -> bytes:
    anchors: str = " ".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<!doctype html><title>T</title><p>{_WORDS}</p>{anchors}".encode()


class _Web:
    """The web client of a crawl: ``pages`` by address, ``redirects`` from an address to where it ends."""

    def __init__(self, pages: dict[str, bytes], redirects: dict[str, str] | None = None) -> None:
        self.pages: dict[str, bytes] = pages
        self.redirects: dict[str, str] = redirects or {}
        self.asked: list[str] = []

    async def get(self, url: str, max_bytes: int) -> FetchedDocument:
        self.asked.append(url)
        final: str = self.redirects.get(url, url)
        body: bytes | None = self.pages.get(final)
        return FetchedDocument(
            url=final,
            status=200 if body is not None else 404,
            media_type="text/html",
            charset="utf-8",
            body=body if body is not None else b"not found",
            truncated=False,
            redirected_from=(url,) if final != url else (),
        )


def _config() -> WebConfig:
    web: WebConfig = load_default_config().web
    return web.model_copy(
        update={
            "crawl": web.crawl.model_copy(update={"per_host_spacing_s": 0.0, "render": "never", "robots": "report"})
        }
    )


async def _crawl(web: _Web, start: str, kind: str = "host", max_pages: int = 10, max_depth: int = 2) -> CrawlResult:
    return await crawl(
        web,  # type: ignore[arg-type]
        _config(),
        start,
        scope_of(kind, start),  # type: ignore[arg-type]
        max_pages,
        max_depth,
        None,
        None,
    )


def _paths(result: CrawlResult) -> list[str]:
    return [urlsplit(page.url).path for page in result.pages]


async def test_a_start_that_redirects_to_its_www_twin_is_crawled_from_there() -> None:
    """The case that gave one page: example.com answers from https://www.example.com/, and so do all its links."""
    web = _Web(
        {
            "https://www.example.com/": _page("https://www.example.com/a", "https://www.example.com/b"),
            "https://www.example.com/a": _page("https://www.example.com/c"),
            "https://www.example.com/b": _page(),
            "https://www.example.com/c": _page(),
        },
        {"https://example.com/": "https://www.example.com/"},
    )

    result = await _crawl(web, "https://example.com/")

    assert _paths(result) == ["/", "/a", "/b", "/c"]
    assert result.scope.url == "https://www.example.com/"
    assert any("the scope moved to https://www.example.com/" in note for note in result.notes), result.notes


async def test_a_start_that_moves_from_http_to_https_is_crawled_from_there() -> None:
    web = _Web(
        {"https://example.com/": _page("https://example.com/a"), "https://example.com/a": _page()},
        {"http://example.com/": "https://example.com/"},
    )

    result = await _crawl(web, "http://example.com/")

    assert _paths(result) == ["/", "/a"]


async def test_a_prefix_scope_keeps_its_folder_on_the_address_the_site_answers_from() -> None:
    web = _Web(
        {
            "https://www.example.com/docs/": _page("/docs/intro", "/blog/post"),
            "https://www.example.com/docs/intro": _page(),
            "https://www.example.com/blog/post": _page(),
        },
        {"https://example.com/docs/": "https://www.example.com/docs/"},
    )

    result = await _crawl(web, "https://example.com/docs/", kind="prefix")

    assert _paths(result) == ["/docs/", "/docs/intro"]
    assert result.scope.url == "https://www.example.com/docs/"
    assert ("www.example.com", 1) in result.dropped, result.dropped


async def test_links_to_the_other_form_of_the_site_are_followed_and_end_in_scope() -> None:
    web = _Web(
        {
            "https://www.example.com/": _page("https://example.com/a", "http://www.example.com/b"),
            "https://www.example.com/a": _page(),
            "https://www.example.com/b": _page(),
        },
        {
            "https://example.com/": "https://www.example.com/",
            "https://example.com/a": "https://www.example.com/a",
            "http://www.example.com/b": "https://www.example.com/b",
        },
    )

    result = await _crawl(web, "https://example.com/")

    assert sorted(_paths(result)) == ["/", "/a", "/b"]
    assert result.dropped == ()


async def test_a_start_that_redirects_to_another_site_keeps_its_page_and_follows_nothing() -> None:
    web = _Web(
        {"https://other.org/": _page("https://other.org/x", "https://example.com/y")},
        {"https://example.com/": "https://other.org/"},
    )

    result = await _crawl(web, "https://example.com/")

    assert [page.url for page in result.pages] == ["https://other.org/"]
    assert result.scope.url == "https://example.com/"
    assert any("another site" in note for note in result.notes), result.notes
    assert "https://other.org/x" not in web.asked


async def test_a_page_a_redirect_carries_out_of_the_scope_is_left_out_with_its_reason() -> None:
    web = _Web(
        {
            "https://example.com/": _page("/go", "/stay"),
            "https://example.com/stay": _page(),
            "https://elsewhere.net/landing": _page("/more"),
        },
        {"https://example.com/go": "https://elsewhere.net/landing"},
    )

    result = await _crawl(web, "https://example.com/")

    assert _paths(result) == ["/", "/stay"]
    assert [entry.url for entry in result.skipped] == ["https://elsewhere.net/landing"]
    assert "redirect" in result.skipped[0].reason
    assert "https://elsewhere.net/more" not in web.asked


async def test_an_ordinary_crawl_is_unchanged_and_names_the_sites_it_did_not_follow() -> None:
    web = _Web(
        {
            "https://example.com/": _page("/a", "https://twitter.com/x", "https://twitter.com/y", "https://b.org/"),
            "https://example.com/a": _page("https://twitter.com/z"),
        }
    )

    result = await _crawl(web, "https://example.com/")

    assert _paths(result) == ["/", "/a"]
    assert result.scope == scope_of("host", "https://example.com/")
    assert result.notes == ()
    assert dict(result.dropped) == {"twitter.com": 3, "b.org": 1}, result.dropped


def test_www_and_the_scheme_are_the_same_site_and_a_subdomain_or_another_host_is_not() -> None:
    assert same_site("http://example.com/", "https://www.example.com/x")
    assert same_site("https://www.example.com/", "https://example.com/")
    assert not same_site("https://example.com/", "https://blog.example.com/")
    assert not same_site("https://example.com/", "https://example.org/")
    assert not same_site("https://example.com/", "ftp://example.com/")
    assert rebased(scope_of("host", "https://example.com/"), "https://blog.example.com/") is None


def test_a_rebased_scope_holds_every_form_of_the_site_and_a_plain_one_only_its_own() -> None:
    plain: Scope = scope_of("host", "https://example.com/")
    moved: Scope | None = rebased(plain, "https://www.example.com/")

    assert moved is not None
    assert in_scope("http://example.com/a", moved) and in_scope("https://example.com/a", moved)
    assert in_scope("http://www.example.com/a", moved)
    assert not in_scope("https://blog.example.com/a", moved)
    assert not in_scope("https://www.example.com/a", plain)


def test_the_index_names_the_scope_that_moved_and_the_sites_left_alone() -> None:
    result = CrawlResult(
        start="https://example.com/",
        scope=scope_of("host", "https://www.example.com/"),
        pages=(),
        skipped=(),
        robots=CrawlResult.model_fields["robots"].annotation(hosts=(), disallowed=()),  # type: ignore[misc]
        frontier_left=0,
        seconds=0.1,
        notes=(
            "The start address redirected to https://www.example.com/; the scope moved to https://www.example.com/.",
        ),
        dropped=(("twitter.com", 3),),
    )

    index: str = render_index(result, (), "abc123")

    assert "the scope moved to https://www.example.com/" in index
    assert "Links outside the scope, not followed: twitter.com (3)" in index
    assert index.count('<web_content nonce="abc123"') == 1


@pytest.mark.parametrize("kind", ["host", "prefix"])
def test_a_page_that_moved_out_of_a_prefix_folder_takes_its_own_folder(kind: str) -> None:
    moved = rebased(scope_of(kind, "https://example.com/docs/a"), "https://www.example.com/documentation/a")  # type: ignore[arg-type]

    assert moved is not None
    assert moved.url == ("https://www.example.com/" if kind == "host" else "https://www.example.com/documentation/")
