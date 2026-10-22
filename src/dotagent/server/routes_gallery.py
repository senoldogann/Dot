"""Customize › Plugins (item 542): the plugins of the gallery, whether each is installed, and Install, which downloads
the pinned commit of its repository and offers its skills to the runs that start after
(:mod:`dotagent.plugins.gallery`). Remove is ``DELETE /installed-plugins/{name}`` (item 497), which moves the folder to
the Trash."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter

from dotagent.core.errors import GalleryError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import plugins_dir
from dotagent.plugins.catalog import read_again
from dotagent.plugins.gallery import (
    GalleryEntry,
    GalleryMarker,
    download_archive,
    homepage,
    install,
    installed_marker,
)
from dotagent.plugins.gallery_list import GALLERY
from dotagent.plugins.removal import is_dots_own
from dotagent.plugins.types import Plugin
from dotagent.server.context import ServerContext
from dotagent.server.views import GalleryPluginView, GalleryState, GalleryView, InstalledPluginView

log = get_logger(__name__)


def _state(entry: GalleryEntry, plugins: Sequence[Plugin]) -> GalleryState:
    """Whether Dot installed ``entry``, has another plugin of its name, or can install it."""
    folder: Path = plugins_dir() / entry.id
    marker: GalleryMarker | None = installed_marker(folder)
    if marker is not None and marker.id == entry.id:
        return "installed"
    if folder.exists() or folder.is_symlink() or any(plugin.name == entry.id for plugin in plugins):
        return "taken"
    return "available"


def gallery_view(entries: Sequence[GalleryEntry], plugins: Sequence[Plugin]) -> GalleryView:
    """The gallery's plugins with where each stands, and the plugins Dot reads that are not the gallery's."""
    own: Path = plugins_dir()
    views: tuple[GalleryPluginView, ...] = tuple(
        GalleryPluginView(
            id=entry.id,
            name=entry.name,
            publisher=entry.publisher,
            official=entry.official,
            category=entry.category,
            does=entry.does,
            skills=tuple(skill.rsplit("/", 1)[-1] for skill in entry.skills),
            licence=entry.licence,
            featured=entry.featured,
            stars=entry.stars,
            added=entry.added.isoformat(),
            logo=entry.logo,
            repository=entry.repository,
            commit=entry.commit,
            homepage=homepage(entry),
            state=_state(entry, plugins),
        )
        for entry in entries
    )
    others: tuple[InstalledPluginView, ...] = tuple(
        InstalledPluginView(
            name=plugin.name,
            description=plugin.description,
            servers=len(plugin.servers),
            own=is_dots_own(plugin, own),
        )
        for plugin in plugins
        if plugin.root.is_dir() and installed_marker(plugin.root) is None
    )
    return GalleryView(plugins=views, others=others)


def gallery_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One install at a time: two of the same plugin would race for its folder.
    installing: asyncio.Lock = asyncio.Lock()

    @router.get("/plugin-gallery")
    async def list_gallery() -> GalleryView:
        """The plugins of the gallery in its order, each with where it stands, and the other plugins Dot reads."""
        return await asyncio.to_thread(gallery_view, GALLERY, ctx.runtime.plugins.plugins)

    @router.post("/plugin-gallery/{plugin_id}")
    async def install_plugin(plugin_id: str) -> GalleryView:
        """Install the gallery's plugin ``plugin_id``: its pinned commit is downloaded, its skills checked against the
        hash the gallery keeps and put in Dot's plugins folder, and the runs that start after are offered them. 409
        when Dot has a plugin of that name already, or the plugin cannot be downloaded or is not the one pinned."""
        entry: GalleryEntry | None = next((one for one in GALLERY if one.id == plugin_id), None)
        if entry is None:
            raise NotFoundError("Dot's gallery has no plugin of this name", {"plugin": plugin_id})
        async with installing:
            # A plugin Dot reads under the name, from a folder of the config or one whose folder went while servers it
            # started run, keeps it: those servers would be credited to the plugin installed.
            if any(plugin.name == entry.id for plugin in ctx.runtime.plugins.plugins):
                raise GalleryError(f"Dot already has a plugin named {entry.id}", {"plugin": entry.id})
            await install(entry, plugins_dir(), download_archive)
            await read_again(ctx.runtime.plugins, ctx.runtime.config.plugins, ctx.runtime.config.mcp)
        log.info("plugin_installed", plugin=entry.id, repository=entry.repository, commit=entry.commit)
        return await list_gallery()

    return router
