"""The files the panel beside a chat shows (``dotagent.previews``): what it shows of a file named by its path or of an
attachment, a PDF's pages and an image as pictures, and a document opened in its application on the Mac. Every route
finds its file with ``located_file``, so none reaches a file outside the places the panel shows."""

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from dotagent.agent.attachments import StoredAttachment, load_attachment
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.desktop.apps import open_document
from dotagent.previews.paths import HEAD_BYTES, image_type, located_file, opens_on_mac
from dotagent.previews.pdfpages import page_png
from dotagent.previews.shown import ShownFile, shown_file
from dotagent.server.context import ServerContext


class OpenRequest(FrozenModel):
    path: str


def _head(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(HEAD_BYTES)


def files_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    workspace: Path = ctx.runtime.workspace
    home: Path = Path.home()

    def located(raw: str) -> Path:
        return located_file(raw, home, workspace)

    def shown(raw: str) -> ShownFile:
        return shown_file(located(raw))

    @router.get("/files/preview")
    async def preview_file(path: str) -> ShownFile:
        """What the panel shows of the file at ``path``: absolute, or under ``~/``."""
        return await asyncio.to_thread(shown, path)

    @router.get("/attachments/{attachment_id}/preview")
    async def preview_attachment(attachment_id: str) -> ShownFile:
        """What the panel shows of a file attached to a message."""
        stored: StoredAttachment = await asyncio.to_thread(load_attachment, workspace, attachment_id)
        return await asyncio.to_thread(shown, stored.path)

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
