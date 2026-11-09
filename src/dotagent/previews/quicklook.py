"""A Word document, an Excel workbook or a presentation as macOS's Quick Look draws it (item 268), for the panel beside
a chat to show it as Finder would: its fonts, fills and pictures, a workbook's sheets one tab each.

``qlmanage -p -o`` writes the drawing as a page, ``Preview.html``, with its parts beside it (styles, scripts, pictures,
a page per sheet). Dot keeps each drawing in a folder of its home (``looks/``) named by an id nobody can guess, and
serves its files at ``/looks/<id>/<file>`` to whoever names the id, sandboxed wherever they open
(:data:`dotagent.server.guard.LOOK_HEADERS`): the frame runs Quick Look's scripts with an origin of its own and reaches
no server. A web page Dot made (an ``.html`` file) is kept the same way, the file itself as the drawing's page (item
1077): Quick Look draws none of it; it is served under the same sandbox, and the panel frames it with no script allowed,
so it runs nothing and reaches no server, nor its pictures and styles beside it on the disk. A file is drawn once per
version (its path, size and time of change) in the daemon's life: the id is keyed with a secret the daemon makes at its
start, which empties the folder.
"""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from dotagent.core.errors import NotFoundError, PreviewError, PreviewTimedOut
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

QUICK_LOOK: str = "/usr/bin/qlmanage"
# The page of a drawing, among its parts.
PAGE: str = "Preview.html"
# How long Quick Look may take to draw a file, how often a call that hung is made, and the wait before the next.
DRAW_TIMEOUT_S: float = 30.0
DRAW_ATTEMPTS: int = 2
RETRY_AFTER_S: float = 1.0
# The drawings kept at most; the oldest go first.
KEPT_LOOKS: int = 100
_ID_CHARS: int = 32
_ID: re.Pattern[str] = re.compile(rf"[0-9a-f]{{{_ID_CHARS}}}")
# The names Quick Look gives the parts of a drawing: a page, ``Attachment<n>`` with a suffix, its properties.
_PART: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.[A-Za-z0-9]{1,8}")
# A tab of a workbook's drawing: the sheet's name, then the part its frame loads.
_SHEET_TAB: re.Pattern[str] = re.compile(
    r'<div class="TabViewItem[^"]*">\s*<div class="TabHeader">(.*?)</div>\s*<a href="([^"]+)"', re.DOTALL
)
_DRAWN_SUFFIX: str = ".qlpreview"
_DRAWING_PREFIX: str = ".drawing-"
_ERROR_CHARS: int = 500
_MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}
_OTHER_MEDIA: str = "application/octet-stream"


def look_id(secret: bytes, path: Path, status: os.stat_result) -> str:
    """The id of the drawing of the file at ``path`` as it is now (``status``), which only ``secret`` gives."""
    version: bytes = f"{path}\0{status.st_size}\0{status.st_mtime_ns}".encode()
    return hmac.new(secret, version, hashlib.sha256).hexdigest()[:_ID_CHARS]


def media_type(name: str) -> str:
    """The media type a part of a drawing is served with, by its suffix."""
    return _MEDIA_TYPES.get(Path(name).suffix.lower(), _OTHER_MEDIA)


def part_path(folder: Path, drawing: str, name: str) -> Path:
    """The part ``name`` of the drawing ``drawing`` kept in ``folder``. ``NotFoundError`` for an id or a name that names
    none, so that no path outside a drawing's folder is ever reached."""
    if _ID.fullmatch(drawing) is None or _PART.fullmatch(name) is None:
        raise NotFoundError("no such drawing", {"drawing": drawing, "name": name})
    found: Path = folder / drawing / name
    if not found.is_file():
        raise NotFoundError("no such drawing", {"drawing": drawing, "name": name})
    return found


class DrawnPage(FrozenModel):
    """A page of a drawing the panel frames: a workbook's sheet with its name, or the whole drawing (``name`` empty)."""

    name: str
    file: str


def drawn_pages(page: str) -> tuple[DrawnPage, ...]:
    """The pages of a drawing whose page (``Preview.html``) is ``page``: a workbook's sheets, each a part of its own
    that Quick Look's tabs load into a frame inside the page, or else the page itself. The panel frames each sheet as it
    is, under tabs of its own: a frame inside a sandboxed page is one its parts refuse (``frame-ancestors``)."""
    sheets: tuple[DrawnPage, ...] = tuple(
        DrawnPage(name=unescape(name).strip(), file=file)
        for name, file in _SHEET_TAB.findall(page)
        if _PART.fullmatch(file) is not None
    )
    return sheets if sheets else (DrawnPage(name="", file=PAGE),)


def _oldest_beyond(folder: Path, kept: int) -> list[Path]:
    drawings: list[Path] = [entry for entry in folder.iterdir() if _ID.fullmatch(entry.name) and entry.is_dir()]
    drawings.sort(key=lambda entry: entry.stat().st_mtime_ns, reverse=True)
    return drawings[kept:]


def _drawn_page(drawing: Path) -> Path:
    """The folder Quick Look wrote its drawing to inside ``drawing``. ``PreviewError`` when it wrote none."""
    written: list[Path] = [entry for entry in drawing.iterdir() if entry.name.endswith(_DRAWN_SUFFIX)]
    if len(written) != 1 or not (written[0] / PAGE).is_file():
        raise PreviewError(
            "Quick Look could not draw this file", {"written": [entry.name for entry in drawing.iterdir()]}
        )
    return written[0]


