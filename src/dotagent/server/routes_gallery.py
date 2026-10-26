"""Customize › Plugins (item 542): the plugins of the gallery, whether each is installed, and Install, which downloads
the pinned commit of its repository and offers its skills to the runs that start after
(:mod:`dotagent.plugins.gallery`). Remove is ``DELETE /installed-plugins/{name}`` (item 497), which moves the folder to
the Trash.

The plugin scout's proposals (item 712, :mod:`dotagent.plugins.scout`) are here too: what it found waits for the
owner's Install, Update or Skip, a night can be asked for now, and an update taken can be reverted. A plugin the owner
installed from a proposal joins the gallery's list; one updated from a proposal stands as ``updated``."""

import asyncio
import contextlib
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter

from dotagent.core.errors import GalleryError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path, plugins_dir
from dotagent.plugins import scout_service
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
from dotagent.plugins.scout import ScoutStopped
from dotagent.plugins.scout_install import has_previous, install_new, install_update, revert
from dotagent.plugins.scout_types import Candidate, Review, ScoutState, candidate_entry, retired, waiting
from dotagent.plugins.types import Plugin
from dotagent.server.context import ServerContext
from dotagent.server.views import (
    GalleryPluginView,
    GalleryState,
    GalleryView,
    InstalledPluginView,
    ScoutCandidateView,
    ScoutFindingView,
    ScoutView,
)

log = get_logger(__name__)


def _installed(entry: GalleryEntry) -> GalleryMarker | None:
    """What Dot kept of installing ``entry``: the marker in the folder of its name, none for another plugin's folder."""
    marker: GalleryMarker | None = installed_marker(plugins_dir() / entry.id)
    return marker if marker is not None and marker.id == entry.id else None


def _state(
    entry: GalleryEntry, marker: GalleryMarker | None, plugins: Sequence[Plugin], updated: frozenset[tuple[str, str]]
) -> GalleryState:
    """Whether Dot installed ``entry`` (``marker``), and from the commit the gallery pins now, from a newer one the
    owner took from the scout (``updated``: the plugin's id and commit of each update taken), or an older one (the pin
    moved with a newer Dot: the runs read the old skills, and Install is refused until Remove), has another plugin of
    its name, or can install it."""
    if marker is not None:
        if (marker.commit, marker.files) == (entry.commit, entry.files):
            return "installed"
        return "updated" if (entry.id, marker.commit) in updated else "outdated"
    folder: Path = plugins_dir() / entry.id
    if folder.exists() or folder.is_symlink() or any(plugin.name == entry.id for plugin in plugins):
        return "taken"
    return "available"


def _plugin_view(
    entry: GalleryEntry, plugins: Sequence[Plugin], updated: frozenset[tuple[str, str]], scouted: frozenset[str]
) -> GalleryPluginView:
    """``entry`` as the page shows it, with where it stands."""
    marker: GalleryMarker | None = _installed(entry)
    return GalleryPluginView(
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
        state=_state(entry, marker, plugins, updated),
        installed_commit=None if marker is None else marker.commit,
        revertible=marker is not None
        and (entry.id, marker.commit) in updated
        and has_previous(plugins_dir(), entry.id),
        scouted=entry.id in scouted,
    )


def scouted_entries(state: ScoutState) -> tuple[GalleryEntry, ...]:
    """The plugins the owner installed from the scout's proposals, as the gallery lists them."""
    return tuple(
        candidate_entry(candidate, candidate.review.does if candidate.review else candidate.name, _day(candidate))
        for candidate in state.candidates
        if candidate.kind == "new" and candidate.state == "installed"
    )


def _day(candidate: Candidate) -> date:
    return datetime.fromtimestamp(candidate.decided_at or candidate.found_at, UTC).date()


