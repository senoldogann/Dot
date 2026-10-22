"""Plugins Dot installs from their own repositories (item 542), as Customize › Plugins lists them.

An entry of the gallery names a repository on GitHub, a commit of it, the skill folders Dot keeps of it and a hash of
their files. Installing it downloads the archive of that commit, unpacks only those folders into a plugin folder of
Dot's own (``<home>/plugins/<id>``, in the Claude Code layout: ``.claude-plugin/plugin.json`` and
``skills/<name>/``), checks the hash and moves the folder into place: what the runs read is what was pinned, whatever
the repository holds later. The plugin's hooks, commands, agents and MCP servers are not taken, as Dot uses only the
skills of a gallery plugin; its hidden files are left out, as when a skill is added from another agent.

The archive is unpacked by hand, member by member: a member outside the folders kept is skipped, and one inside them
that is a link, or neither a file nor a folder, or that would land outside the plugin, stops the install, as does a
plugin over :data:`dotagent.help.elsewhere.SKILL_COPY_LIMIT`. Nothing is left behind by an install that stops: it works
in a hidden folder beside Dot's plugins, which the catalog does not read, and moves the plugin out of it only once the
hash matched. Removing a plugin is Customize's Remove plugin (item 497): its folder goes to the Trash.
"""

import asyncio
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO, Literal

import httpx
from pydantic import Field, ValidationError

from dotagent.core.errors import GalleryError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.help.elsewhere import SKILL_COPY_LIMIT, CopyLimit
from dotagent.plugins.types import MANIFEST_DIR, MANIFEST_FILE, SKILLS_DIR

log = get_logger(__name__)

# Dot's note in a plugin it installed from the gallery: which entry, from where, and the hash of what it kept.
GALLERY_MARKER: str = "gallery.json"
ARCHIVE_URL: str = "https://codeload.github.com/{repository}/tar.gz/{commit}"
# The archive of a whole repository at one commit, of which only some folders are kept. The largest of the gallery's,
# anthropics/claude-plugins-official, is under 20 MB.
ARCHIVE_LIMIT_BYTES: int = 100 * 2**20
DOWNLOAD_TIMEOUT_S: float = 120.0
_CHUNK_BYTES: int = 1 << 16
_FILE_MODE: int = 0o644
_SCRIPT_MODE: int = 0o755

type GalleryCategory = Literal["development", "design", "product", "data", "business", "writing"]
type ArchiveFetch = Callable[["GalleryEntry", Path], Awaitable[Path]]


