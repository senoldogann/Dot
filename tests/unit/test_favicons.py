"""A chat's link icon is the site's own /favicon.ico, cached here. A host that is not a public name is refused, and
the address of the page the reader clicked is never part of the fetch."""

import pytest

from dotagent.server.favicons import FaviconCache, favicon_host, favicon_url, icon_media, read_icon
from dotagent.web.types import FetchedDocument

_PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_a_favicon_is_fetched_from_the_site_itself() -> None:
    assert favicon_url("GitHub.com") == "https://github.com/favicon.ico"
    assert favicon_url("www.github.com.") == "https://github.com/favicon.ico"
    assert "google" not in favicon_url("example.com")
    assert "duckduckgo" not in favicon_url("example.com")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "metadata.google.internal",
        "printer.local",
        "not a host",
        "",
    ],
)
def test_a_favicon_is_refused_for_a_host_that_is_not_a_public_name(host: str) -> None:
    with pytest.raises(ValueError):
        favicon_host(host)


def test_an_icon_is_a_small_image_and_a_page_is_not() -> None:
    assert icon_media(_PNG, "image/png") == "image/png"
    assert icon_media(_PNG, "application/octet-stream") == "image/png"
    assert icon_media(b"<html>no</html>", "text/html") is None
    assert icon_media(b"", "image/png") is None
    assert icon_media(b"\x00" * (64 * 1024 + 1), "image/png") is None


def test_a_miss_is_remembered_and_an_icon_is_kept() -> None:
    cache = FaviconCache()
    missed = FetchedDocument(
        url="https://example.com/favicon.ico",
        status=404,
        media_type="text/html",
        charset=None,
        body=b"<html></html>",
        truncated=False,
        redirected_from=(),
    )
    assert read_icon(cache, "example.com", missed, now=0.0) is None
    # A later ask does not need the document again: the miss is the cache's.
    assert read_icon(cache, "example.com", None, now=1.0) is None
    kept = FetchedDocument(
        url="https://github.com/favicon.ico",
        status=200,
        media_type="image/png",
        charset=None,
        body=_PNG,
        truncated=False,
        redirected_from=(),
    )
    assert read_icon(cache, "github.com", kept, now=0.0) == (_PNG, "image/png")
    assert read_icon(cache, "github.com", None, now=1.0) == (_PNG, "image/png")
