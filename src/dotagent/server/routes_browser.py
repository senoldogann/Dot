"""Routes for which Chrome the browser tools work in: Dot's own, or the one the user has open."""

import asyncio

from fastapi import APIRouter
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, ChromeState, inspect_chrome
from dotagent.browser.devtools import devtools_address
from dotagent.browser.modes import adopt_mode, open_users_chrome
from dotagent.browser.session import BrowserSession
from dotagent.config_edit import set_browser_mode, set_browser_setting
from dotagent.core.errors import RunConflict
from dotagent.core.paths import chrome_data_dir, config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import BrowserDebugPortRequest, BrowserModeRequest, BrowserRaiseRequest, BrowserView


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
            raise_window=session.config.raise_window,
            debug_port=session.config.debug_port,
            devtools_address=devtools_address(session.config),
        )

    @router.get("/browser")
    async def browser_state() -> BrowserView:
        return await state()

    @router.post("/browser/mode")
    async def set_mode(request: BrowserModeRequest) -> BrowserView:
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("the browser cannot be switched while runs are active", {"active_runs": active})
        await adopt_mode(session, request.mode, open_users_chrome)
        await asyncio.to_thread(set_browser_mode, config_path(), request.mode)
        return await state()

    @router.post("/browser/raise")
    async def set_raise(request: BrowserRaiseRequest) -> BrowserView:
        """Whether Chrome is brought to the front while a run works (item 97); takes effect at once and is kept."""
        session.set_raise_window(request.raise_window)
        await asyncio.to_thread(set_browser_setting, config_path(), "raise_window", request.raise_window)
        return await state()

    @router.post("/browser/debug-port")
    async def set_debug_port(request: BrowserDebugPortRequest) -> BrowserView:
        """The port Dot's own Chrome takes DevTools connections on (item 265; 0 for none), kept in the config file.
        Dot's own Chrome, open on another port, is closed and starts on this one with the next call that needs it, so
        the change is refused while runs are active."""
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("the DevTools port cannot be changed while runs are active", {"active_runs": active})
        await ctx.runtime.browsers.set_debug_port(request.debug_port)
        await asyncio.to_thread(set_browser_setting, config_path(), "debug_port", request.debug_port)
        return await state()

    return router
