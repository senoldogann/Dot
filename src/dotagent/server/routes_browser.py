"""Routes for which Chrome the browser tools work in: Dot's own, or the one the user has open."""

import asyncio

from fastapi import APIRouter
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, ChromeState, inspect_chrome
from dotagent.browser.devtools import devtools_address
from dotagent.browser.login import open_sign_in_window
from dotagent.browser.look import PageLook
from dotagent.browser.modes import adopt_mode, open_users_chrome
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.profiles import (
    add_profile,
    clear_site_data,
    delete_profile,
    known_profile,
    listed_profiles,
    profile_dir,
)
from dotagent.browser.session import BrowserSession
from dotagent.config_edit import BrowserSetting, SettingValue, set_browser_mode, set_browser_setting
from dotagent.core.errors import InvalidValueError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import chrome_data_dir, config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import (
    BrowserDebugPortRequest,
    BrowserEnabledRequest,
    BrowserModeRequest,
    BrowserProfileRequest,
    BrowserRaiseRequest,
    BrowserView,
)

_log = get_logger("dotagent.server.routes_browser")


def browser_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    session: BrowserSession = ctx.runtime.browser
    # The plain Chrome window on Dot's own profile that Settings › Browser opened for signing in (item 344): the wait
    # for it to quit, while it is open; one request at a time opens it.
    signing: set[asyncio.Task[int]] = set()
    opening: asyncio.Lock = asyncio.Lock()

    def closed(window: asyncio.Task[int]) -> None:
        signing.discard(window)
        if not window.cancelled() and window.exception() is None and window.result() != 0:
            _log.warning("browser_sign_in_window_failed", exit_code=window.result())

    async def state() -> BrowserView:
        chrome: ChromeState = await asyncio.to_thread(inspect_chrome, chrome_data_dir())
        return BrowserView(
            mode=session.mode,
            connected=session.connected,
            lost=session.lost,
            waiting_allow=session.waiting_allow,
            signing_in=bool(signing),
            chrome_running=chrome.running,
            remote_debugging=chrome.remote_debugging,
            port=chrome.port,
            inspect_page=INSPECT_PAGE,
            attach_timeout_s=session.config.attach_timeout_s,
            raise_window=session.config.raise_window,
            debug_port=session.config.debug_port,
            devtools_address=devtools_address(session.config),
            enabled=session.config.enabled,
            look=PageLook(
                fit_window=session.config.fit_window,
                viewport_width=session.config.viewport_width,
                viewport_height=session.config.viewport_height,
                appearance=session.config.appearance,
            ),
            profile=session.config.profile,
            profiles=await asyncio.to_thread(listed_profiles),
        )

    def refuse_while_runs(what: str) -> None:
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict(f"{what} while runs are active", {"active_runs": active})

    async def refuse_while_open(name: str) -> None:
        """Refuse a change to the profile ``name`` while a Chrome window has it open (Sign in… opens one)."""
        holder: int | None = await asyncio.to_thread(profile_holder_pid, profile_dir(name))
        if holder is not None:
            raise RunConflict(
                "the profile is open in a Chrome window; quit that Chrome first", {"profile": name, "pid": holder}
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

    @router.post("/browser/sign-in")
    async def sign_in() -> BrowserView:
        """Open Dot's own profile in a plain Chrome window, where the owner signs in to the sites Dot should use, as
        ``dotx browser login`` does (item 344): Dot's own Chrome is closed first, which is refused while runs are active,
        and the window stays until the owner quits it. While one is open, another is not opened."""
        async with opening:
            if not signing:
                active: list[JsonValue] = list(ctx.manager.active_run_ids())
                if active:
                    raise RunConflict(
                        "Dot's Chrome cannot be closed for signing in while runs are active", {"active_runs": active}
                    )
                own: BrowserSession = ctx.runtime.browsers.session_for("own")
                await own.let_go_of_chrome()
                window: asyncio.subprocess.Process = await open_sign_in_window(own.profile_dir)
                waiting: asyncio.Task[int] = asyncio.create_task(window.wait())
                signing.add(waiting)
                waiting.add_done_callback(closed)
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

    @router.post("/browser/enabled")
    async def set_enabled(request: BrowserEnabledRequest) -> BrowserView:
        """Whether runs are offered the browser tools (item 354), for the runs that start from now on; kept."""
        ctx.runtime.browsers.set_enabled(request.enabled)
        await asyncio.to_thread(set_browser_setting, config_path(), "enabled", request.enabled)
        return await state()

    @router.post("/browser/look")
    async def set_look(request: PageLook) -> BrowserView:
        """How pages look (item 354): the size of a page of Dot's own Chrome and the colour scheme, kept in the config
        file. Chrome is let go of so that it starts again with them, so the change is refused while runs are active."""
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("how pages look cannot be changed while runs are active", {"active_runs": active})
        await ctx.runtime.browsers.set_look(request)
        settings: tuple[tuple[BrowserSetting, SettingValue], ...] = (
            ("fit_window", request.fit_window),
            ("viewport_width", request.viewport_width),
            ("viewport_height", request.viewport_height),
            ("appearance", request.appearance),
        )
        for key, value in settings:
            await asyncio.to_thread(set_browser_setting, config_path(), key, value)
        return await state()

    @router.post("/browser/profiles")
    async def add_browser_profile(request: BrowserProfileRequest) -> BrowserView:
        """Add a profile of Dot's own Chrome (item 355): an empty one, which Chrome fills on its first start on it."""
        await asyncio.to_thread(add_profile, request.name)
        return await state()

    @router.post("/browser/profile")
    async def use_browser_profile(request: BrowserProfileRequest) -> BrowserView:
        """Have Dot's own Chrome work on the profile ``name`` from its next start (item 355), kept in the config file.
        Chrome is let go of, so the change is refused while runs are active."""
        await asyncio.to_thread(known_profile, request.name)
        refuse_while_runs("the profile cannot be changed")
        await ctx.runtime.browsers.use_profile(request.name)
        await asyncio.to_thread(set_browser_setting, config_path(), "profile", request.name)
        return await state()

    @router.post("/browser/profiles/{name}/clear")
    async def clear_browser_profile(name: str) -> BrowserView:
        """Delete the cookies and the caches of the profile ``name`` (item 355). Dot's own Chrome is let go of when it
        works on it, so the change is refused while runs are active, and while a Chrome window has the profile open."""
        await asyncio.to_thread(known_profile, name)
        refuse_while_runs("a profile cannot be cleared")
        own: BrowserSession = ctx.runtime.browsers.session_for("own")
        if own.config.profile == name:
            await own.let_go_of_chrome()
        await refuse_while_open(name)
        await asyncio.to_thread(clear_site_data, profile_dir(name))
        return await state()

    @router.delete("/browser/profiles/{name}")
    async def delete_browser_profile(name: str) -> BrowserView:
        """Delete a profile the owner added (item 355), with what it kept; the one in use stays."""
        await asyncio.to_thread(known_profile, name)
        if session.config.profile == name:
            raise InvalidValueError("the profile in use cannot be deleted; use another one first", {"profile": name})
        await refuse_while_open(name)
        await asyncio.to_thread(delete_profile, name)
        return await state()

    return router
