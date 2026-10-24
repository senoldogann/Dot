"""A page Dot made in its workspace, shown whole (item 600): the files beside it that it names, its style sheets, its
scripts and the pictures, sounds, videos and fonts they use, are written into it, since a shown page is sandboxed and
loads nothing of this Mac (``PAGE_HEADERS``). A file is written in when it is one ``read_file`` reads without a
question: in the workspace once its links are followed, outside the protected places and Dot's own files, and not one a
crawl or an MCP answer kept there (item 494); and when it fits, the page with its files being one page
(``PAGE_MAX_CHARS``): one that does not is left out, and the page is shown without it. What a script loads by itself (a
module's imports, a fetch) is not.

What the answer says of a page is true of what a page commonly names (item 612, the final review). The pictures of a
``srcset`` and the icon of a page are written in as the pictures of a tag are. A frame, an object and an embed are named
as not loaded, since the policy of a shown page lets none of them load anything. A picture that three ``img`` tags or
more name is written once, and the tags take it from there, so that an icon in each row of a long table is in every row.
Each file is found, read and encoded once however often it is named, and the work has a time limit
(:data:`WORK_S`) after which the rest of the page is shown as written, and the answer says so."""

import base64
import fcntl
import json
import math
import mimetypes
import os
import re
import time
from collections import Counter
from collections.abc import Iterator
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
# What a frame, an object and an embed name. The policy of a shown page has no frame-src, object-src or child-src, and
# its default-src is none, so none of them loads anything, a data: address included: the files they name are named as
# not loaded, and not written in.
_FRAMED: dict[str, str] = {"iframe": "src", "embed": "src", "object": "data"}
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
# How long writing the files in may take. A page that names files by the thousands has each found, judged, read and
# encoded, which holds the interpreter while it works; after this long the rest of the page is shown as it was written.
WORK_S: float = 10.0
# A picture that this many ``img`` tags name by their ``src`` is written once, in a table at the end of the page that
# the tags take it from (below), and not in each of them: a page is 500,000 characters, which a small icon in each
# row of a thousand uses up in three hundred.
_SHARED_FROM: int = 3
# The mark of a tag that takes its picture from the table, and the script that gives it. The script stands in a
# function, so that it declares nothing the page's own scripts could meet.
_MARK: str = "data-dot-pic"
_BODY_END: re.Pattern[str] = re.compile(r"</body", re.IGNORECASE)


class _TimeUp(Exception):
    """The time for writing the files in ran out (:data:`WORK_S`)."""


@dataclass
class PageFiles:
    """A page with the files beside it that it names written in: the names written in, those ``read_file`` would not
    read without a question (outside the workspace, protected, a third party's, missing), those that did not fit, and
    those a frame, an object or an embed names, which no shown page loads. ``cut`` says in words why the writing in
    stopped before the end of the page, when it did."""

    html: str
    written: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)
    framed: list[str] = field(default_factory=list)
    cut: str = ""


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


def _srcset_urls(value: str) -> Iterator[tuple[int, int]]:
    """Where the addresses of a ``srcset`` stand in it, as the HTML standard reads it: a candidate is an address, which
    is what comes up to a space (the commas that end it end the candidate; one inside it, as in a ``data:`` address,
    does not), and descriptors after it, up to the next comma outside parentheses."""
    size: int = len(value)
    at: int = 0
    while at < size:
        while at < size and (value[at].isspace() or value[at] == ","):
            at += 1
        if at >= size:
            return
        start: int = at
        while at < size and not value[at].isspace():
            at += 1
        end: int = at
        while end > start and value[end - 1] == ",":
            end -= 1
        yield start, end
        if end != at:
            continue
        depth: int = 0
        while at < size:
            char: str = value[at]
            at += 1
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                break


def _is_icon(rel: str | None) -> bool:
    """Whether a ``link`` with this ``rel`` names the icon of the page (``icon``, ``shortcut icon``,
    ``apple-touch-icon`` and the like)."""
    return any("icon" in token for token in (rel or "").lower().split())


