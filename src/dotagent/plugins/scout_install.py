"""The owner's word on a proposal of the plugin scout (item 712): install a new plugin, update one, or put back the
version an update replaced.

Each goes through the gallery's install (:func:`dotagent.plugins.gallery.install`): the archive of the pinned commit,
only the skills' folders, and the hash the scout scanned and had reviewed; an archive whose files differ installs
nothing. An update keeps the version it replaces in ``<home>/plugins/.previous/<id>`` (a hidden folder, which Dot does
not read as a plugin), from where Revert puts it back; the version Revert takes out goes to the Trash, as Remove's
does. A failure leaves the plugin as it was.
"""

import asyncio
from datetime import date
from pathlib import Path

from dotagent.core.errors import GalleryError
from dotagent.core.logs import get_logger
from dotagent.plugins import removal
from dotagent.plugins.gallery import ArchiveFetch, GalleryEntry, GalleryMarker, install, installed_marker
from dotagent.plugins.scout_types import Candidate, candidate_entry

log = get_logger(__name__)

PREVIOUS_DIR: str = ".previous"


def previous_folder(plugins: Path, plugin_id: str) -> Path:
    return plugins / PREVIOUS_DIR / plugin_id


def _does(candidate: Candidate) -> str:
    return candidate.review.does if candidate.review is not None and candidate.review.does else candidate.name


async def install_new(candidate: Candidate, plugins: Path, fetch: ArchiveFetch, today: date) -> Path:
    """Install the new plugin ``candidate`` proposes into ``plugins``; where it is."""
    if candidate.kind != "new":
        raise GalleryError("this proposal is an update, not a new plugin", {"plugin": candidate.id})
    entry: GalleryEntry = candidate_entry(candidate, _does(candidate), today)
    return await install(entry, plugins, fetch)


def _set_aside(current: Path, previous: Path) -> None:
    """Move the installed version ``current`` to ``previous``, the one kept there before going to the Trash."""
    if previous.exists():
        removal.move_to_trash(previous, removal.trash_folder())
    previous.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current.rename(previous)


async def install_update(candidate: Candidate, plugins: Path, fetch: ArchiveFetch, today: date) -> Path:
    """Update the installed plugin ``candidate`` names to its commit: the installed version, which must still be the
    one the update was found against, is kept in :func:`previous_folder`, and put back when the install fails."""
    if candidate.kind != "update":
        raise GalleryError("this proposal is a new plugin, not an update", {"plugin": candidate.id})
    current: Path = plugins / candidate.id
    marker: GalleryMarker | None = await asyncio.to_thread(installed_marker, current)
    if marker is None or marker.commit != candidate.previous:
        raise GalleryError(
            "the installed plugin is not the version this update was found against; the next night looks again",
            {"plugin": candidate.id, "installed": marker.commit if marker else None, "expected": candidate.previous},
        )
    previous: Path = previous_folder(plugins, candidate.id)
    await asyncio.to_thread(_set_aside, current, previous)
    try:
        return await install(candidate_entry(candidate, _does(candidate), today), plugins, fetch)
    except BaseException:
        await asyncio.to_thread(_put_back, previous, current)
        raise


def _put_back(previous: Path, current: Path) -> None:
    """After an update that failed: the version set aside goes back where it was, unless the new one got there."""
    if not current.exists() and previous.exists():
        previous.rename(current)


def _revert(plugins: Path, plugin_id: str) -> GalleryMarker:
    current: Path = plugins / plugin_id
    previous: Path = previous_folder(plugins, plugin_id)
    marker: GalleryMarker | None = installed_marker(previous)
    if marker is None:
        raise GalleryError("there is no earlier version of this plugin to put back", {"plugin": plugin_id})
    if current.exists():
        removal.move_to_trash(current, removal.trash_folder())
    previous.rename(current)
    return marker


async def revert(plugins: Path, plugin_id: str) -> GalleryMarker:
    """Put back the version of ``plugin_id`` an update replaced; what its marker says. The version taken out goes to
    the Trash."""
    marker: GalleryMarker = await asyncio.to_thread(_revert, plugins, plugin_id)
    log.info("scout_update_reverted", plugin=plugin_id, commit=marker.commit)
    return marker


def has_previous(plugins: Path, plugin_id: str) -> bool:
    return installed_marker(previous_folder(plugins, plugin_id)) is not None
