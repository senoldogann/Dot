"""The icon of a site a chat linked to: this Mac fetches ``https://<host>/favicon.ico`` and keeps it."""

import time

from fastapi import APIRouter
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from dotagent.core.errors import WebError
from dotagent.core.logs import get_logger
from dotagent.server.context import ServerContext
from dotagent.server.favicons import FaviconCache, favicon_host, favicon_url, read_icon

log = get_logger(__name__)
_MAX_BYTES: int = 64 * 1024
_SERVER_ERROR: int = 500


def favicons_router(ctx: ServerContext) -> APIRouter:
    """``GET /api/favicons/{host}`` answers the site's icon, 404 when it has none (kept for a while) and 502 when it
    could not be fetched (not kept: the next drawing asks again). The host is a name, and the fetch is the web
    connector's, which refuses an address that is not public."""
    router: APIRouter = APIRouter(prefix="/api")
    cache: FaviconCache = FaviconCache()

    @router.get("/favicons/{host}")
    async def favicon(host: str) -> Response:
        try:
            name: str = favicon_host(host)
        except ValueError:
            raise HTTPException(status_code=404) from None
        kept = cache.get(name, time.monotonic())
        if kept is not None:
            if kept.body is None:
                raise HTTPException(status_code=404)
            return Response(kept.body, media_type=kept.media_type, headers={"Cache-Control": "private, max-age=86400"})
        try:
            document = await ctx.runtime.web.get(favicon_url(name), _MAX_BYTES)
        except WebError as error:
            log.warning("favicon_fetch_failed", host=name, error=error.message, context=error.context)
            raise HTTPException(status_code=502) from error
        if document.status >= _SERVER_ERROR:
            # The site failed rather than said it has no icon: not kept, asked again by the next drawing.
            log.warning("favicon_fetch_failed", host=name, status=document.status)
            raise HTTPException(status_code=502)
        found = read_icon(cache, name, document)
        if found is None:
            log.info("favicon_missed", host=name)
            raise HTTPException(status_code=404)
        log.info("favicon_kept", host=name, bytes=len(found[0]))
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=86400"})

    return router
