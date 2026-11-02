"""Routes for which Chrome the browser tools work in: Dot's own, or the one the user has open."""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from pydantic import JsonValue

from dotagent.browser.attach import INSPECT_PAGE, ChromeState, inspect_chrome
from dotagent.browser.chrome_import import (
    ImportReport,
    chrome_is_open,
    chrome_profile_names,
    chrome_profile_titles,
    cookie_count,
    has_stored_cookies,
    import_chrome_profile,
    require_importable,
)
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
    name_for_import,
    profile_dir,
)
from dotagent.browser.readable import readable_cookies
from dotagent.browser.session import BrowserSession
from dotagent.config_edit import (
    BrowserSetting,
    SettingValue,
    browser_values,
    restore_browser_values,
    set_browser_mode,
    set_browser_setting,
    set_browser_settings,
)
from dotagent.core.errors import BrowserError, InvalidValueError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import chrome_data_dir, config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import (
    BrowserDebugPortRequest,
    BrowserEnabledRequest,
    BrowserModeRequest,
    BrowserProfileRequest,
    BrowserRaiseRequest,
    BrowserRemoteInputRequest,
    BrowserView,
    ChromeImportOptions,
    ChromeImportRequest,
    ChromeImportResult,
    ChromeImportSource,
    ChromeImportTarget,
    ProfileCheck,
)

_log = get_logger("dotagent.server.routes_browser")


