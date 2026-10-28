"""Routes for Snapshots (item 405, :mod:`dotagent.desktop.snapshots`): the settings as they stand and the captures not
yet claimed, a setting changed from the page (live, and kept in the config file), a capture taken by hand, and the
claim a composer makes of the captures waiting for it."""

import asyncio

from fastapi import APIRouter, Request

from dotagent.config import SnapshotSound
from dotagent.config_edit import set_setting
from dotagent.core.errors import ConfigError
from dotagent.core.paths import config_path
from dotagent.desktop.snapshots import SHORTCUT_WORDS, SnapshotService
from dotagent.server.context import ServerContext
from dotagent.server.views import SnapshotsClaimed, SnapshotSettingRequest, SnapshotsView


def snapshots_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    service: SnapshotService = ctx.runtime.snapshots
    # One change at a time: a second one waits for the first and finds what it did.
    changing: asyncio.Lock = asyncio.Lock()

    def view() -> SnapshotsView:
        return SnapshotsView(
            enabled=service.enabled,
            app_text=service.app_text,
            sound=service.sound,
            animations=service.animations,
            flash=service.flash,
            open_dot=service.open_dot,
            shortcut=SHORTCUT_WORDS,
            listening=service.listening,
            problem=service.problem,
            pending=service.pending(),
        )

    @router.get("/snapshots")
    async def snapshots() -> SnapshotsView:
        return view()

    @router.post("/snapshots/settings")
    async def change(request: SnapshotSettingRequest) -> SnapshotsView:
        """Change one setting: it holds at once, and is kept in the config file; a config file that cannot be
        changed leaves the setting as it was."""
        async with changing:
            before: bool | SnapshotSound = getattr(service, request.key)
            await service.apply(request.key, request.value)
            try:
                await asyncio.to_thread(set_setting, config_path(), "snapshots", request.key, request.value)
            except (ConfigError, OSError):
                await service.apply(request.key, before)
                raise
        return view()

    @router.post("/snapshots/take")
    async def take() -> SnapshotsView:
        """Capture the window in front now, as the double tap does; the capture waits to be claimed."""
        await service.take()
        return view()

    @router.post("/snapshots/claim")
    async def claim(request: Request) -> SnapshotsClaimed:
        """The captures waiting, for the composer that asks: they are its from now. Its browser (the User-Agent) is
        where Dot's tab is looked for first after the next capture (item 414)."""
        return SnapshotsClaimed(files=service.claim(request.headers.get("user-agent")), animations=service.animations)

    return router
