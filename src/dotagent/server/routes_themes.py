"""Routes for the themes of one's own (item 128): the themes of the folder with the files left out of it, and saving
or deleting one. The page sends a theme as the file it is kept as, and the daemon checks it as it checks the files it
reads (``dotagent.themes``)."""

import asyncio

from fastapi import APIRouter

from dotagent.core.logs import get_logger
from dotagent.core.paths import themes_dir
from dotagent.themes import StoredTheme, ThemeFile, ThemeFolder, delete_theme, read_theme_folder, save_theme

log = get_logger(__name__)


def themes_router() -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One change of the folder at a time: each weighs the files there against the caps before it writes.
    writing: asyncio.Lock = asyncio.Lock()

    # A theme goes out as its file holds it: the keys it has, none written as null.
    @router.get("/themes", response_model_exclude_none=True)
    async def themes() -> ThemeFolder:
        return await asyncio.to_thread(read_theme_folder, themes_dir())

    @router.put("/themes/{theme_id}", response_model_exclude_none=True)
    async def put_theme(theme_id: str, theme: ThemeFile) -> StoredTheme:
        """Save a theme of one's own, new or over the one of that id."""
        async with writing:
            stored: StoredTheme = await asyncio.to_thread(save_theme, themes_dir(), theme_id, theme)
        log.info("theme_saved", theme=theme_id)
        return stored

    @router.delete("/themes/{theme_id}", status_code=204)
    async def remove_theme(theme_id: str) -> None:
        async with writing:
            await asyncio.to_thread(delete_theme, themes_dir(), theme_id)
        log.info("theme_deleted", theme=theme_id)

    return router
