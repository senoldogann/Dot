"""A page Dot made in its workspace, shown whole (item 600): the files beside it that it names, its style sheets, its
scripts and the pictures, sounds, videos and fonts they use, are written into it, since a shown page is sandboxed and
loads nothing of this Mac (``PAGE_HEADERS``). A file is written in when it is one ``read_file`` reads without a
question: in the workspace once its links are followed, outside the protected places and Dot's own files, and not one a
crawl or an MCP answer kept there (item 494); and when it fits, the page with its files being one page
(``PAGE_MAX_CHARS``): one that does not is left out, and the page is shown without it. What a script loads by itself (a
module's imports, a fetch) is not."""

import base64
import fcntl
import math
import mimetypes
import os
import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotagent.agent.pages import PAGE_MAX_CHARS
from dotagent.core.errors import PolicyDenied
from dotagent.policy.paths import classify_path, expand_path
from dotagent.tools.third_party import holds_third_party

# The attributes that name a file a tag shows or plays, written in as a data: address.
_SHOWN: dict[str, tuple[str, ...]] = {
    "img": ("src",),
    "source": ("src",),
    "audio": ("src",),
    "video": ("src", "poster"),
    "track": ("src",),
    "input": ("src",),
    "image": ("href", "xlink:href"),
}
# A picture or a font a style uses, or a sheet it imports, which is not written in (a shown page's styles load from no
# data: address) and is named as left out.
# Every run is bounded and a name holds no parenthesis, so that a sheet of unclosed "url(" is read in linear time: the
# regular expression holds the interpreter while it runs (the independent review).
_CSS_URL: re.Pattern[str] = re.compile(
    r"""(?P<imported>@import\s{1,64}(?:url\(\s{0,64})?['"]?(?P<sheet>[^'"()\s;]{1,2048})[^;{}\n]{0,2048};?)"""
    r"""|url\(\s{0,64}(?P<quote>['"]?)(?P<url>[^'"()\s]{1,2048})(?P=quote)\s{0,64}\)"""
)
# The longest path the kernel names a file by (MAXPATHLEN), which F_GETPATH writes.
_PATH_BYTES: int = 1024
# What the attributes of a tag written again and the url() around a data: address may add to a page beyond what its
# files take as written (counted, escapes and all), kept free so that the page stays one page.
_MARGIN: int = 2000


@dataclass
class PageFiles:
    """A page with the files beside it that it names written in: the names written in, those ``read_file`` would not
    read without a question (outside the workspace, protected, a third party's, missing), and those that did not fit."""

    html: str
    written: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)


class _Tags(HTMLParser):
    """The start tags of a page and its style blocks, each with where it stands in the page's text."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self._lines: list[int] = [0, *(match.end() for match in re.finditer("\n", text))]
        self.tags: list[tuple[int, int, str, list[tuple[str, str | None]], bool]] = []
        self.styles: list[tuple[int, int]] = []
        self._style_from: int | None = None

    def _here(self) -> int:
        line, column = self.getpos()
        return self._lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start: int = self._here()
        written: str = self.get_starttag_text() or ""
        self.tags.append((start, start + len(written), tag, attrs, written.endswith("/>")))
        if tag == "style":
            self._style_from = start + len(written)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style_from is not None:
            self.styles.append((self._style_from, self._here()))
            self._style_from = None


def _beside(url: str) -> str | None:
    """The path ``url`` names when it is a file beside the page: written relative, with no scheme, host or leading
    slash. ``None`` for a web address, a ``data:`` one, a fragment alone or a path from the root."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return None
    return unquote(parts.path)


def _start_tag(tag: str, attrs: list[tuple[str, str | None]], closed: bool) -> str:
    written: str = "".join(
        f" {name}" if value is None else f' {name}="{escape(value, quote=True)}"' for name, value in attrs
    )
    return f"<{tag}{written}{' /' if closed else ''}>"


def _data_start(path: Path) -> str:
    return f"data:{mimetypes.guess_type(path.name)[0] or 'application/octet-stream'};base64,"