def gallery_view(entries: Sequence[GalleryEntry], plugins: Sequence[Plugin], state: ScoutState) -> GalleryView:
    """The gallery's plugins (and those installed from the scout's proposals) with where each stands, and the plugins
    Dot reads that are not the gallery's."""
    own: Path = plugins_dir()
    updated: frozenset[tuple[str, str]] = frozenset(
        (candidate.id, candidate.commit)
        for candidate in state.candidates
        if candidate.kind == "update" and candidate.state == "installed"
    )
    scouted: tuple[GalleryEntry, ...] = scouted_entries(state)
    listed: tuple[GalleryEntry, ...] = (*entries, *(one for one in scouted if one.id not in {e.id for e in entries}))
    views: tuple[GalleryPluginView, ...] = tuple(
        _plugin_view(entry, plugins, updated, frozenset(one.id for one in scouted)) for entry in listed
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
        state: ScoutState = await scout_service.load(ctx.runtime.store)
        return await asyncio.to_thread(gallery_view, GALLERY, ctx.runtime.plugins.plugins, state)

    @router.post("/plugin-gallery/{plugin_id}")
    async def install_plugin(plugin_id: str) -> GalleryView:
        """Install the gallery's plugin ``plugin_id``: its pinned commit is downloaded, its skills checked against the
        hash the gallery keeps and put in Dot's plugins folder, and the runs that start after are offered them. 409
        when Dot has a plugin of that name already, or the plugin cannot be downloaded or is not the one pinned. A
        plugin the owner installed from the scout's proposals, and removed, is the gallery's too: it installs again at
        the commit and with the files the scout checked."""
        state: ScoutState = await scout_service.load(ctx.runtime.store)
        listed: tuple[GalleryEntry, ...] = (*GALLERY, *scouted_entries(state))
        entry: GalleryEntry | None = next((one for one in listed if one.id == plugin_id), None)
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

    looking: set[asyncio.Task[ScoutState]] = set()

    def under_way() -> bool:
        """Whether a night runs, or one the owner asked for is on its way to its lock."""
        return scout_service.running(ctx.runtime) or any(not task.done() for task in looking)

    async def scout_view() -> ScoutView:
        state: ScoutState = await scout_service.load(ctx.runtime.store)
        now: float = time.time()
        # An update that no longer applies (its plugin was reverted, removed or installed again) is not shown: the
        # night's end marks it failed (scout_types.retired).
        shown: ScoutState = await asyncio.to_thread(retired, state, plugins_dir())
        views: list[ScoutCandidateView] = [
            candidate_view(candidate.model_copy(update={"state": "proposed"}) if waiting(candidate, now) else candidate)
            for candidate in shown.candidates
        ]
        return ScoutView(
            proposals=tuple(view for view in views if view.state == "proposed"),
            refused=tuple(view for view in views if view.state == "refused"),
            last_run=state.last_run,
            last_summary=state.last_summary,
            running=under_way(),
            time=ctx.runtime.config.scout.time,
        )

    @router.get("/plugin-scout")
    async def get_scout() -> ScoutView:
        """The scout's proposals waiting for the owner, those its review refused, and its last night."""
        return await scout_view()

    @router.post("/plugin-scout/look")
    async def look_now() -> ScoutView:
        """Start a night of the scout now, in the background; the page asks again until it is over. One under way is
        not started twice."""
        if await asyncio.to_thread(failsafe_path().exists):
            raise ScoutStopped("the emergency stop is engaged: release it, then look again", {})
        if not under_way():
            task: asyncio.Task[ScoutState] = asyncio.create_task(
                scout_service.night(ctx.runtime, time.time()), name="plugin-scout-now"
            )
            looking.add(task)
            task.add_done_callback(_looked)
            task.add_done_callback(looking.discard)
            await asyncio.sleep(0)
        return await scout_view()

    async def decided(plugin_id: str, kind: str, change: dict[str, object], installing: bool = False) -> Candidate:
        """The proposal ``plugin_id`` of ``kind`` changed by ``change``, read and written back under the state's lock,
        as a night's end writes it; what it was before. It must be waiting (:func:`waiting`), or with ``installing``
        an install under way; 404 when it is not, as when another tab took it first."""
        async with scout_service.state_lock(ctx.runtime):
            state: ScoutState = await scout_service.load(ctx.runtime.store)
            now: float = time.time()
            found: Candidate | None = next(
                (
                    one
                    for one in state.candidates
                    if one.id == plugin_id
                    and one.kind == kind
                    and (one.state == "installing" if installing else waiting(one, now))
                ),
                None,
            )
            if found is None:
                raise NotFoundError("the scout has no such proposal waiting", {"plugin": plugin_id, "kind": kind})
            changed: Candidate = found.model_copy(update={**change, "decided_at": now})
            kept: ScoutState = state.model_copy(
                update={"candidates": tuple(changed if one is found else one for one in state.candidates)}
            )
            await scout_service.save(ctx.runtime.store, kept)
            return found

    @router.post("/plugin-scout/{plugin_id}/install")
    async def install_proposal(plugin_id: str, kind: str = "new") -> ScoutView:
        """Install the plugin the scout proposed, or take the update it proposed: the commit it pinned and the files
        it scanned and had reviewed, nothing else; an update keeps the version it replaces for Revert. 409 when it
        cannot be installed (the files differ, the name is taken, the installed version moved). The proposal is taken
        under the state's lock first (``installing``), so that a Skip or an Install of another tab meanwhile finds none
        waiting; one whose install fails waits again, and one installed is marked so before the plugins are read again
        (the files are in place whether or not that reading goes well)."""
        candidate: Candidate = await decided(plugin_id, kind, {"state": "installing"})
        async with installing:
            try:
                if kind == "new":
                    if any(plugin.name == candidate.id for plugin in ctx.runtime.plugins.plugins):
                        raise GalleryError(f"Dot already has a plugin named {candidate.id}", {"plugin": candidate.id})
                    await install_new(candidate, plugins_dir(), download_archive, scout_service.today_of(ctx.runtime))
                else:
                    await install_update(
                        candidate, plugins_dir(), download_archive, scout_service.today_of(ctx.runtime)
                    )
            except BaseException:
                # One that another tab took meanwhile (its install left off for longer than the grace) is theirs: the
                # error said is the install's own.
                with contextlib.suppress(NotFoundError):
                    await decided(plugin_id, kind, {"state": "proposed"}, installing=True)
                raise
            await decided(plugin_id, kind, {"state": "installed"}, installing=True)
            await read_again(ctx.runtime.plugins, ctx.runtime.config.plugins, ctx.runtime.config.mcp)
        log.info("scout_proposal_installed", plugin=plugin_id, kind=kind, commit=candidate.commit)
        return await scout_view()

    @router.post("/plugin-scout/{plugin_id}/skip")
    async def skip_proposal(plugin_id: str, kind: str = "new") -> ScoutView:
        """Skip the proposal: it is not shown again, and the scout does not propose that plugin again (an update: not
        that commit)."""
        await decided(plugin_id, kind, {"state": "skipped"})
        log.info("scout_proposal_skipped", plugin=plugin_id, kind=kind)
        return await scout_view()

    @router.post("/plugin-gallery/{plugin_id}/revert")
    async def revert_plugin(plugin_id: str) -> GalleryView:
        """Put back the version of ``plugin_id`` an update from the scout replaced; the version taken out goes to the
        Trash. 409 when there is no earlier version kept, or the installed version is not an update the owner took
        (one installed again after a Remove: the version kept was replaced by another)."""
        async with installing:
            taken_back: GalleryMarker | None = await asyncio.to_thread(installed_marker, plugins_dir() / plugin_id)
            taken: ScoutState = await scout_service.load(ctx.runtime.store)
            if taken_back is None or not any(
                one.id == plugin_id
                and one.kind == "update"
                and one.state == "installed"
                and one.commit == taken_back.commit
                for one in taken.candidates
            ):
                raise GalleryError(
                    "the installed version of this plugin is not an update Dot took", {"plugin": plugin_id}
                )
            marker: GalleryMarker = await revert(plugins_dir(), plugin_id)
            await read_again(ctx.runtime.plugins, ctx.runtime.config.plugins, ctx.runtime.config.mcp)
            async with scout_service.state_lock(ctx.runtime):
                state: ScoutState = await scout_service.load(ctx.runtime.store)
                await scout_service.save(
                    ctx.runtime.store,
                    await asyncio.to_thread(
                        retired,
                        state.model_copy(
                            update={
                                "candidates": tuple(
                                    _after_revert(one, plugin_id, taken_back) for one in state.candidates
                                )
                            }
                        ),
                        plugins_dir(),
                    ),
                )
        log.info("scout_update_reverted_by_owner", plugin=plugin_id, commit=marker.commit)
        return await list_gallery()

    return router


def _after_revert(one: Candidate, plugin_id: str, taken_back: GalleryMarker) -> Candidate:
    """``one`` once the update of ``plugin_id`` to ``taken_back`` was reverted: that update skipped (one taken before it
    is the version now installed). An update proposed from the version taken back no longer applies, and
    :func:`retired` marks it failed (the review of the ninth push); the next night proposes one from the version put
    back."""
    if one.id == plugin_id and one.kind == "update" and one.state == "installed" and one.commit == taken_back.commit:
        return one.model_copy(update={"state": "skipped"})
    return one


def _looked(task: "asyncio.Task[ScoutState]") -> None:
    """A night the owner asked for that failed is logged: the page shows the last night's summary."""
    if task.cancelled():
        return
    error: BaseException | None = task.exception()
    if isinstance(error, ScoutStopped):
        # The emergency stop, as the scheduler's night logs it: not a failure.
        log.info("scout_stopped", error=error.message)
    elif error is not None:
        log.error("scout_now_failed", error=f"{type(error).__name__}: {error}")


def candidate_view(candidate: Candidate) -> ScoutCandidateView:
    review: Review | None = candidate.review
    shared: str = candidate.skills[0].rsplit("/", 2)[0] if len(candidate.skills) == 1 else ""
    return ScoutCandidateView(
        id=candidate.id,
        kind=candidate.kind,
        name=candidate.name,
        publisher=candidate.publisher,
        tier=candidate.tier,
        source=candidate.source,
        category=candidate.category,
        repository=candidate.repository,
        commit=candidate.commit,
        homepage=f"https://github.com/{candidate.repository}/tree/{candidate.commit}"
        + (f"/{shared}" if shared and shared != candidate.skills[0] else ""),
        skills=tuple(skill.rsplit("/", 1)[-1] for skill in candidate.skills),
        licence=candidate.licence,
        stars=candidate.stars,
        pushed=candidate.pushed.isoformat(),
        risk=None if review is None else review.risk,
        refuse=review is not None and review.refuse,
        does=review.does if review else "",
        why=review.why if review else "",
        fit=round(candidate.fit, 2),
        findings=tuple(
            ScoutFindingView(rule=one.rule, severity=one.severity, path=one.path, detail=one.detail)
            for one in candidate.findings
        ),
        previous=candidate.previous,
        changed=candidate.changed,
        state=candidate.state,
        found_at=candidate.found_at,
        failure=candidate.failure,
    )