async def _quick_look(path: Path, workshop: Path) -> None:
    """Have Quick Look draw the file at ``path`` into ``workshop``; its process ends with the call, however the call
    ends. ``PreviewError`` when it fails or takes longer than ``DRAW_TIMEOUT_S``."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        QUICK_LOOK,
        "-p",
        "-o",
        str(workshop),
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), DRAW_TIMEOUT_S)
    except TimeoutError as error:
        raise PreviewTimedOut(
            "Quick Look took too long to draw this file", {"path": str(path), "timeout_s": DRAW_TIMEOUT_S}
        ) from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise PreviewError(
            "Quick Look could not draw this file",
            {
                "path": str(path),
                "returncode": process.returncode,
                "stderr": stderr.decode("utf-8", errors="replace")[-_ERROR_CHARS:],
            },
        )


def _empty(folder: Path) -> None:
    """Remove what ``folder`` holds, folders and files, and keep it. Blocking."""
    for entry in folder.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


async def _drawn_by_quick_look(path: Path, workshop: Path) -> None:
    """:func:`_quick_look`, tried again after a hang (item 1084): ``qlmanage`` now and then hangs on a workbook it draws
    at once the next time (a CI runner's gate, five times since item 540), so a call that timed out is made again
    ``DRAW_ATTEMPTS`` times at most, ``RETRY_AFTER_S`` apart and doubling, each one logged; the last one's error is
    raised. A failure that is no hang is raised at once."""
    for attempt in range(1, DRAW_ATTEMPTS + 1):
        try:
            await _quick_look(path, workshop)
            return
        except PreviewTimedOut as error:
            if attempt == DRAW_ATTEMPTS:
                raise
            log.warning("quick_look_hung", path=str(path), attempt=attempt, error=error.message)
            # What the hung call wrote, if anything, goes: the next one writes its own.
            await asyncio.to_thread(_empty, workshop)
            await asyncio.sleep(RETRY_AFTER_S * 2 ** (attempt - 1))


def _keep_page(folder: Path, path: Path, drawing: str) -> list[str]:
    """Keep the web page at ``path`` as the drawing ``drawing`` of ``folder``; the names of the oldest drawings let go
    beyond ``KEPT_LOOKS``. Blocking."""
    workshop: Path = folder / f"{_DRAWING_PREFIX}{secrets.token_hex(8)}"
    workshop.mkdir(parents=True)
    try:
        shutil.copyfile(path, workshop / PAGE)
        os.replace(workshop, folder / drawing)
    except OSError:
        shutil.rmtree(workshop)
        raise
    gone: list[Path] = _oldest_beyond(folder, KEPT_LOOKS)
    for old in gone:
        shutil.rmtree(old)
    return [old.name for old in gone]


@dataclass
class QuickLook:
    """The drawings of this daemon's life (module docstring). A file version is drawn by one call at a time, under a
    lock of its own: one for each version asked for, given up with its drawing when that is let go."""

    folder: Path
    secret: bytes
    _drawing: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def look(self, path: Path) -> str:
        """The id of the drawing of the file at ``path`` (resolved, a Word, Excel or PowerPoint file the panel shows),
        drawn now unless it was already. ``PreviewError`` when Quick Look cannot draw it."""
        status: os.stat_result = await asyncio.to_thread(path.stat)
        drawing: str = look_id(self.secret, path, status)
        async with self._drawing.setdefault(drawing, asyncio.Lock()):
            if not await asyncio.to_thread((self.folder / drawing / PAGE).is_file):
                await self._draw(path, drawing)
        return drawing

    async def page(self, path: Path) -> str:
        """The id of the drawing of the web page at ``path`` (resolved, a file the panel shows): the page itself, kept
        now unless it was already (module docstring)."""
        status: os.stat_result = await asyncio.to_thread(path.stat)
        drawing: str = look_id(self.secret, path, status)
        async with self._drawing.setdefault(drawing, asyncio.Lock()):
            if not await asyncio.to_thread((self.folder / drawing / PAGE).is_file):
                # The locks go with the drawings let go, here in the loop that holds them.
                for gone in await asyncio.to_thread(_keep_page, self.folder, path, drawing):
                    self._drawing.pop(gone, None)
                log.info("page_kept", path=str(path), drawing=drawing)
        return drawing

    async def pages(self, drawing: str) -> tuple[DrawnPage, ...]:
        """The pages of the drawing ``drawing`` (:func:`drawn_pages`)."""
        page: Path = await asyncio.to_thread(part_path, self.folder, drawing, PAGE)
        return drawn_pages(await asyncio.to_thread(page.read_text, encoding="utf-8", errors="replace"))

    async def _draw(self, path: Path, drawing: str) -> None:
        workshop: Path = self.folder / f"{_DRAWING_PREFIX}{secrets.token_hex(8)}"
        await asyncio.to_thread(workshop.mkdir, parents=True)
        try:
            await _drawn_by_quick_look(path, workshop)
            drawn: Path = await asyncio.to_thread(_drawn_page, workshop)
            await asyncio.to_thread(os.replace, drawn, self.folder / drawing)
            for old in await asyncio.to_thread(_oldest_beyond, self.folder, KEPT_LOOKS):
                await asyncio.to_thread(shutil.rmtree, old)
                # Its lock goes with it.
                self._drawing.pop(old.name, None)
        finally:
            await asyncio.to_thread(shutil.rmtree, workshop)
        log.info("look_drawn", path=str(path), drawing=drawing)


def open_quick_look(folder: Path) -> QuickLook:
    """The drawings of a daemon that starts now: ``folder`` emptied of the last daemon's, and a new secret."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return QuickLook(folder=folder, secret=secrets.token_bytes(32))