class _Writer:
    """Writes the files a page names into it while they fit, and keeps the names of those it wrote and left out. Each
    file is found, read and encoded once, however often the page names it, within :data:`WORK_S`."""

    def __init__(
        self, found: PageFiles, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
    ) -> None:
        self._found: PageFiles = found
        self._workspace: Path = workspace
        self._protected: tuple[Path, ...] = protected
        self._dot_paths: tuple[Path, ...] = dot_paths
        # What the page may still grow by and stay one page: a file is measured before it is read.
        self._room: int = PAGE_MAX_CHARS - _MARGIN - len(found.html)
        self._deadline: float = time.monotonic() + WORK_S
        # What was found of each file named from a folder (``None``: it is not one that is read), what was read of it
        # and its data: address, each once.
        self._places: dict[tuple[Path, str], tuple[str, Path, int] | None] = {}
        self._contents: dict[Path, bytes | None] = {}
        self._addresses: dict[Path, str] = {}
        # The pictures that ``img`` tags take from the table: the number of each file's entry, and the table.
        self._numbers: dict[Path, int] = {}
        self.table: dict[int, str] = {}

    def mark(self) -> tuple[int, int, int, int, int, int]:
        """Where the names and the room stand now, for :meth:`undo` to go back to."""
        found: PageFiles = self._found
        return (
            len(found.written),
            len(found.left_out),
            len(found.too_large),
            len(found.framed),
            self._room,
            len(self.table),
        )

    def undo(self, mark: tuple[int, int, int, int, int, int]) -> None:
        """Take back what was counted since ``mark``: the edit it was for is not made."""
        written, left_out, too_large, framed, room, table = mark
        found: PageFiles = self._found
        del found.written[written:], found.left_out[left_out:], found.too_large[too_large:], found.framed[framed:]
        self._room = room
        for path, number in list(self._numbers.items()):
            if number > table:
                del self._numbers[path]
                del self.table[number]

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

    def _find(self, named: str, folder: Path) -> tuple[str, Path, int] | None:
        try:
            path: Path = expand_path(named, folder)
            if self._readable(path) and path.is_file():
                return named, path, path.stat().st_size
        except (OSError, ValueError, PolicyDenied):
            pass
        return None

    def file(self, url: str, folder: Path) -> tuple[str, Path, int] | None:
        """The file ``url`` names from ``folder`` and its size, when it is one read_file reads without a question; one
        that is not, or whose path cannot be read (a NUL in it, a folder it may not see), is named as left out each time
        it is named. A file is looked for once: a page that names a thousand different ones runs out of time
        (:class:`_TimeUp`), one that names a file a thousand times does not."""
        named: str | None = _beside(url)
        if named is None:
            return None
        if (folder, named) not in self._places:
            if time.monotonic() > self._deadline:
                raise _TimeUp
            self._places[folder, named] = self._find(named, folder)
        found: tuple[str, Path, int] | None = self._places[folder, named]
        if found is None:
            self._found.left_out.append(named)
        return found

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
        what it takes once written; a file that cannot be read is named as left out. It is read once."""
        if not self._fits(named, takes):
            return None
        if path not in self._contents:
            self._contents[path] = self._read(path, size)
        data: bytes | None = self._contents[path]
        if data is None:
            self._found.left_out.append(named)
        return data

    def _address(self, named: str, path: Path, size: int) -> str | None:
        """The data: address of the file at ``path`` when its length fits, encoded once; nothing is counted yet."""
        start: str = _data_start(path)
        data: bytes | None = self._bytes(named, path, size, len(start) + 4 * math.ceil(size / 3))
        if data is None:
            return None
        if path not in self._addresses:
            self._addresses[path] = f"{start}{base64.b64encode(data).decode('ascii')}"
        return self._addresses[path]

    def data(self, url: str, folder: Path) -> str | None:
        found: tuple[str, Path, int] | None = self.file(url, folder)
        if found is None:
            return None
        named, path, size = found
        address: str | None = self._address(named, path, size)
        return address if address is not None and self._count(named, len(address)) else None

    def shared(self, url: str, folder: Path) -> int | None:
        """The number of the entry of the picture ``url`` names in the table that the ``img`` tags take their pictures
        from, made when it is first asked for: the picture is in the page once, and each tag takes only its mark.
        ``None`` for a file that is not written in (named as left out or as too large, as :meth:`data` names it)."""
        found: tuple[str, Path, int] | None = self.file(url, folder)
        if found is None:
            return None
        named, path, size = found
        number: int | None = self._numbers.get(path)
        if number is not None:
            # The picture is in the table: a tag after the first costs its mark alone, and its size is not asked
            # against the room again (the review of item 612: a picture past half the room fitted its first tag and
            # no other, and was named as left out though it was in the page).
            return number if self._count(named, len(f' {_MARK}="{number}"')) else None
        address: str | None = self._address(named, path, size)
        if address is None:
            return None
        number = len(self.table) + 1
        # The entry of the table and the mark of the tag that asks first; the tags after it cost their marks.
        takes: int = len(address) + len(f'"{number}":"",') + len(f' {_MARK}="{number}"')
        if not self._count(named, takes):
            return None
        self._numbers[path] = number
        self.table[number] = address
        return number

    def framed(self, url: str) -> None:
        """Name the file a frame, an object or an embed names, if it is a file beside the page: no shown page loads
        it."""
        named: str | None = _beside(url)
        if named is not None:
            self._found.framed.append(named)

    def srcset(self, value: str, folder: Path) -> str:
        """``value``, the ``srcset`` of an ``img`` or a ``source``, with the pictures its candidates name written in."""
        pieces: list[str] = []
        at: int = 0
        for start, end in _srcset_urls(value):
            address: str | None = self.data(value[start:end], folder)
            if address is not None:
                pieces.extend((value[at:start], address))
                at = end
        pieces.append(value[at:])
        return "".join(pieces)

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


def _repeated(tags: list[tuple[int, int, str, list[tuple[str, str | None]], bool]]) -> frozenset[str]:
    """The files that :data:`_SHARED_FROM` ``img`` tags or more name by their ``src`` alone (with no ``srcset``)."""
    counts: Counter[str] = Counter()
    for _, _, tag, attrs, _ in tags:
        if tag != "img":
            continue
        named: dict[str, str | None] = dict(attrs)
        source: str | None = named.get("src")
        if source and "srcset" not in named and (beside := _beside(source)) is not None:
            counts[beside] += 1
    return frozenset(name for name, count in counts.items() if count >= _SHARED_FROM)


def _rewritten(
    writer: _Writer,
    folder: Path,
    repeated: frozenset[str],
    tag: str,
    attrs: list[tuple[str, str | None]],
    closed: bool,
) -> str | None:
    """The start tag of ``tag`` as it is written once the files it names are in, or ``None`` when it stays as it is. A
    linked style sheet becomes a style block and a script's file its text, with the tag they stood in."""
    named: dict[str, str | None] = dict(attrs)
    source: str | None = named.get("href") if tag == "link" else named.get("src")
    rel: list[str] = (named.get("rel") or "").lower().split()
    if tag == "link" and source and "stylesheet" in rel:
        sheet: tuple[str, Path] | None = writer.text(source, folder, "</style")
        if sheet is None:
            return None
        media: str | None = named.get("media")
        opening: str = f'<style media="{escape(media, quote=True)}">' if media else "<style>"
        return f"{opening}{writer.style(*sheet)}</style>"
    if tag == "script" and source:
        script: tuple[str, Path] | None = writer.text(source, folder, "</script")
        if script is None:
            return None
        kept: list[tuple[str, str | None]] = [(name, value) for name, value in attrs if name != "src"]
        return f"{_start_tag(tag, kept, False)}{script[0]}"
    icon: bool = tag == "link" and _is_icon(named.get("rel"))
    changed: list[tuple[str, str | None]] = []
    for name, value in attrs:
        if not value:
            changed.append((name, value))
        elif name == "style" and "url(" in value:
            changed.append((name, writer.style(value, folder)))
        elif name == "srcset" and tag in ("img", "source"):
            changed.append((name, writer.srcset(value, folder)))
        elif _FRAMED.get(tag) == name:
            writer.framed(value)
            changed.append((name, value))
        elif tag == "img" and name == "src" and "srcset" not in named and _beside(value) in repeated:
            number: int | None = writer.shared(value, folder)
            changed.append((name, value) if number is None else (_MARK, str(number)))
        elif name in _SHOWN.get(tag, ()) or (icon and name == "href"):
            data: str | None = writer.data(value, folder)
            changed.append((name, value if data is None else data))
        else:
            changed.append((name, value))
    return _start_tag(tag, changed, closed) if changed != attrs else None