class _Writer:
    """Writes the files a page names into it while they fit, and keeps the names of those it wrote and left out."""

    def __init__(
        self, found: PageFiles, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
    ) -> None:
        self._found: PageFiles = found
        self._workspace: Path = workspace
        self._protected: tuple[Path, ...] = protected
        self._dot_paths: tuple[Path, ...] = dot_paths
        # What the page may still grow by and stay one page: a file is measured before it is read.
        self._room: int = PAGE_MAX_CHARS - _MARGIN - len(found.html)

    def _readable(self, path: Path) -> bool:
        """Whether the resolved ``path`` is one read_file reads without a question."""
        return classify_path(
            path, self._workspace, self._protected, self._dot_paths
        ) == "workspace" and not holds_third_party(path, self._workspace)

    def _read(self, path: Path, limit: int) -> bytes | None:
        """The bytes of ``path``, at most ``limit`` of them, from the file opened without following a link and judged
        again by the path the kernel opened (the independent review: a file swapped for a link to a protected one after
        it was judged is not read); ``None`` for one that cannot be read, is no longer readable so, or grew."""
        try:
            descriptor: int = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            opened: bytes = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(_PATH_BYTES)).split(b"\0", 1)[0]
            if not self._readable(Path(os.fsdecode(opened))):
                return None
            chunks: list[bytes] = []
            left: int = limit + 1
            while left > 0 and (chunk := os.read(descriptor, left)):
                chunks.append(chunk)
                left -= len(chunk)
        except OSError:
            return None
        finally:
            os.close(descriptor)
        data: bytes = b"".join(chunks)
        return data if len(data) <= limit else None

    def file(self, url: str, folder: Path) -> tuple[str, Path, int] | None:
        """The file ``url`` names from ``folder`` and its size, when it is one read_file reads without a question; one
        that is not, or whose path cannot be read (a NUL in it, a folder it may not see), is named as left out."""
        named: str | None = _beside(url)
        if named is None:
            return None
        try:
            path: Path = expand_path(named, folder)
            if self._readable(path) and path.is_file():
                return named, path, path.stat().st_size
        except (OSError, ValueError, PolicyDenied):
            pass
        self._found.left_out.append(named)
        return None

    def _fits(self, named: str, takes: int) -> bool:
        """Whether ``takes`` more characters leave the page one page; a file that does not fit is named as too large."""
        if takes > self._room:
            self._found.too_large.append(named)
            return False
        return True

    def _count(self, named: str, takes: int) -> bool:
        """Count what ``named`` takes in the page in, as it is written there, when it fits."""
        if not self._fits(named, takes):
            return False
        self._room -= takes
        self._found.written.append(named)
        return True

    def _bytes(self, named: str, path: Path, size: int, takes: int) -> bytes | None:
        """The bytes of ``path`` when the ``takes`` characters it is measured at fit and it can be read, counted in by
        what it takes once written; a file that cannot be read is named as left out."""
        if not self._fits(named, takes):
            return None
        data: bytes | None = self._read(path, size)
        if data is None:
            self._found.left_out.append(named)
        return data

    def data(self, url: str, folder: Path) -> str | None:
        found: tuple[str, Path, int] | None = self.file(url, folder)
        if found is None:
            return None
        named, path, size = found
        start: str = _data_start(path)
        data: bytes | None = self._bytes(named, path, size, len(start) + 4 * math.ceil(size / 3))
        if data is None:
            return None
        address: str = f"{start}{base64.b64encode(data).decode('ascii')}"
        return address if self._count(named, len(address)) else None

    def text(self, url: str, folder: Path, closing: str) -> tuple[str, Path] | None:
        """The text of the style sheet or script ``url`` names, with its folder, ``closing`` (the end tag it stands in)
        escaped in it so that it cannot end the element early, when it fits as written, escapes and all."""
        found: tuple[str, Path, int] | None = self.file(url, folder)
        if found is None:
            return None
        named, path, size = found
        # UTF-8 takes at least one byte a character: the file's size bounds its text before the escapes.
        data: bytes | None = self._bytes(named, path, size, size)
        if data is None:
            return None
        # A browser ends the element at its end tag in any letter case: each is escaped as it is written.
        text: str = re.sub(
            re.escape(closing), lambda end: f"<\\{end[0][1:]}", data.decode("utf-8", errors="replace"), flags=re.I
        )
        return (text, path.parent) if self._count(named, len(text)) else None

    def style(self, css: str, folder: Path) -> str:
        """``css`` with the pictures and fonts it names from ``folder`` written in."""

        def written(found: re.Match[str]) -> str:
            if found["imported"]:
                if (named := _beside(found["sheet"])) is not None:
                    self._found.left_out.append(named)
                return found[0]
            data: str | None = self.data(found["url"], folder)
            return found[0] if data is None else f'url("{data}")'

        return _CSS_URL.sub(written, css)


def with_its_files(
    html: str, page: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> PageFiles:
    """The page ``html`` read from ``page`` with the files beside it that it names written in (item 600), while the
    whole stays one page: a linked style sheet becomes a style block, a script's file its text, and a picture, a sound,
    a video or a font of a tag, of a style block, of a ``style`` attribute or of a sheet a ``data:`` address."""
    found: PageFiles = PageFiles(html)
    writer: _Writer = _Writer(found, workspace, protected, dot_paths)
    folder: Path = page.parent
    tags: _Tags = _Tags(html)
    tags.feed(html)
    tags.close()
    edits: list[tuple[int, int, str]] = []
    for start, end, tag, attrs, closed in tags.tags:
        named: dict[str, str | None] = dict(attrs)
        source: str | None = named.get("href") if tag == "link" else named.get("src")
        if tag == "link" and source and "stylesheet" in (named.get("rel") or "").lower().split():
            sheet: tuple[str, Path] | None = writer.text(source, folder, "</style")
            if sheet is not None:
                media: str | None = named.get("media")
                opening: str = f'<style media="{escape(media, quote=True)}">' if media else "<style>"
                edits.append((start, end, f"{opening}{writer.style(*sheet)}</style>"))
            continue
        if tag == "script" and source:
            script: tuple[str, Path] | None = writer.text(source, folder, "</script")
            if script is not None:
                kept: list[tuple[str, str | None]] = [(name, value) for name, value in attrs if name != "src"]
                edits.append((start, end, f"{_start_tag(tag, kept, False)}{script[0]}"))
            continue
        changed: list[tuple[str, str | None]] = []
        for name, value in attrs:
            if value and name == "style" and "url(" in value:
                changed.append((name, writer.style(value, folder)))
                continue
            data: str | None = writer.data(value, folder) if value and name in _SHOWN.get(tag, ()) else None
            changed.append((name, value if data is None else data))
        if changed != attrs:
            edits.append((start, end, _start_tag(tag, changed, closed)))
    edits.extend((start, end, writer.style(html[start:end], folder)) for start, end in tags.styles)
    # The page's text and the edits, in their order, joined once.
    pieces: list[str] = []
    at: int = 0
    for start, end, replacement in sorted(edits):
        pieces.extend((html[at:start], replacement))
        at = end
    pieces.append(html[at:])
    whole: str = "".join(pieces)
    if len(whole) > PAGE_MAX_CHARS:
        # A file grew while it was read, or the attributes written again took more than the margin: the page is shown
        # as written.
        return PageFiles(html, left_out=found.left_out, too_large=[*found.too_large, *found.written])
    found.html = whole
    return found
