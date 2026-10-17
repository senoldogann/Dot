"""The files the panel beside a chat shows (``dotagent.previews``): what it shows of a file named by its path or of an
attachment, a PDF's pages and an image as pictures, an Office file as Quick Look draws it, and a document opened in its
application on the Mac. Every route finds its file with ``located_file``, so none reaches a file outside the places the
panel shows."""

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from dotagent.agent.attachments import StoredAttachment, load_attachment
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.desktop.apps import open_document
from dotagent.previews.paths import HEAD_BYTES, OFFICE_KINDS, image_type, located_file, opens_on_mac, shown_kind
from dotagent.previews.pdfpages import page_png
from dotagent.previews.quicklook import QuickLook, media_type, part_path
from dotagent.previews.shown import MAX_OFFICE_BYTES, ShownFile, shown_file
from dotagent.server.context import ServerContext
from dotagent.server.guard import LOOKS_PREFIX


class OpenRequest(FrozenModel):
    path: str


class LookPage(FrozenModel):
    """A page of the drawing Quick Look made of a file, where it is served for a frame of the panel: a workbook's sheet
    with its name, or the whole drawing (``name`` empty)."""

    name: str
    url: str


class LookAnswer(FrozenModel):
    """The pages of the drawing Quick Look made of a file, for the panel to frame one at a time."""

    pages: tuple[LookPage, ...]


def _head(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(HEAD_BYTES)


def _drawable(path: Path) -> Path:
    """``path`` when it is an Office file the panel shows and Quick Look may be asked to draw; ``InvalidValueError``
    otherwise, and why."""
    if shown_kind(path, _head(path)) not in OFFICE_KINDS:
        raise InvalidValueError("only a Word, Excel or PowerPoint file is drawn by Quick Look", {"path": str(path)})
    if path.stat().st_size > MAX_OFFICE_BYTES:
        raise InvalidValueError("the file is too large to be drawn", {"path": str(path), "max": MAX_OFFICE_BYTES})
    return path


def files_router(ctx: ServerContext, looks: QuickLook) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    workspace: Path = ctx.runtime.workspace
    home: Path = Path.home()

    def located(raw: str) -> Path:
        return located_file(raw, home, workspace)

    def shown(raw: str) -> ShownFile:
        return shown_file(located(raw))

    def drawable(raw: str) -> Path:
        return _drawable(located(raw))

    async def drawn(raw: str) -> LookAnswer:
        found: Path = await asyncio.to_thread(drawable, raw)
        drawing: str = await looks.look(found)
        return LookAnswer(
            pages=tuple(
                LookPage(name=page.name, url=f"{LOOKS_PREFIX}{drawing}/{page.file}")
                for page in await looks.pages(drawing)
            )
        )

    @router.get("/files/preview")
    async def preview_file(path: str) -> ShownFile:
        """What the panel shows of the file at ``path``: absolute, or under ``~/``."""
        return await asyncio.to_thread(shown, path)

    @router.get("/attachments/{attachment_id}/preview")
    async def preview_attachment(attachment_id: str) -> ShownFile:
        """What the panel shows of a file attached to a message."""
        stored: StoredAttachment = await asyncio.to_thread(load_attachment, workspace, attachment_id)
        return await asyncio.to_thread(shown, stored.path)

    @router.get("/files/look")
    async def look_file(path: str) -> LookAnswer:
        """Where the drawing Quick Look made of the Office file at ``path`` is served; 422 when it cannot draw it."""
        return await drawn(path)

    @router.get("/attachments/{attachment_id}/look")
    async def look_attachment(attachment_id: str) -> LookAnswer:
        """Where the drawing Quick Look made of an Office file attached to a message is served."""
        stored: StoredAttachment = await asyncio.to_thread(load_attachment, workspace, attachment_id)
        return await drawn(stored.path)

    @router.get("/files/page")
    async def file_page(path: str, number: int, width: int) -> Response:
        """Page ``number`` (from 1) of the PDF at ``path`` as a PNG ``width`` pixels wide, one of ``PAGE_WIDTHS``."""
        found: Path = await asyncio.to_thread(located, path)
        return Response(await asyncio.to_thread(page_png, found, number, width), media_type="image/png")

    @router.get("/files/image")
    async def file_image(path: str) -> FileResponse:
        """The image at ``path``, of a kind every browser draws."""
        found: Path = await asyncio.to_thread(located, path)
        media_type: str | None = image_type(await asyncio.to_thread(_head, found))
        if media_type is None:
            raise InvalidValueError("the file is no image the panel shows", {"path": path})
        return FileResponse(found, media_type=media_type)

    @router.post("/files/open", status_code=204)
    async def open_file(request: OpenRequest) -> None:
        """Open the document at ``path`` in its application on the Mac."""
        found: Path = await asyncio.to_thread(located, request.path)
        if not opens_on_mac(found):
            raise InvalidValueError(
                f"a {found.suffix or 'suffixless'} file is not opened in an application", {"path": request.path}
            )
        await asyncio.to_thread(open_document, found)

    return router


def looks_router(looks: QuickLook) -> APIRouter:
    """The parts of the drawings Quick Look made (``/looks/<id>/<file>``), to whoever names a drawing's id; the guard
    sandboxes them (:data:`dotagent.server.guard.LOOK_HEADERS`)."""
    router: APIRouter = APIRouter()

    @router.get(f"{LOOKS_PREFIX}{{drawing}}/{{name}}")
    async def look_part(drawing: str, name: str) -> FileResponse:
        """A part of a drawing; 404 for an id or a name that names none."""
        found: Path = await asyncio.to_thread(part_path, looks.folder, drawing, name)
        return FileResponse(found, media_type=media_type(name))

    return router
