"""What the skills that come with Dot for Office files and OneDrive find on this Mac, for ``dotx doctor`` (item 196):
the applications they drive, each with the version its Info.plist gives, and the folders OneDrive syncs, told by the
kind of account each belongs to. Nothing is started, and a folder is never named: the folder of a work or school
account carries the name of the organization."""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotagent.help.apps import bundle_info

type OneDriveKind = Literal["personal", "work or school", "shared libraries"]

# The applications the skills drive, as their bundles are named.
OFFICE_APPS: tuple[str, ...] = ("Microsoft Excel", "Microsoft Word", "LibreOffice")
ONEDRIVE_APP: str = "OneDrive"
# Where macOS keeps the folders of the cloud drives (OneDrive, Google Drive, Dropbox) since macOS 12.1.
CLOUD_STORAGE: Path = Path.home() / "Library" / "CloudStorage"
_PERSONAL: str = "OneDrive-Personal"
_SHARED: str = "OneDrive-SharedLibraries-"
_ONEDRIVE: str = "OneDrive-"


def app_version(roots: Sequence[Path], name: str) -> str | None:
    """The version of the application called ``name`` in the first of ``roots`` that holds it, empty when its
    Info.plist gives none; ``None`` when no root holds it."""
    for root in roots:
        info: dict[str, object] | None = bundle_info(root / f"{name}.app")
        if info is not None:
            version: object = info.get("CFBundleShortVersionString")
            return version if isinstance(version, str) else ""
    return None


def onedrive_kind(folder: str) -> OneDriveKind | None:
    """The kind of account a folder of ``~/Library/CloudStorage`` called ``folder`` belongs to; ``None`` for a folder of
    another cloud drive."""
    if folder == _PERSONAL:
        return "personal"
    if folder.startswith(_SHARED):
        return "shared libraries"
    if folder.startswith(_ONEDRIVE):
        return "work or school"
    return None


def onedrive_folders(cloud: Path) -> tuple[OneDriveKind, ...]:
    """The kind of each OneDrive folder in ``cloud``, in the order of their names; none when ``cloud`` does not exist.
    Only the names in ``cloud`` are read, not what the folders hold."""
    if not cloud.is_dir():
        return ()
    kinds: list[OneDriveKind | None] = [
        onedrive_kind(child.name) for child in sorted(cloud.iterdir()) if child.is_dir()
    ]
    return tuple(kind for kind in kinds if kind is not None)
