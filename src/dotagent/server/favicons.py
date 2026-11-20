"""A site's own icon for a link in the chat, fetched from that site and kept on this Mac.

The page asks for a host. The fetch is ``https://<host>/favicon.ico`` and nothing else: the address the reader
clicked is not sent anywhere, and no favicon service is asked. A host that is not a public site's name is refused
before any connection, and the web connector checks the address again as it connects.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from dotagent.web.types import FetchedDocument

# A DNS name: labels of letters, digits and hyphens, one dot at least, a letter suffix. An address of a machine
# (127.0.0.1, a private range) is not a name, so it never matches.
_HOST: re.Pattern[str] = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_REFUSED_SUFFIX: tuple[str, ...] = (".local", ".localhost", ".localdomain")
_REFUSED: frozenset[str] = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})
_MAX_BYTES: int = 64 * 1024
_KEPT_S: float = 24 * 60 * 60
_MISSED_S: float = 10 * 60
_IMAGES: frozenset[str] = frozenset(
    {
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
    }
)


def favicon_host(host: str) -> str:
    """The host an icon may be fetched for, or ``ValueError`` when `host` is not a public site's name."""
    cleaned: str = host.strip().lower().rstrip(".")
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    if (
        not _HOST.fullmatch(cleaned)
        or cleaned in _REFUSED
        or any(cleaned.endswith(suffix) for suffix in _REFUSED_SUFFIX)
    ):
        raise ValueError(cleaned)
    return cleaned


def favicon_url(host: str) -> str:
    """``https://<host>/favicon.ico`` for a host :func:`favicon_host` accepts. Nothing of a page's path is kept."""
    return f"https://{favicon_host(host)}/favicon.ico"


def icon_media(body: bytes, media_type: str) -> str | None:
    """The image type to send for `body`, or ``None`` when it is not a small icon.

    An HTML page, an empty body and a body over the cap are refused. A type the server left unnamed is accepted
    when the bytes are an icon, a PNG, a JPEG, a GIF or a WebP.
    """
    if not body or len(body) > _MAX_BYTES or body.lstrip().startswith(b"<"):
        return None
    kind: str = media_type.split(";", 1)[0].strip().lower()
    if kind in _IMAGES:
        return kind
    sniffed: str | None = _sniff(body)
    if sniffed is not None and kind in ("", "application/octet-stream", "text/plain"):
        return sniffed
    return None


def _sniff(body: bytes) -> str | None:
    if body.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class _Kept:
    until: float
    body: bytes | None
    media_type: str


class FaviconCache:
    """Icons kept in the process, and the misses, so a chat of many links asks a site once."""

    def __init__(self) -> None:
        self._kept: dict[str, _Kept] = {}

    def get(self, host: str, now: float) -> _Kept | None:
        found: _Kept | None = self._kept.get(host)
        if found is None or found.until <= now:
            return None
        return found

    def store(self, host: str, body: bytes | None, media_type: str, now: float) -> None:
        self._kept[host] = _Kept(now + (_KEPT_S if body is not None else _MISSED_S), body, media_type)


def icon_from(document: FetchedDocument) -> tuple[bytes, str] | None:
    """The icon in `document`, when the answer is a small image, else ``None``."""
    if document.status != 200 or document.truncated:
        return None
    media: str | None = icon_media(document.body, document.media_type)
    if media is None:
        return None
    return document.body, media


def read_icon(
    cache: FaviconCache, host: str, document: FetchedDocument, now: float | None = None
) -> tuple[bytes, str] | None:
    """The cached icon of `host`, or the one in `document` (the site's answer), stored either way: an answer that is no
    icon is kept as a miss."""
    moment: float = time.monotonic() if now is None else now
    kept: _Kept | None = cache.get(host, moment)
    if kept is not None:
        return None if kept.body is None else (kept.body, kept.media_type)
    found: tuple[bytes, str] | None = icon_from(document)
    if found is None:
        cache.store(host, None, "", moment)
        return None
    cache.store(host, found[0], found[1], moment)
    return found
