"""Routes for which Chrome the browser tools work in: Dot's own, or the one the user has open."""

import asyncio

from fastapi import APIRouter
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, ChromeState, inspect_chrome
from dotagent.browser.modes import adopt_mode
from dotagent.browser.session import BrowserSession
from dotagent.config_edit import set_browser_mode
from dotagent.core.errors import RunConflict
from dotagent.core.paths import chrome_data_dir, config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import BrowserModeRequest, BrowserView


def browser_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    session: BrowserSession = ctx.runtime.browser

    async def state() -> BrowserView:
        chrome: ChromeState = await asyncio.to_thread(inspect_chrome, chrome_data_dir())
        return BrowserView(
            mode=session.mode,
            connected=session.connected,
            chrome_running=chrome.running,
            remote_debugging=chrome.remote_debugging,
            port=chrome.port,
            inspect_page=INSPECT_PAGE,
            attach_timeout_s=session.config.attach_timeout_s,
        )

    @router.get("/browser")
    async def browser_state() -> BrowserView:
        return await state()

    @router.post("/browser/mode")
    async def set_mode(request: BrowserModeRequest) -> BrowserView:
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("the browser cannot be switched while runs are active", {"active_runs": active})
        await adopt_mode(session, request.mode)
        await asyncio.to_thread(set_browser_mode, config_path(), request.mode)
        return await state()

    return router
