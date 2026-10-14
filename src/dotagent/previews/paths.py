"""Which files the panel beside a chat shows, how it shows each, and which it opens in their application on the Mac.

The panel shows a file under the workspace, or under the home folder outside any hidden folder and outside
``~/Library``, except for the folders cloud drives keep the files they sync in (OneDrive and the others in
``~/Library/CloudStorage``, iCloud Drive in ``~/Library/Mobile Documents``). A path is judged once its links are
followed, so a link cannot lead out of those places. Dot's own files (its database, its token, its keys) are in
``~/Library/Application Support/Dot`` and never shown, the workspace in it aside.

What the panel draws is told by the file's first bytes, its suffix aside for Office files and tables: a PDF, an image
of a kind every browser draws, a Word document, an Excel workbook, a presentation, a CSV or TSV table, or text.

Only documents open in their application (``OPENED_SUFFIXES``): a script, a page or an application would run.
"""

from pathlib import Path
from typing import Literal

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.office import OfficeKind, office_kind, unreadable_office_file
from dotagent.web.text import document_kind

type ShownKind = Literal["pdf", "image", "document", "workbook", "presentation", "table", "text"]

# The first bytes a file is told by.
HEAD_BYTES: int = 4096
# The images every browser draws, by their first bytes (a WebP file's are RIFF, its size, then WEBP).
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# A table's cells are split by its suffix's delimiter.
TABLE_DELIMITERS: dict[str, str] = {".csv": ",", ".tsv": "\t"}
OPENED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".heic",
        ".docx",
        ".doc",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".pptx",
        ".ppt",
        ".pages",
        ".numbers",
        ".key",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".csv",
        ".tsv",
        ".txt",
        ".md",
        ".json",
        ".log",
        ".yaml",
        ".yml",
    }
)
# The folders of ~/Library that hold a person's own files: those cloud drives sync.
_CLOUD_FOLDERS: frozenset[str] = frozenset({"CloudStorage", "Mobile Documents"})


def _named(raw: str, home: Path) -> Path:
    if raw.startswith("~/"):
        return home / raw.removeprefix("~/")
    path: Path = Path(raw)
    if not path.is_absolute():
        raise InvalidValueError("the path is neither absolute nor under ~", {"path": raw})
    return path


def _within(path: Path, root: Path) -> tuple[str, ...] | None:
    """The parts of ``path`` under ``root``, or ``None`` when it is not under it."""
    return path.relative_to(root).parts if path.is_relative_to(root) else None


def located_file(raw: str, home: Path, workspace: Path) -> Path:
    """The file ``raw`` names (``~/`` standing for ``home``), its links followed, when it is in a place the panel shows
    files of. ``NotFoundError`` when there is no such file; ``InvalidValueError`` when it is no file or is elsewhere,
    and where."""
    try:
        path: Path = _named(raw, home).resolve(strict=True)
    except FileNotFoundError as error:
        raise NotFoundError("there is no such file", {"path": raw}) from error
    if not path.is_file():
        raise InvalidValueError("the path is no file", {"path": raw})
    if _within(path, workspace.resolve()) is not None:
        return path
    parts: tuple[str, ...] | None = _within(path, home.resolve())
    if parts is None:
        raise InvalidValueError("the file is outside your home folder", {"path": raw})
    if any(part.startswith(".") for part in parts):
        raise InvalidValueError("the file is in a hidden folder", {"path": raw})
    if parts[0] == "Library" and (len(parts) < 3 or parts[1] not in _CLOUD_FOLDERS):
        raise InvalidValueError("the file is among the files of an application", {"path": raw})
    return path


def image_type(head: bytes) -> str | None:
    """The media type of an image every browser draws, by its first bytes, or ``None``."""
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return next((media_type for magic, media_type in _IMAGE_MAGIC if head.startswith(magic)), None)


def shown_kind(path: Path, head: bytes) -> ShownKind:
    """How the panel shows the file at ``path`` whose first bytes are ``head``. ``InvalidValueError`` when it does not
    show it, and why."""
    office: OfficeKind | None = office_kind(path.name, head)
    if office is not None:
        return office
    unreadable: str | None = unreadable_office_file(path.name, head)
    if unreadable is not None:
        raise InvalidValueError(unreadable, {"path": str(path)})
    if image_type(head) is not None:
        return "image"
    match document_kind("", head):
        case "pdf":
            return "pdf"
        case "text" | "html":
            return "table" if path.suffix.lower() in TABLE_DELIMITERS else "text"
        case "binary":
            if path.suffix.lower() in TABLE_DELIMITERS:
                return "table"
            raise InvalidValueError("the panel does not show a file of this kind", {"path": str(path)})


def opens_on_mac(path: Path) -> bool:
    """Whether the file at ``path`` is a document that may open in its application on the Mac."""
    return path.suffix.lower() in OPENED_SUFFIXES
