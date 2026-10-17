"""The pages of the browser UI as the daemon read them when it started, and how they are served (item 309).

The daemon serves the pages it read at its start, not the files as they are on disk later. Its Python code is the code
it started with: pages changed under it (a merge, a batch of work ported in) would ask its API for fields and routes
that only a newer daemon has, and break on every screen until it starts again. Kept, the pages and the API stay one
version until the next start, and the digest a page compares (:func:`pages_build`) is that of the pages it is served.
Hidden files (``.DS_Store``, a tool's cache) are no pages: they are neither served nor counted.
"""

import hashlib
import mimetypes
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import formatdate, parsedate
from pathlib import Path

from starlette.datastructures import URL, Headers
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse, Response
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose

# How many hex digits of the digest of the pages (:func:`pages_build`) a page compares.
_BUILD_DIGITS: int = 16
_INDEX: str = "index.html"
# The headers a 304 answer keeps, as Starlette's StaticFiles keeps them.
_NOT_MODIFIED_HEADERS: frozenset[str] = frozenset(
    {"cache-control", "content-location", "date", "etag", "expires", "vary"}
)


@dataclass(frozen=True)
class KeptPage:
    """One file of the pages: its bytes, when it was last written as it was read, and the tag a browser checks it by."""

    body: bytes
    modified: float
    etag: str


@dataclass(frozen=True)
class KeptPages:
    """The pages as they were read: each file by its place under their folder (``js/main.js``), and their digest."""

    files: Mapping[str, KeptPage]
    build: str


def pages_build(files: Mapping[str, KeptPage]) -> str:
    """What the pages are: a digest of every file's place and bytes, which changes with any of them. The daemon tells it
    with its status, so that a page loaded from other pages (before the daemon started again) says so."""
    digest = hashlib.sha256()
    for place in sorted(files):
        digest.update(place.encode())
        digest.update(b"\0")
        digest.update(files[place].body)
        digest.update(b"\0")
    return digest.hexdigest()[:_BUILD_DIGITS]


def keep_pages(directory: Path) -> KeptPages:
    """The pages under ``directory`` as they are now, hidden files and those in hidden folders left out."""
    files: dict[str, KeptPage] = {}
    for path in sorted(directory.rglob("*")):
        place: Path = path.relative_to(directory)
        if path.is_file() and not any(part.startswith(".") for part in place.parts):
            body: bytes = path.read_bytes()
            files[place.as_posix()] = KeptPage(
                body=body, modified=path.stat().st_mtime, etag=hashlib.sha256(body).hexdigest()[:_BUILD_DIGITS]
            )
    return KeptPages(files=files, build=pages_build(files))


def _route_path(scope: Scope) -> str:
    """The path below where the pages are mounted, read as Starlette's StaticFiles reads it."""
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root) :] if path[len(root)] == "/" else path


def _unchanged(headers: Mapping[str, str], asked: Headers) -> bool:
    """Whether the browser holds the page as it is (``If-None-Match``, else ``If-Modified-Since``)."""
    tags: str | None = asked.get("if-none-match")
    if tags:
        return tags.strip() == "*" or headers["etag"] in [tag.strip().removeprefix("W/") for tag in tags.split(",")]
    since: str | None = asked.get("if-modified-since")
    if not since:
        return False
    asked_at = parsedate(since)
    written_at = parsedate(headers["last-modified"])
    return asked_at is not None and written_at is not None and asked_at >= written_at


def _page_response(page: KeptPage, place: str, scope: Scope) -> Response:
    headers: dict[str, str] = {"etag": f'"{page.etag}"', "last-modified": formatdate(page.modified, usegmt=True)}
    if _unchanged(headers, Headers(scope=scope)):
        return Response(
            status_code=304, headers={name: value for name, value in headers.items() if name in _NOT_MODIFIED_HEADERS}
        )
    media_type: str = mimetypes.guess_type(place)[0] or "text/plain"
    if scope["method"] == "HEAD":
        return Response(b"", headers={**headers, "content-length": str(len(page.body))}, media_type=media_type)
    return Response(page.body, headers=headers, media_type=media_type)


class ServedPages:
    """Kept pages (:class:`KeptPages`) served as Starlette's ``StaticFiles(html=True)`` serves a folder: GET and HEAD
    only, ``/`` and a folder's path answer with its ``index.html`` (a folder's path without the last ``/`` is sent on to
    it), a page the browser holds as it is answers 304, and a place that names no page 404."""

    def __init__(self, pages: KeptPages) -> None:
        self._pages: KeptPages = pages

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await WebSocketClose()(scope, receive, send)
            return
        response: Response = self._respond(scope)
        await response(scope, receive, send)

    def _respond(self, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=405)
        files: Mapping[str, KeptPage] = self._pages.files
        place: str = posixpath.normpath(_route_path(scope).lstrip("/"))
        if place in files:
            return _page_response(files[place], place, scope)
        index: str = _INDEX if place == "." else f"{place}/{_INDEX}"
        if index not in files:
            raise HTTPException(status_code=404)
        if not scope["path"].endswith("/"):
            url: URL = URL(scope=scope)
            return RedirectResponse(url=url.replace(path=f"{url.path}/"))
        return _page_response(files[index], index, scope)
