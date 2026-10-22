"""Routes of the applications of Customize › Connectors (item 278): where each one stands, and the step that sets it up;
the listings of the Plugins page (item 316), which takes those steps; and the icon of an application of the Mac, read
from its bundle."""

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

from dotagent.browser.session import BrowserSession
from dotagent.connections import app_icons, mac
from dotagent.connections.catalogue import FOLDER_APPS, MAC_APPS, WEB_APPS, FolderApp, MacApp, WebApp
from dotagent.connections.directory import plugin_views
from dotagent.connections.states import (
    ConnectionState,
    folder_state,
    folder_step,
    mac_state,
    mac_step,
    web_state,
    web_step,
)
from dotagent.connections.web import CookieKey, profile_cookies, signed_in
from dotagent.core.errors import NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.desktop import perms
from dotagent.server.context import ServerContext
from dotagent.server.views import ConnectionsView, ConnectionView, PluginsView
from dotagent.skills.office import CLOUD_STORAGE, onedrive_folders
from dotagent.store.db import Store

log = get_logger(__name__)

# The browser thread whose tab a sign-in opens in.
_SIGN_IN_THREAD: str = "connect-apps"
# How long a step waits for the owner's answer to macOS's question before it answers that the question waits; the
# question stays on the Mac until it is answered.
_ASK_WAIT_S: float = 60.0
# The answers of macOS about an application that are kept for the time it does not run (item 288).
_KEPT_ANSWERS: dict[str, mac.AutomationState] = {"allowed": "allowed", "not_asked": "not_asked", "refused": "refused"}


def _answer_key(program: Path, bundle_id: str) -> str:
    """The kv key of the last answer macOS gave about the application ``bundle_id`` for ``program``, the program it
    holds responsible for Dot: an answer is about that program alone."""
    return f"automation:{program}:{bundle_id}"


def _first_time(bundle_id: str) -> mac.AutomationState:
    """macOS's question about the application ``bundle_id``, started hidden first when it does not run."""
    state: mac.AutomationState = mac.automation_state(bundle_id)
    if state == "not_open":
        state = mac.start_hidden(bundle_id)
    return mac.ask_automation(bundle_id) if state == "not_asked" else state


