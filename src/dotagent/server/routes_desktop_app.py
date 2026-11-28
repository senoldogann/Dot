"""Routes of the desktop shell's settings (``[desktop_app]``): whether Dot.app opens at login, and the language of its
own words. The shell reads them from the config file each second (``desktop/src-tauri/src/lib.rs``), so a change here
needs nothing else: the shell writes or removes its login item, and redraws its menu, within a second."""

import asyncio

from fastapi import APIRouter

from dotagent.config import DesktopAppConfig, load_config
from dotagent.config_edit import set_setting
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import DesktopAppRequest, DesktopAppView

log = get_logger(__name__)


def desktop_app_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One change at a time, so that two writes of the config file do not cross.
    changing: asyncio.Lock = asyncio.Lock()

    @router.get("/desktop-app")
    async def desktop_app() -> DesktopAppView:
        return await asyncio.to_thread(_read)

    @router.put("/desktop-app")
    async def change_desktop_app(request: DesktopAppRequest) -> DesktopAppView:
        """Write what the request gives into the config file; what it leaves out stays. The answer is the file read
        again."""
        async with changing:
            if request.start_at_login is not None:
                await asyncio.to_thread(
                    set_setting, config_path(), "desktop_app", "start_at_login", request.start_at_login
                )
            if request.language is not None:
                await asyncio.to_thread(set_setting, config_path(), "desktop_app", "language", request.language)
        log.info("desktop_app_changed", start_at_login=request.start_at_login, language=request.language)
        return await asyncio.to_thread(_read)

    return router


def _read() -> DesktopAppView:
    """``[desktop_app]`` as the config file holds it now, which the shell reads too: a hand edit since the daemon
    started is seen. Blocking."""
    held: DesktopAppConfig = load_config(config_path()).desktop_app
    return DesktopAppView(start_at_login=held.start_at_login, language=held.language)
