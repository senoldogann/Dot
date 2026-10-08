"""Documents as text for the model: HTML becomes headings, lists, tables and links; other formats are decoded."""

import codecs
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit

from dotagent.core.errors import WebError
from dotagent.core.types import FrozenModel

type DocumentKind = Literal["html", "pdf", "text", "binary"]
type _TokenKind = Literal["text", "raw", "marker", "break", "link_open", "link_close"]


class ReadablePage(FrozenModel):
    title: str
    description: str
    text: str


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: str


_HTML_TYPES: frozenset[str] = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/sql",
        "application/csv",
        "application/x-sh",
    }
)
_SNIFF_BYTES: int = 4096
_META_CHARSET: re.Pattern[bytes] = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.IGNORECASE)
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

_SKIPPED_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "object",
        "embed",
        "select",
        "textarea",
        "button",
        "dialog",
        "nav",
        "footer",
        "aside",
        "audio",
        "video",
        "map",
    }
)
# ARIA landmarks and widgets that are page furniture, the counterpart of the skipped tags above.
_SKIPPED_ROLES: frozenset[str] = frozenset(
    {"navigation", "contentinfo", "complementary", "search", "dialog", "alertdialog", "menu", "menubar"}
)
_VOID_TAGS: frozenset[str] = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_PARAGRAPH_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "section",
        "article",
        "main",
        "blockquote",
        "table",
        "figure",
        "address",
        "details",
        "fieldset",
        "dl",
        "center",
    }
)
_LINE_TAGS: frozenset[str] = frozenset(
    {"div", "header", "hgroup", "summary", "figcaption", "dt", "dd", "caption", "thead", "tbody", "tfoot", "form"}
)
_HEADINGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_HEADING_ENDERS: frozenset[str] = _PARAGRAPH_TAGS | {"ul", "ol", "li", "tr", "pre", "hr"}
_MAIN_TAGS: frozenset[str] = frozenset({"main", "article"})
_UNUSABLE_LINKS: tuple[str, ...] = ("#", "javascript:", "data:", "tel:", "sms:", "about:", "vbscript:")
_LINK_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_MAX_LINK_CHARS: int = 500
_MAX_ALT_CHARS: int = 120
_MIN_ALT_CHARS: int = 3
_MAX_DESCRIPTION_CHARS: int = 400
_MIN_MAIN_CHARS: int = 500
_MIN_MAIN_SHARE: float = 0.2
_SPACES: re.Pattern[str] = re.compile(r"\s+")
_TRAILING_SPACES: re.Pattern[str] = re.compile(r"[ \t]+\n")
_BLANK_RUNS: re.Pattern[str] = re.compile(r"\n{3,}")
_HIDDEN_STYLE: re.Pattern[str] = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
# Soft hyphens, zero-width spaces and joiners, direction marks and byte-order marks: invisible, and used by pages to
# decorate anchors or to hide text.
_INVISIBLE: re.Pattern[str] = re.compile("[\u00ad\u200b\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def document_kind(media_type: str, head: bytes) -> DocumentKind:
    """How to read a document: by its declared type, or by its first bytes when the type says nothing useful."""
    if media_type in _HTML_TYPES:
        return "html"
    if media_type == "application/pdf":
        return "pdf"
    if media_type.startswith("text/") or media_type.endswith(("+json", "+xml")) or media_type in _TEXT_TYPES:
        return "text"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    return "text" if _looks_like_text(head) else "binary"


def _looks_like_text(head: bytes) -> bool:
    sample: bytes = head[:_SNIFF_BYTES]
    if b"\x00" in sample:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(sample, final=False)
    except UnicodeDecodeError:
        return False
    return True


def decode_body(body: bytes, header_charset: str | None) -> str:
    """The document as text: the character set from the header, a byte-order mark or a ``<meta>`` tag, else UTF-8."""
    label: str = header_charset or _bom_charset(body) or _meta_charset(body) or "utf-8"
    try:
        return body.decode(label, errors="replace")
    except LookupError as exc:
        raise WebError("the document declares a character set that is not known", {"charset": label}) from exc


def _bom_charset(body: bytes) -> str | None:
    return next((label for mark, label in _BOMS if body.startswith(mark)), None)


def _meta_charset(body: bytes) -> str | None:
    found: re.Match[bytes] | None = _META_CHARSET.search(body[:_SNIFF_BYTES])
    return found.group(1).decode("ascii", errors="replace") if found is not None else None


def _is_hidden(attributes: dict[str, str]) -> bool:
    return (
        "hidden" in attributes
        or attributes.get("aria-hidden", "").lower() == "true"
        or _HIDDEN_STYLE.search(attributes.get("style", "")) is not None
    )


def _is_furniture(tag: str, attributes: dict[str, str]) -> bool:
    """Parts that are not the content: hidden ones, landmarks such as navigation and search, and the links of a
    language switcher (a link that names the language of its target with ``hreflang``)."""
    role: str = attributes.get("role", "").lower().partition(" ")[0]
    return _is_hidden(attributes) or role in _SKIPPED_ROLES or (tag == "a" and "hreflang" in attributes)


def _is_content(token: _Token) -> bool:
    return token.kind in ("text", "raw") and bool(token.value.strip())


def _usable_link(base: str, target: str) -> str:
    """The absolute address a link points to, or an empty string for anchors, scripts and unparsable targets."""
    cleaned: str = target.strip()
    if not cleaned or cleaned.lower().startswith(_UNUSABLE_LINKS):
        return ""
    try:
        absolute: str = urljoin(base, cleaned)
        scheme: str = urlsplit(absolute).scheme
    except ValueError:
        # A target that cannot be parsed has no destination; the text of the link stays.
        return ""
    return absolute if scheme in _LINK_SCHEMES and len(absolute) <= _MAX_LINK_CHARS else ""


def _trailing_newlines(value: str, before: int) -> int:
    """The number of line breaks at the end of the output once ``value`` is appended to output that ended in
    ``before`` of them."""
    stripped: str = value.rstrip("\n")
    return before + len(value) if not stripped else len(value) - len(stripped)


def _render(tokens: Sequence[_Token]) -> str:
    """Text from tokens. A break asks for at least that many line breaks at this point rather than adding them, and
    the first line of a list item stays on the line of its marker."""
    parts: list[str] = []
    open_links: list[int] = []
    last: str = ""
    newlines: int = 0
    at_marker: bool = False
    for token in tokens:
        match token.kind:
            case "link_open":
                open_links.append(len(parts))
                parts.append("")
            case "link_close":
                if open_links:
                    start: int = open_links.pop()
                    if "".join(parts[start + 1 :]).strip():
                        parts[start] = "["
                        parts.append(f"]({token.value})")
                        newlines = 0
            case "break":
                missing: int = int(token.value) - newlines
                if missing > 0 and not at_marker:
                    parts.append("\n" * missing)
                    newlines += missing
                    last = "\n"
            case "marker":
                parts.append(token.value)
                newlines, last, at_marker = 0, " ", True
            case "raw":
                parts.append(token.value)
                newlines = _trailing_newlines(token.value, newlines)
                last = token.value[-1:] or last
                at_marker = False
            case "text":
                value: str = token.value.lstrip(" ") if last in ("", " ", "\n") else token.value
                if value:
                    parts.append(value)
                    newlines, last, at_marker = 0, value[-1], False
    joined: str = "".join(parts)
    return _BLANK_RUNS.sub("\n\n", _TRAILING_SPACES.sub("\n", joined)).strip()


class _Reader(HTMLParser):
    """Collects the readable parts of an HTML page; the text inside ``<main>``/``<article>`` is kept apart so a page
    that has a main region can be reduced to it."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base: str = base_url
        self.everything: list[_Token] = []
        self.main_region: list[_Token] = []
        self.title_parts: list[str] = []
        self.description: str = ""
        self._main_stack: list[str] = []
        self._skip_stack: list[str] = []
        self._link_stack: list[str] = []
        self._list_stack: list[int] = []
        self._pre_depth: int = 0
        self._in_heading: bool = False
        self._row_cells: int = 0
        self._in_title: bool = False
        self._title_done: bool = False
        # The marker of a list item and what follows it, held back until the item shows content: an item whose
        # parts were all left out (a language switcher, a row of icons) leaves no empty bullet behind.
        self._pending_item: list[tuple[_Token, bool]] | None = None

    def _store(self, token: _Token, in_main: bool) -> None:
        self.everything.append(token)
        if in_main:
            self.main_region.append(token)

    def _emit(self, token: _Token) -> None:
        in_main: bool = bool(self._main_stack)
        if self._pending_item is None:
            self._store(token, in_main)
            return
        self._pending_item.append((token, in_main))
        if _is_content(token):
            held: list[tuple[_Token, bool]] = self._pending_item
            self._pending_item = None
            for held_token, held_in_main in held:
                self._store(held_token, held_in_main)

    def _break(self, lines: int) -> None:
        """A line break in the output; inside a heading (anchor boxes and wrappers are blocks) it is a space."""
        self._emit(_Token("text", " ") if self._in_heading else _Token("break", str(lines)))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str] = {name: value or "" for name, value in attrs}
        if self._skip_stack or tag in _SKIPPED_TAGS or _is_furniture(tag, attributes):
            if tag not in _VOID_TAGS:
                self._skip_stack.append(tag)
            return
        self._open(tag, attributes)
        if tag in _MAIN_TAGS or attributes.get("role") == "main":
            self._main_stack.append(tag)

    def _open(self, tag: str, attributes: dict[str, str]) -> None:
        if tag in _HEADING_ENDERS or tag in _HEADINGS:
            self._in_heading = False
        if tag in _HEADINGS:
            self._break(2)
            self._emit(_Token("raw", "#" * int(tag[1]) + " "))
            self._in_heading = True
        elif tag in _PARAGRAPH_TAGS:
            self._break(2)
        elif tag in _LINE_TAGS:
            self._break(1)
        elif tag in ("ul", "ol"):
            self._break(1)
            self._list_stack.append(-1 if tag == "ul" else 0)
        elif tag == "li":
            self._open_item()
        elif tag == "tr":
            self._break(1)
            self._row_cells = 0
        elif tag in ("td", "th"):
            if self._row_cells:
                self._emit(_Token("text", " | "))
            self._row_cells += 1
        elif tag == "br":
            self._break(1)
        elif tag == "hr":
            self._break(2)
        elif tag == "pre":
            self._break(2)
            self._emit(_Token("raw", "```\n"))
            self._pre_depth += 1
        elif tag == "code" and not self._pre_depth:
            self._emit(_Token("text", "`"))
        elif tag == "a":
            self._open_link(attributes.get("href", ""))
        elif tag == "img":
            self._image(attributes.get("alt", ""))
        elif tag == "title" and not self._title_done:
            self._in_title = True
        elif tag == "base" and attributes.get("href"):
            self._base = urljoin(self._base, attributes["href"])
        elif tag == "meta" and not self.description:
            wanted: str = (attributes.get("name") or attributes.get("property") or "").lower()
            if wanted in ("description", "og:description"):
                self.description = _SPACES.sub(" ", attributes.get("content", "")).strip()[:_MAX_DESCRIPTION_CHARS]

    def _open_item(self) -> None:
        self._pending_item = None  # the previous item had no content, so its marker is dropped
        self._break(1)
        indent: str = "  " * max(len(self._list_stack) - 1, 0)
        if self._list_stack and self._list_stack[-1] >= 0:
            self._list_stack[-1] += 1
            marker: str = f"{self._list_stack[-1]}. "
        else:
            marker = "- "
        self._pending_item = [(_Token("marker", indent + marker), bool(self._main_stack))]

    def _open_link(self, target: str) -> None:
        href: str = _usable_link(self._base, target)
        self._link_stack.append(href)
        if href:
            self._emit(_Token("link_open", ""))

    def _image(self, alt: str) -> None:
        description: str = _SPACES.sub(" ", alt).strip()
        if len(description) >= _MIN_ALT_CHARS:
            self._emit(_Token("text", f"[image: {description[:_MAX_ALT_CHARS]}]"))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            if tag in self._skip_stack:
                while self._skip_stack.pop() != tag:
                    pass
            return
        self._close(tag)
        if self._main_stack and self._main_stack[-1] == tag:
            self._main_stack.pop()

    def _close(self, tag: str) -> None:
        if tag in ("li", "ul", "ol"):
            self._pending_item = None
        if tag in _HEADINGS:
            self._in_heading = False
            self._break(2)
        elif tag in _PARAGRAPH_TAGS:
            self._break(2)
        elif tag in _LINE_TAGS or tag in ("tr", "li"):
            self._break(1)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._break(1)
        elif tag == "pre" and self._pre_depth:
            self._pre_depth -= 1
            self._emit(_Token("raw", "\n```"))
            self._break(2)
        elif tag == "code" and not self._pre_depth:
            self._emit(_Token("text", "`"))
        elif tag == "a" and self._link_stack:
            href: str = self._link_stack.pop()
            if href:
                self._emit(_Token("link_close", href))
        elif tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data: str) -> None:
        visible: str = _INVISIBLE.sub("", data)
        if self._skip_stack or not visible:
            return
        if self._in_title:
            self.title_parts.append(visible)
        elif self._pre_depth:
            self._emit(_Token("raw", visible))
        else:
            self._emit(_Token("text", _SPACES.sub(" ", visible)))


def html_to_text(html: str, base_url: str) -> ReadablePage:
    """The readable content of an HTML page. Navigation, scripts, forms controls and hidden parts are left out, links
    keep their (absolute) addresses, and a page with a main region is reduced to it."""
    reader: _Reader = _Reader(base_url)
    reader.feed(html)
    reader.close()
    everything: str = _render(reader.everything)
    main: str = _render(reader.main_region)
    chosen: str = (
        main if len(main) >= _MIN_MAIN_CHARS and len(main) >= len(everything) * _MIN_MAIN_SHARE else everything
    )
    return ReadablePage(
        title=_SPACES.sub(" ", "".join(reader.title_parts)).strip(), description=reader.description, text=chosen
    )