def _with_table(whole: str, table: dict[int, str]) -> str:
    """``whole`` with the script that gives the ``img`` tags their pictures from ``table``, before the end of the body
    (at the end of the page when it has none)."""
    entries: str = json.dumps({str(number): address for number, address in table.items()}, separators=(",", ":"))
    safe: str = entries.replace("</", "<\\/")
    script: str = (
        f"<script>(()=>{{const p={safe};"
        f'for(const e of document.querySelectorAll("img[{_MARK}]"))e.src=p[e.getAttribute("{_MARK}")]}})()</script>'
    )
    at: int | None = None
    for end in _BODY_END.finditer(whole):
        at = end.start()
    return f"{whole}{script}" if at is None else f"{whole[:at]}{script}{whole[at:]}"


def with_its_files(
    html: str, page: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> PageFiles:
    """The page ``html`` read from ``page`` with the files beside it that it names written in (item 600), while the
    whole stays one page: a linked style sheet becomes a style block, a script's file its text, and a picture, a sound,
    a video or a font of a tag, of a ``srcset``, of an icon link, of a style block, of a ``style`` attribute or of a
    sheet a ``data:`` address. A picture that three ``img`` tags or more name is written once, in a table that the tags
    take it from; the files a frame, an object and an embed name are named as not loaded (item 612). Within
    :data:`WORK_S`: the rest of the page is shown as written after it, and ``cut`` says so."""
    found: PageFiles = PageFiles(html)
    writer: _Writer = _Writer(found, workspace, protected, dot_paths)
    folder: Path = page.parent
    tags: _Tags = _Tags(html)
    tags.feed(html)
    tags.close()
    repeated: frozenset[str] = _repeated(tags.tags)
    edits: list[tuple[int, int, str]] = []
    try:
        for start, end, tag, attrs, closed in tags.tags:
            mark: tuple[int, int, int, int, int, int] = writer.mark()
            try:
                replacement: str | None = _rewritten(writer, folder, repeated, tag, attrs, closed)
            except _TimeUp:
                writer.undo(mark)
                raise
            if replacement is not None:
                edits.append((start, end, replacement))
        for start, end in tags.styles:
            mark = writer.mark()
            try:
                edits.append((start, end, writer.style(html[start:end], folder)))
            except _TimeUp:
                writer.undo(mark)
                raise
    except _TimeUp:
        found.cut = (
            f"Writing the files in took more than {WORK_S:g} seconds, so the files named in the rest of the page are "
            "not written in."
        )
    # The page's text and the edits, in their order, joined once.
    pieces: list[str] = []
    at: int = 0
    for start, end, replacement in sorted(edits):
        pieces.extend((html[at:start], replacement))
        at = end
    pieces.append(html[at:])
    whole: str = "".join(pieces)
    if writer.table:
        whole = _with_table(whole, writer.table)
    if len(whole) > PAGE_MAX_CHARS:
        # A file grew while it was read, or the attributes written again took more than the margin: the page is shown
        # as written.
        return PageFiles(
            html,
            left_out=found.left_out,
            too_large=[*found.too_large, *found.written],
            framed=found.framed,
            cut=found.cut,
        )
    found.html = whole
    return found
