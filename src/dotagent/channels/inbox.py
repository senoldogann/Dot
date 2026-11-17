"""The files the owner sends on a channel (item 806): a picture, a PDF or a document that comes with a message, read
from where Messages keeps it and handed to the run as the composer hands an upload.

Messages keeps what a message brings under ``~/Library/Messages/Attachments`` (beside its database), and names it in the
row of the attachment with ``~`` for the home folder. A file is read only from there, as a regular file reached with no
link on the way, of at most :data:`~dotagent.agent.attachments.MAX_ATTACHMENT_BYTES`; anything else is no file of the
owner's message. A photo of an iPhone (HEIC, HEIF) and a TIFF, which neither Pillow nor the model reads, are drawn as
PNG by macOS first (:func:`~dotagent.core.photos.drawn_by_macos`). A voice message and a video, of at most
:data:`~dotagent.agent.attachments.MAX_MEDIA_BYTES`, are kept as they came, for the run to watch with ``watch_video``
(item 1213). A file of another kind is not read, and the owner is told so. A file whose download from iCloud has not
ended yet (it is missing, or shorter than the row says) is waited for a moment by the channel."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotagent.agent.attachments import MAX_ATTACHMENT_BYTES, MAX_MEDIA_BYTES, AttachmentKind, media_kind
from dotagent.core.photos import drawn_by_macos
from dotagent.policy.paths import within

# What the files of one message may hold in all, read into memory before they are kept (item 1213): a video may be
# 200 MB, and a message of ten is not read whole.
MESSAGE_BYTES: int = 300 * 1024 * 1024
# The folder beside Messages' database that holds what messages brought.
ATTACHMENTS_DIR: str = "Attachments"
# The image types a model reads as they are; any other image is drawn as PNG by macOS first.
_AS_THEY_ARE: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_DRAWN_SUFFIXES: frozenset[str] = frozenset({".heic", ".heif", ".tif", ".tiff"})
_AUDIO_TYPES: tuple[str, ...] = ("public.audio", "com.apple.coreaudio-format", "public.mpeg-4-audio", "com.apple.m4a")

# Why a file of the owner's message is not read: a file Messages has not finished bringing to this Mac, or one that is
# out of place, too large or of no kind Dot reads.
type Unread = Literal["coming", "elsewhere", "too_large", "unreadable"]


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


def _media(file: RowFile) -> AttachmentKind | None:
    """``video`` or ``audio`` for a video or a sound (a voice message), by its type as Messages tells it or its name."""
    found: AttachmentKind | None = media_kind(file.name or file.path, file.mime)
    if found is not None:
        return found
    if file.uti in _AUDIO_TYPES:
        return "audio"
    return "video" if file.uti.startswith(("public.movie", "public.video", "com.apple.quicktime")) else None


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


def read_file(file: RowFile, root: Path, home: Path, room: int) -> Incoming | Unread:
    """``file`` read for the run, or why it is not; ``room`` is what the message's files may still hold in all, past
    which it is too large. Blocking."""
    if file.sticker:
        return "unreadable"
    media: AttachmentKind | None = _media(file)
    limit: int = min(room, MAX_ATTACHMENT_BYTES if media is None else MAX_MEDIA_BYTES)
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
        if status.st_size > limit:
            return "too_large"
        if status.st_size == 0 or status.st_size < file.size:
            return "coming"
        with os.fdopen(descriptor, "rb", closefd=False) as opened:
            data: bytes = opened.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        return "too_large"
    name: str = file.name or path.name
    media_type: str = file.mime
    if media is not None:
        # Kept as it came, for watch_video (item 1213); a type Messages left empty is said by the kind.
        return Incoming(name, media_type or f"{media}/{Path(name).suffix.lstrip('.').casefold() or 'unknown'}", data)
    if _suffix(file) in _DRAWN_SUFFIXES or (media_type.startswith("image/") and media_type not in _AS_THEY_ARE):
        drawn: bytes = drawn_by_macos(data)
        if drawn is data:
            return "unreadable"
        return Incoming(f"{Path(name).stem}.png", "image/png", drawn)
    return Incoming(name, media_type, data)