async def kept_then_taken(
    keys: tuple[BrowserSetting, ...], keep: Callable[[], None], take: Callable[[], Awaitable[None]]
) -> None:
    """Keep settings ``keys`` of ``[browser]`` in the config file (``keep``), then take them in the running daemon
    (``take``); when the daemon refuses them, those keys are put back as they were, and nothing else (item 942). So a
    file that cannot be written changes nothing, a change the daemon refuses (a port that is taken) is not kept for the
    next start, and a setting another route kept meanwhile stays."""
    before: tuple[tuple[BrowserSetting, SettingValue | None], ...] = await asyncio.to_thread(
        browser_values, config_path(), keys
    )
    await asyncio.to_thread(keep)
    try:
        await take()
    except BaseException:
        await asyncio.to_thread(restore_browser_values, config_path(), before)
        raise


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
            remote_input=session.config.remote_input,
            debug_port=session.config.debug_port,
            devtools_address=devtools_address(session.config),
            enabled=session.config.enabled,
            look=PageLook(
                fit_window=session.config.fit_window,
                viewport_width=session.config.viewport_width,
                viewport_height=session.config.viewport_height,
                appearance=session.config.appearance,
                zoom=session.config.zoom,
            ),
            profile=session.config.profile,
            profiles=await asyncio.to_thread(listed_profiles),
        )

    def refuse_while_runs(what: str) -> None:
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict(f"{what} while tasks are active", {"active_runs": active})

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
            raise RunConflict("the browser cannot be switched while tasks are active", {"active_runs": active})

        # A session a campaign made for the mode the main one is going to is closed first (item 554): the main session
        # works in it now, and the other would keep its Chrome on the same profile.
        async def take() -> None:
            await ctx.runtime.browsers.retire_other(request.mode)
            await adopt_mode(session, request.mode, open_users_chrome)

        await kept_then_taken(("mode",), lambda: set_browser_mode(config_path(), request.mode), take)
        return await state()

    @router.post("/browser/sign-in")
    async def sign_in() -> BrowserView:
        """Open Dot's own profile in a plain Chrome window, where the owner signs in to the sites Dot should use, as
        ``dotx browser login`` does (item 344): Dot's own Chrome is closed first, which is refused while runs are
        active, and the window stays until the owner quits it. While one is open, another is not opened."""
        async with opening:
            if not signing:
                active: list[JsonValue] = list(ctx.manager.active_run_ids())
                if active:
                    raise RunConflict(
                        "Dot's Chrome cannot be closed for signing in while tasks are active", {"active_runs": active}
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
        # Kept first, then taken: a config that cannot be written changes nothing (the review of 2026-11-01).
        await asyncio.to_thread(set_browser_setting, config_path(), "raise_window", request.raise_window)
        session.set_raise_window(request.raise_window)
        return await state()

    @router.post("/browser/remote-input")
    async def set_remote_input(request: BrowserRemoteInputRequest) -> BrowserView:
        """Whether the owner may use a held run's page from the screen that shows it (item 470); takes effect at once
        and is kept."""
        # Kept first: a write that fails must not leave a phone able to drive a held page (the review of 2026-11-01).
        await asyncio.to_thread(set_browser_setting, config_path(), "remote_input", request.remote_input)
        ctx.runtime.browsers.set_remote_input(request.remote_input)
        return await state()

    @router.post("/browser/debug-port")
    async def set_debug_port(request: BrowserDebugPortRequest) -> BrowserView:
        """The port Dot's own Chrome takes DevTools connections on (item 265; 0 for none), kept in the config file.
        Dot's own Chrome, open on another port, is closed and starts on this one with the next call that needs it, so
        the change is refused while runs are active."""
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("the DevTools port cannot be changed while tasks are active", {"active_runs": active})
        # A write that fails must not leave a DevTools port open that the page says was not set (item 942).
        await kept_then_taken(
            ("debug_port",),
            lambda: set_browser_setting(config_path(), "debug_port", request.debug_port),
            lambda: ctx.runtime.browsers.set_debug_port(request.debug_port),
        )
        return await state()

    @router.post("/browser/enabled")
    async def set_enabled(request: BrowserEnabledRequest) -> BrowserView:
        """Whether runs are offered the browser tools (item 354), for the runs that start from now on; kept."""
        await asyncio.to_thread(set_browser_setting, config_path(), "enabled", request.enabled)
        ctx.runtime.browsers.set_enabled(request.enabled)
        return await state()

    @router.post("/browser/look")
    async def set_look(request: PageLook) -> BrowserView:
        """How pages look (items 354 and 358): the size of a page of Dot's own Chrome, the colour scheme and the zoom of
        Dot's own Chrome, kept in the config file. Chrome is let go of so that it starts again with them, so the change
        is refused while runs are active."""
        active: list[JsonValue] = list(ctx.manager.active_run_ids())
        if active:
            raise RunConflict("how pages look cannot be changed while tasks are active", {"active_runs": active})
        settings: tuple[tuple[BrowserSetting, SettingValue], ...] = (
            ("fit_window", request.fit_window),
            ("viewport_width", request.viewport_width),
            ("viewport_height", request.viewport_height),
            ("appearance", request.appearance),
            ("zoom", request.zoom),
        )
        # All five in one edit (item 942): never half of them, nor taken and not kept.
        await kept_then_taken(
            tuple(key for key, _ in settings),
            lambda: set_browser_settings(config_path(), settings),
            lambda: ctx.runtime.browsers.set_look(request),
        )
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
        await kept_then_taken(
            ("profile",),
            lambda: set_browser_setting(config_path(), "profile", request.name),
            lambda: ctx.runtime.browsers.use_profile(request.name),
        )
        return await state()

    @router.post("/browser/profiles/{name}/clear")
    async def clear_browser_profile(name: str) -> BrowserView:
        """Delete what the sites left in the profile ``name``, and the logins and autofill Chrome saved for them (items
        355 and 532). Dot's own Chrome is let go of when it works on it, so the change is refused while runs are
        active, and while a Chrome window has the profile open."""
        await asyncio.to_thread(known_profile, name)
        refuse_while_runs("a profile cannot be cleared")
        own: BrowserSession = ctx.runtime.browsers.session_for("own")
        if own.config.profile == name:
            await own.let_go_of_chrome()
        await refuse_while_open(name)
        await asyncio.to_thread(clear_site_data, profile_dir(name))
        return await state()

    def import_options() -> ChromeImportOptions:
        """What Import from Chrome offers (blocking: it reads the files of the owner's Chrome and of Dot's profiles)."""
        data_dir = chrome_data_dir()
        chrome_open: bool = chrome_is_open(data_dir)
        listed = listed_profiles()
        taken: list[str] = [profile.name for profile in listed]
        # The name Chrome shows for a profile, or its folder's for one Chrome names nowhere; its cookies are counted
        # only while Chrome is closed, as one that runs holds the file as it writes it.
        titles: dict[str, str] = chrome_profile_titles(data_dir)
        sources: list[ChromeImportSource] = []
        for folder in chrome_profile_names(data_dir):
            cookies: int | None = None
            uncounted: str | None = None
            if not chrome_open:
                # A profile whose cookies cannot be counted is listed saying so, not left out with the others (the
                # pre-push review of item 917): its import says what holds it.
                try:
                    cookies = cookie_count(data_dir, folder)
                except BrowserError as error:
                    _log.warning("chrome_cookies_uncounted", folder=folder, error=error.message, context=error.context)
                    uncounted = error.message
            sources.append(
                ChromeImportSource(
                    folder=folder,
                    name=titles.get(folder, folder),
                    cookies=cookies,
                    uncounted=uncounted,
                    new_name=name_for_import(titles.get(folder, folder), taken),
                )
            )
        return ChromeImportOptions(
            chrome_open=chrome_open,
            sources=tuple(sources),
            targets=tuple(
                ChromeImportTarget(
                    name=profile.name, kind=profile.kind, signed_in=has_stored_cookies(profile_dir(profile.name))
                )
                for profile in listed
                if profile.kind != "incognito"
            ),
            in_use=session.config.profile,
        )

    @router.get("/browser/chrome-import")
    async def chrome_import_options() -> ChromeImportOptions:
        """The profiles of the owner's Chrome that hold cookies, with the names Chrome shows for them and their cookies
        (counted while Chrome is closed), and the profiles of Dot's own Chrome an import may fill (item 917). The
        owner's Chrome is only read."""
        return await asyncio.to_thread(import_options)

    async def free_for_import(name: str, use: bool) -> None:
        """Make the profile ``name`` free for an import: refused while runs are active when it is the one in use or is
        to be (Dot's Chrome is let go of then), and while a Chrome window has it open."""
        in_use: bool = session.config.profile == name
        if in_use or use:
            refuse_while_runs("sign-ins cannot be imported into the profile tasks use")
        own: BrowserSession = ctx.runtime.browsers.session_for("own")
        if own.config.profile == name:
            await own.let_go_of_chrome()
        await refuse_while_open(name)

    @router.post("/browser/chrome-import")
    async def import_from_chrome(request: ChromeImportRequest) -> ChromeImportResult:
        """Copy the cookies and the local storage of the owner's Chrome profile ``source`` into a profile of Dot's own
        Chrome (item 917), as ``dotx browser import-chrome`` does: into a new one named after it for ``into`` null,
        which goes again when the import fails, or into ``into``, whose sign-ins are replaced only with ``replace``.
        Chrome must be quit; Incognito is refused, as it keeps nothing. ``use`` makes tasks work on it. The owner's
        Chrome is only read and nothing is decrypted: Chrome reads the cookies with its key in the Keychain, which the
        check that follows asks for (``POST /api/browser/profiles/{name}/check``)."""
        data_dir = chrome_data_dir()
        if await asyncio.to_thread(chrome_is_open, data_dir):
            raise RunConflict(
                "Chrome is open; quit it (⌘Q) so that its files are complete, and import again",
                {"source": request.source},
            )
        made: bool = request.into is None
        if request.into is None:
            listed = await asyncio.to_thread(listed_profiles)
            titles: dict[str, str] = await asyncio.to_thread(chrome_profile_titles, data_dir)
            name: str = name_for_import(titles.get(request.source, request.source), [view.name for view in listed])
        else:
            name = request.into
            await asyncio.to_thread(known_profile, name)
            # Incognito is refused before anything is let go of (the pre-push review of item 917).
            require_importable(profile_dir(name))
            if await asyncio.to_thread(has_stored_cookies, profile_dir(name)) and not request.replace:
                raise InvalidValueError(
                    f"{name} holds sign-ins already; say to replace them, or import into a new profile",
                    {"profile": name},
                )
        await free_for_import(name, request.use)
        if made:
            await asyncio.to_thread(add_profile, name)
        try:
            report: ImportReport = await asyncio.to_thread(
                import_chrome_profile, data_dir, request.source, profile_dir(name)
            )
        except Exception as error:
            if made:
                # The profile was made for this import alone, and nothing of it was made: it goes with it. A stop of the
                # request is let through and leaves it, as the copy may still be under way in its thread.
                try:
                    await asyncio.to_thread(delete_profile, name)
                except OSError as cleanup:
                    raise BrowserError(
                        "the import failed, and the profile made for it could not be removed: delete it in Settings › "
                        "Browser",
                        {"profile": name, "error": str(error), "cleanup": str(cleanup)},
                    ) from error
            raise
        if request.use:
            await ctx.runtime.browsers.use_profile(name)
            await asyncio.to_thread(set_browser_setting, config_path(), "profile", name)
        return ChromeImportResult(
            profile=name,
            cookies=report.cookies,
            loadable=report.loadable,
            local_storage_bytes=report.local_storage_bytes,
            staging_left=report.staging_left,
            browser=await state(),
        )

    @router.post("/browser/profiles/{name}/check")
    async def check_browser_profile(name: str) -> ProfileCheck:
        """How many cookies Chrome reads from the profile ``name`` (item 917), in a hidden Chrome of its own: the check
        that ends an import, when macOS may ask whether Chrome may use its key in the Keychain. None read is an error
        that says what to do."""
        await asyncio.to_thread(known_profile, name)
        await free_for_import(name, False)
        readable: int = await readable_cookies(session.config, ctx.runtime.config.server, profile_dir(name))
        if readable == 0:
            raise BrowserError(
                'Chrome could not read the copied cookies; allow Chrome to use "Chrome Safe Storage" in the Keychain '
                "(Always Allow) and import again, replacing them: Chrome has dropped the cookies it could not read",
                {"profile": name},
            )
        return ProfileCheck(readable=readable)

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