class GalleryEntry(FrozenModel):
    """One plugin of the gallery: what the page shows of it and where Dot takes it from. ``commit`` is the commit of
    ``repository`` Dot installs, ``skills`` the folders of it (each holding a ``SKILL.md``) it keeps under
    ``skills/<their own name>``, and ``files`` the hash of what it keeps (:func:`files_hash`). ``official`` says the
    publisher makes what the skills are about, or the agent they were written for; ``stars`` are the repository's
    stars when it was pinned (the Popular order) and ``added`` the day it joined the gallery (the Newest order)."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    name: str = Field(min_length=1, max_length=40)
    publisher: str = Field(min_length=1, max_length=40)
    official: bool
    category: GalleryCategory
    does: str = Field(min_length=1, max_length=160)
    repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    skills: tuple[str, ...] = Field(min_length=1)
    files: str = Field(pattern=r"^[0-9a-f]{64}$")
    licence: str = Field(min_length=1)
    featured: bool = False
    stars: int = Field(ge=0)
    added: date
    logo: str | None = None


class GalleryMarker(FrozenModel):
    """What :data:`GALLERY_MARKER` says of an installed plugin."""

    id: str
    repository: str
    commit: str
    files: str


def archive_url(entry: GalleryEntry) -> str:
    return ARCHIVE_URL.format(repository=entry.repository, commit=entry.commit)


def homepage(entry: GalleryEntry) -> str:
    """The page of the pinned commit on GitHub, at the folder the skills share when they share one."""
    shared: str = os.path.commonpath(entry.skills) if len(entry.skills) > 1 else entry.skills[0]
    path: str = "" if shared in ("", ".") else f"/{shared}"
    return f"https://github.com/{entry.repository}/tree/{entry.commit}{path}"


def skill_folder_name(path: str) -> str:
    """The name of the folder a skill of ``skills`` gets in the plugin: the last part of its path."""
    return PurePosixPath(path).name


async def download_archive(entry: GalleryEntry, folder: Path) -> Path:
    """The archive of ``entry``'s commit, written into ``folder``. Raises GalleryError when GitHub does not hand it
    over, or it is larger than :data:`ARCHIVE_LIMIT_BYTES`."""
    target: Path = folder / "archive.tar.gz"
    context: dict[str, str] = {"plugin": entry.id, "repository": entry.repository, "commit": entry.commit}
    try:
        async with (
            httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client,
            client.stream("GET", archive_url(entry)) as response,
        ):
            if response.status_code != httpx.codes.OK:
                raise GalleryError(f"GitHub answered {response.status_code} for the plugin's archive", context)
            handle: BinaryIO = await asyncio.to_thread(target.open, "xb")
            try:
                size: int = 0
                async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > ARCHIVE_LIMIT_BYTES:
                        raise GalleryError(
                            f"the plugin's archive is larger than {ARCHIVE_LIMIT_BYTES // 2**20} MB", context
                        )
                    await asyncio.to_thread(handle.write, chunk)
            finally:
                await asyncio.to_thread(handle.close)
    except httpx.HTTPError as error:
        raise GalleryError(f"the plugin's archive could not be downloaded ({type(error).__name__})", context) from error
    return target


def _write_member(source: IO[bytes], target: Path, executable: bool) -> int:
    """Copy one file of the archive to ``target``; its size."""
    size: int = 0
    with target.open("xb") as handle:
        while chunk := source.read(_CHUNK_BYTES):
            size += len(chunk)
            handle.write(chunk)
    target.chmod(_SCRIPT_MODE if executable else _FILE_MODE)
    return size


def _kept_place(entry: GalleryEntry, inside: PurePosixPath) -> PurePosixPath | None:
    """Where the member at ``inside`` (its path in the repository) goes in the plugin: ``skills/<name>/<rest>`` for one
    under a folder of ``entry.skills``, its hidden entries left out; None for a member Dot does not keep."""
    for path in entry.skills:
        folder: PurePosixPath = PurePosixPath(path)
        if inside.parts[: len(folder.parts)] != folder.parts:
            continue
        rest: tuple[str, ...] = inside.parts[len(folder.parts) :]
        if any(part.startswith(".") for part in rest):
            return None
        return PurePosixPath(SKILLS_DIR, folder.name, *rest)
    return None


def unpack(entry: GalleryEntry, archive: Path, plugin: Path, limit: CopyLimit = SKILL_COPY_LIMIT) -> None:
    """Write the plugin of ``entry`` into the new folder ``plugin`` from ``archive``: its skills, its manifest and the
    marker of where it came from. Raises GalleryError when the archive cannot be read, holds a link or something that
    is neither a file nor a folder where Dot keeps it, a path that leads out of the plugin, lacks a skill, or is over
    ``limit``."""
    context: dict[str, str] = {"plugin": entry.id, "repository": entry.repository}
    files: int = 0
    size: int = 0
    plugin.mkdir(mode=0o755)
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                path: PurePosixPath = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise GalleryError("the plugin's archive holds a path that leads out of its folder", context)
                # GitHub's archive holds one top folder, <repository>-<commit>, and what is beside it is no folder
                # of the repository.
                if len(path.parts) < 2:
                    continue
                place: PurePosixPath | None = _kept_place(entry, PurePosixPath(*path.parts[1:]))
                if place is None:
                    continue
                target: Path = plugin / place
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    raise GalleryError("the plugin holds a link, which Dot does not install", context)
                if not member.isreg():
                    raise GalleryError("the plugin holds something that is neither a file nor a folder", context)
                files += 1
                size += member.size
                if files > limit.files or size > limit.bytes:
                    raise GalleryError(
                        f"the plugin is too big to install (at most {limit.files} files and {limit.bytes // 2**20} MB)",
                        context,
                    )
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source: IO[bytes] | None = tar.extractfile(member)
                if source is None:
                    raise GalleryError("a file of the plugin's archive cannot be read", context)
                with source:
                    _write_member(source, target, bool(member.mode & stat.S_IXUSR))
    except (tarfile.TarError, EOFError, OSError) as error:
        raise GalleryError(f"the plugin's archive cannot be read ({type(error).__name__})", context) from error
    for skill in entry.skills:
        if not (plugin / SKILLS_DIR / skill_folder_name(skill) / "SKILL.md").is_file():
            raise GalleryError(f"the plugin's archive has no skill at {skill}", {**context, "skill": skill})
    manifest: Path = plugin / MANIFEST_DIR
    manifest.mkdir(mode=0o755)
    (manifest / MANIFEST_FILE).write_text(
        json.dumps({"name": entry.id, "description": entry.does, "version": entry.commit[:12]}, indent=2) + "\n",
        encoding="utf-8",
    )
    marker: GalleryMarker = GalleryMarker(
        id=entry.id, repository=entry.repository, commit=entry.commit, files=entry.files
    )
    (manifest / GALLERY_MARKER).write_text(marker.model_dump_json(indent=2) + "\n", encoding="utf-8")


def files_hash(skills: Path) -> str:
    """The SHA-256 of the files under ``skills``: of each file's path, whether it runs, and its own SHA-256, in the
    order of their paths. Raises OSError when a file cannot be read."""
    entries: list[tuple[str, bool, str]] = []
    for root, folders, names in os.walk(skills):
        folders.sort()
        for name in names:
            path: Path = Path(root) / name
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK_BYTES):
                    digest.update(chunk)
            runs: bool = bool(os.lstat(path).st_mode & stat.S_IXUSR)
            entries.append((path.relative_to(skills).as_posix(), runs, digest.hexdigest()))
    total = hashlib.sha256()
    for relative, runs, digest_hex in sorted(entries):
        total.update(f"{relative}\0{'x' if runs else '-'}\0{digest_hex}\n".encode())
    return total.hexdigest()


def _discard(tree: Path) -> None:
    """Delete ``tree``, a folder an install made, whatever modes its folders were given."""
    for root, _folders, _names in os.walk(tree):
        os.chmod(root, stat.S_IMODE(os.lstat(root).st_mode) | stat.S_IRWXU)
    shutil.rmtree(tree, ignore_errors=True)


def _refuse_taken(entry: GalleryEntry, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise GalleryError(f"Dot's plugins folder already holds {entry.id}", {"plugin": entry.id})


def _settle(entry: GalleryEntry, made: Path, target: Path) -> None:
    """Move the plugin unpacked at ``made`` to ``target`` once its files are the ones pinned. Raises GalleryError when
    they are not, cannot be read, or ``target`` was taken meanwhile."""
    context: dict[str, str] = {"plugin": entry.id, "commit": entry.commit}
    try:
        found: str = files_hash(made / SKILLS_DIR)
    except OSError as error:
        raise GalleryError(f"the plugin's files cannot be read ({type(error).__name__})", context) from error
    if found != entry.files:
        raise GalleryError(
            "the plugin's files are not the ones pinned in Dot's gallery, so it is not installed", context
        )
    _refuse_taken(entry, target)
    made.rename(target)


async def install(entry: GalleryEntry, folder: Path, fetch: ArchiveFetch = download_archive) -> Path:
    """Install ``entry`` into Dot's plugins folder ``folder``: its archive fetched with ``fetch``, its skills unpacked
    and checked against ``entry.files``, then moved into ``folder / entry.id``; where it is. Raises GalleryError when
    Dot has a plugin of that name already, or the plugin cannot be downloaded, unpacked or its files are not the ones
    pinned."""
    target: Path = folder / entry.id
    await asyncio.to_thread(_refuse_taken, entry, target)
    await asyncio.to_thread(folder.mkdir, 0o700, True, True)
    staging: Path = Path(await asyncio.to_thread(tempfile.mkdtemp, "", ".installing-", str(folder)))
    try:
        archive: Path = await fetch(entry, staging)
        made: Path = staging / entry.id
        await asyncio.to_thread(unpack, entry, archive, made)
        await asyncio.to_thread(_settle, entry, made, target)
    finally:
        await asyncio.to_thread(_discard, staging)
    return target


def installed_marker(plugin: Path) -> GalleryMarker | None:
    """What the plugin folder ``plugin`` says of the gallery entry it was installed from; None for a plugin that did
    not come from the gallery, or whose marker cannot be read."""
    marker: Path = plugin / MANIFEST_DIR / GALLERY_MARKER
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        return GalleryMarker.model_validate_json(marker.read_bytes())
    except (OSError, ValidationError) as exc:
        # The plugin is then listed as one the gallery did not install, which Remove still takes out; the log says why.
        log.warning("gallery_marker_unreadable", plugin=str(plugin), error=f"{type(exc).__name__}: {exc}")
        return None