def connections_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    session: BrowserSession = ctx.runtime.browser
    store: Store = ctx.runtime.store
    # The questions macOS asks the owner now, by application: each ends with the owner's answer.
    asking: dict[str, asyncio.Future[mac.AutomationState]] = {}

    def live_state(app: MacApp) -> tuple[ConnectionState, bool]:
        """What macOS says about ``app`` now, and whether the installed application has an icon to show."""
        installed: Path | None = mac.installed_app(app.bundle_id)
        if installed is None:
            return "not_installed", False
        return mac.automation_state(app.bundle_id), app_icons.icon_file(installed) is not None

    async def mac_view(app: MacApp, program: Path) -> ConnectionView:
        """Where ``app`` stands for ``program``; an answer macOS gives about it is kept for when it does not run."""
        live, icon = await asyncio.to_thread(live_state, app)
        key: str = _answer_key(program, app.bundle_id)
        kept: str | None = await store.kv_get(key)
        if live in _KEPT_ANSWERS and live != kept:
            await store.kv_set(key, live)
        state: ConnectionState = mac_state(live, None if kept is None else _KEPT_ANSWERS.get(kept))
        return ConnectionView(
            id=app.id,
            kind="mac",
            name=app.name,
            does=app.does,
            state=state,
            step=mac_step(state),
            waiting=app.id in asking,
            icon=icon,
        )

    def folder_view(app: FolderApp) -> ConnectionView:
        installed: tuple[str, Path] | None = mac.installed_one_of(app.bundle_ids)
        state: ConnectionState = folder_state(installed is not None, len(onedrive_folders(CLOUD_STORAGE)) > 0)
        return ConnectionView(
            id=app.id,
            kind="folder",
            name=app.name,
            does=app.does,
            state=state,
            step=folder_step(state),
            waiting=False,
            icon=installed is not None and app_icons.icon_file(installed[1]) is not None,
        )

    def web_view(app: WebApp, cookies: frozenset[CookieKey]) -> ConnectionView:
        state: ConnectionState = web_state(session.mode, signed_in(app, cookies))
        return ConnectionView(
            id=app.id,
            kind="web",
            name=app.name,
            does=app.does,
            state=state,
            step=web_step(state, session.config.headless),
            waiting=False,
            icon=False,
        )

    async def cookies() -> frozenset[CookieKey]:
        """What Dot's browser holds: asked while it runs, else read from its profile; none in the owner's Chrome."""
        if session.mode == "attach":
            return frozenset()
        live: frozenset[CookieKey] | None = await session.live_cookies()
        if live is not None:
            return live
        return await asyncio.to_thread(profile_cookies, session.profile_dir, time.time())

    def folder_views() -> list[ConnectionView]:
        return [folder_view(app) for app in FOLDER_APPS]

    @router.get("/connections")
    async def list_connections() -> ConnectionsView:
        program: Path = await asyncio.to_thread(perms.responsible_program)
        macs: list[ConnectionView] = [await mac_view(app, program) for app in MAC_APPS]
        folders: list[ConnectionView] = await asyncio.to_thread(folder_views)
        held: frozenset[CookieKey] = await cookies()
        return ConnectionsView(
            apps=(*macs, *folders, *(web_view(app, held) for app in WEB_APPS)),
            browser_mode=session.mode,
            headless=session.config.headless,
            runner=perms.program_name(program),
        )

    def icon_of(app_id: str) -> Path:
        """The ``.icns`` file of the installed application ``app_id`` of the Mac or of a synced folder;
        ``NotFoundError`` for an application of the web, one that is not installed and one whose bundle holds no icon
        file."""
        bundle_ids: tuple[str, ...]
        found_mac: MacApp | None = next((app for app in MAC_APPS if app.id == app_id), None)
        found_folder: FolderApp | None = next((app for app in FOLDER_APPS if app.id == app_id), None)
        if found_mac is not None:
            bundle_ids = (found_mac.bundle_id,)
        elif found_folder is not None:
            bundle_ids = found_folder.bundle_ids
        else:
            raise NotFoundError("Dot lists no such application of this Mac", {"app_id": app_id})
        installed: tuple[str, Path] | None = mac.installed_one_of(bundle_ids)
        if installed is None:
            raise NotFoundError("the application is not installed", {"app_id": app_id, "bundle_ids": list(bundle_ids)})
        icns: Path | None = app_icons.icon_file(installed[1])
        if icns is None:
            raise NotFoundError("the application has no icon file", {"app_id": app_id, "app": str(installed[1])})
        return icns

    @router.get("/connections/{app_id}/icon")
    async def app_icon(app_id: str) -> Response:
        """The icon of the application ``app_id`` as the owner has it installed, a PNG read from its bundle (Apple's
        and Microsoft's artwork is not shipped with Dot); 404 for an application of the web, one that is not installed
        and one with no icon file."""
        icns: Path = await asyncio.to_thread(icon_of, app_id)
        return Response(await asyncio.to_thread(app_icons.icon_png, icns), media_type="image/png")

    @router.get("/plugins")
    async def list_plugins() -> PluginsView:
        """The listings of the Plugins page, each with the ways Dot reaches its service; where each way stands is
        read from ``GET /connections`` and ``GET /mcp/servers``, and its step taken there."""
        return PluginsView(plugins=plugin_views())

    async def ask(app: MacApp) -> None:
        """Ask macOS about ``app`` once at a time, and wait a while for the owner's answer."""
        question: asyncio.Future[mac.AutomationState] | None = asking.get(app.id)
        if question is None:
            question = asyncio.ensure_future(asyncio.to_thread(_first_time, app.bundle_id))
            asking[app.id] = question

            def answered(done: asyncio.Future[mac.AutomationState]) -> None:
                asking.pop(app.id, None)
                if not done.cancelled() and done.exception() is not None:
                    log.warning("automation_question_failed", app=app.id, error=str(done.exception()))

            question.add_done_callback(answered)
        try:
            await asyncio.wait_for(asyncio.shield(question), _ASK_WAIT_S)
        except TimeoutError:
            log.info("automation_question_waits", app=app.id, waited_s=_ASK_WAIT_S)

    @router.post("/connections/{app_id}")
    async def take_step(app_id: str) -> ConnectionView:
        """Take the step of the application ``app_id``: ask macOS about it, show the Automation pane after a no, open
        its sign-in page in Dot's browser, or open the application to sign in there."""
        found_mac: MacApp | None = next((app for app in MAC_APPS if app.id == app_id), None)
        if found_mac is not None:
            program: Path = await asyncio.to_thread(perms.responsible_program)
            current: ConnectionView = await mac_view(found_mac, program)
            if current.step == "settings":
                await asyncio.to_thread(perms.open_settings, "automation")
            elif current.step == "ask":
                await ask(found_mac)
            return await mac_view(found_mac, program)
        found_folder: FolderApp | None = next((app for app in FOLDER_APPS if app.id == app_id), None)
        if found_folder is not None:
            current = await asyncio.to_thread(folder_view, found_folder)
            if current.step == "open_app":
                installed: tuple[str, Path] | None = await asyncio.to_thread(
                    mac.installed_one_of, found_folder.bundle_ids
                )
                if installed is None:
                    raise NotFoundError("the application is not installed", {"app_id": app_id})
                await asyncio.to_thread(mac.open_app, installed[0])
            return current
        found_web: WebApp | None = next((app for app in WEB_APPS if app.id == app_id), None)
        if found_web is None:
            raise NotFoundError("Dot lists no such application", {"app_id": app_id})
        if session.mode == "attach":
            raise RunConflict(
                "Dot works in your own Chrome (Settings › Browser › My Chrome), where you sign in as you always do",
                {"app_id": app_id},
            )
        if session.config.headless:
            raise RunConflict(
                "Dot's browser runs without a window, so there is none to sign in in: switch headless off in the "
                "config",
                {"app_id": app_id},
            )
        # The owner signs in on the page; no model reads its view, which hides as a run's would.
        await session.open_url(_SIGN_IN_THREAD, found_web.sign_in, True)
        await session.focus(_SIGN_IN_THREAD)
        return web_view(found_web, await cookies())

    return router
