"""The files the owner sends on a channel (item 806): a picture, a PDF or a document that comes with a message, read
from where Messages keeps it and handed to the run as the composer hands an upload.

Messages keeps what a message brings under ``~/Library/Messages/Attachments`` (beside its database), and names it in the
row of the attachment with ``~`` for the home folder. A file is read only from there, as a regular file reached with no
link on the way, of at most :data:`~dotagent.agent.attachments.MAX_ATTACHMENT_BYTES`; anything else is no file of the
owner's message. A photo of an iPhone (HEIC, HEIF) and a TIFF, which neither Pillow nor the model reads, are drawn as
PNG by macOS first (:func:`~dotagent.core.photos.drawn_by_macos`). A voice message, a video and a file of another
kind are not read, and the owner is told so. A file whose download from iCloud has not ended yet (it is missing, or
shorter than the row says) is waited for a moment by the channel."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES
from dotagent.core.photos import drawn_by_macos
from dotagent.policy.paths import within

# The folder beside Messages' database that holds what messages brought.
ATTACHMENTS_DIR: str = "Attachments"
# The image types a model reads as they are; any other image is drawn as PNG by macOS first.
_AS_THEY_ARE: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_DRAWN_SUFFIXES: frozenset[str] = frozenset({".heic", ".heif", ".tif", ".tiff"})
_AUDIO_TYPES: tuple[str, ...] = ("public.audio", "com.apple.coreaudio-format", "public.mpeg-4-audio", "com.apple.m4a")
_AUDIO_SUFFIXES: frozenset[str] = frozenset({".caf", ".m4a", ".mp3", ".aac", ".amr", ".wav"})
_VIDEO_SUFFIXES: frozenset[str] = frozenset({".mov", ".mp4", ".m4v", ".3gp"})

# Why a file of the owner's message is not read: a voice message or a sound, a video, a file Messages has not finished
# bringing to this Mac, or one that is out of place, too large or of no kind Dot reads.
type Unread = Literal["audio", "video", "coming", "elsewhere", "too_large", "unreadable"]


@dataclass(frozen=True)
class RowFile:
    """A file a row of Messages' database brought, as the row of its attachment names it: ``path`` as Messages wrote it
    (``~/Library/Messages/Attachments/…``), ``name`` the file's own name as it was sent, ``mime`` and ``uti`` its
    type as Messages tells it (either may be empty), ``size`` its length when it has come (0 when not told) and
    ``sticker`` whether it is a sticker, which is no file of the owner's."""

    path: str
    name: str
    mime: str
    uti: str
    size: int
    sticker: bool = False


@dataclass(frozen=True)
class Incoming:
    """A file the owner sent, read and ready to be kept as an upload: its ``name``, ``media_type`` and bytes."""

    name: str
    media_type: str
    data: bytes


def _suffix(file: RowFile) -> str:
    return Path(file.name or file.path).suffix.casefold()


def _kind(file: RowFile) -> Unread | None:
    """``audio`` or ``video`` for a file of those kinds, ``None`` for one that may be read."""
    suffix: str = _suffix(file)
    if file.mime.startswith("audio/") or file.uti in _AUDIO_TYPES or suffix in _AUDIO_SUFFIXES:
        return "audio"
    if file.mime.startswith("video/") or file.uti.startswith(("public.movie", "public.video", "com.apple.quicktime")):
        return "video"
    return "video" if suffix in _VIDEO_SUFFIXES else None


def where_it_is(file: RowFile, root: Path, home: Path) -> Path | None:
    """The file ``file`` names, its links resolved, when it lies in ``root`` (the folder of attachments); ``None`` when
    it is elsewhere. ``~`` is ``home``. Blocking."""
    raw: str = file.path
    named: Path = home / raw[2:] if raw.startswith("~/") else Path(raw)
    if not named.is_absolute():
        return None
    # ``..`` is read before the place is judged, so that a name cannot climb out of the folder while it is missing.
    path: Path = Path(os.path.normpath(named))
    try:
        resolved: Path = path.resolve(strict=True)
    except OSError:
        return path if within(path, root) else None
    return resolved if within(resolved, root.resolve()) else None


def read_file(file: RowFile, root: Path, home: Path) -> Incoming | Unread:
    """``file`` read for the run, or why it is not. Blocking."""
    if file.sticker:
        return "unreadable"
    unread: Unread | None = _kind(file)
    if unread is not None:
        return unread
    path: Path | None = where_it_is(file, root, home)
    if path is None:
        return "elsewhere"
    try:
        descriptor: int = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "coming"
    except OSError:
        return "elsewhere"
    try:
        status: os.stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            return "elsewhere"
        if status.st_size > MAX_ATTACHMENT_BYTES:
            return "too_large"
        if status.st_size == 0 or status.st_size < file.size:
            return "coming"
        with os.fdopen(descriptor, "rb", closefd=False) as opened:
            data: bytes = opened.read(MAX_ATTACHMENT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_ATTACHMENT_BYTES:
        return "too_large"
    name: str = file.name or path.name
    media_type: str = file.mime
    if _suffix(file) in _DRAWN_SUFFIXES or (media_type.startswith("image/") and media_type not in _AS_THEY_ARE):
        drawn: bytes = drawn_by_macos(data)
        if drawn is data:
            return "unreadable"
        return Incoming(f"{Path(name).stem}.png", "image/png", drawn)
    return Incoming(name, media_type, data)
