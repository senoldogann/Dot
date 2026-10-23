"""Bringing the sign-ins of a personal Chrome profile over to Dot's browser profile.

Chrome refuses remote debugging on its default data directory (since version 136) and locks a profile while it runs, so
Dot cannot drive the personal profile itself. It copies the two things a signed-in site needs, the cookies and the local
storage, into its own profile instead, while Chrome is quit. Nothing is decrypted or read: the cookie values stay
encrypted with the Keychain key they were written with, so the profile switches to the system keychain (see
``keychain``). The personal profile is only read, never changed. The copy is made in a staging directory first and moved
into place at the end, all or nothing: what the profile held is put aside in the staging directory and put back when a
step fails, and the staging directory, which holds a plain copy of the local storage, never stays (item 529).
"""

import contextlib
import os
import shutil
import sqlite3
import time
from pathlib import Path

from dotagent.browser.keychain import marker_path, write_keychain_mode
from dotagent.browser.profile_lock import held_for_import, profile_holder_pid
from dotagent.browser.profiles import keep_session_cookies
from dotagent.core.errors import BrowserError
from dotagent.core.paths import browser_incognito_dir
from dotagent.core.types import FrozenModel

# Chrome keeps the profile it opens in a user data directory under this name.
_PROFILE_DIR: str = "Default"
# Chrome 154 keeps the cookie database in the profile folder itself; a smoke test with a real Chrome pins this layout.
_COOKIES: str = "Cookies"
_COOKIE_SIDECARS: tuple[str, ...] = ("-journal", "-wal", "-shm")
_LOCAL_STORAGE: Path = Path("Local Storage")
_STAGING: str = "import-staging"
# Where, in the staging folder, what the profile held goes while an import replaces it.
_ASIDE: str = "previous"
# What it is called once the import was made and the profile no longer needs it.
_REPLACED: str = "replaced"
# Chrome stores times as microseconds since 1601-01-01.
_CHROME_EPOCH_OFFSET_S: int = 11_644_473_600
_MICROSECONDS: int = 1_000_000


class ImportReport(FrozenModel):
    """``cookies`` last past a restart and have not expired; ``loadable`` adds the ones with no expiry, which Chrome
    gives back (:func:`keep_session_cookies`): what it can be expected to read. ``staging_left`` names the staging
    folder, or the folder in it that holds what the profile held before, when it could not be removed after an import
    that was made: sign-ins and local storage that are the owner's to delete."""

    source: str
    cookies: int
    loadable: int
    local_storage_bytes: int
    staging_left: str | None = None


def chrome_profile_names(data_dir: Path) -> list[str]:
    """The profiles in a Chrome data directory that have cookies, e.g. ``Default`` and ``Profile 1``."""
    if not data_dir.is_dir():
        return []
    return sorted(entry.name for entry in data_dir.iterdir() if (entry / _COOKIES).is_file())


def has_stored_cookies(dot_profile: Path) -> bool:
    """Whether Dot's own profile already holds a cookie, whose sign-in an import would replace. The cookie file Chrome
    makes as it starts holds none until a site sets one (item 529); one that cannot be read is taken to hold some."""
    database: Path = dot_profile / _PROFILE_DIR / _COOKIES
    if not database.is_file():
        return False
    if database.stat().st_size == 0:
        return False
    try:
        with contextlib.closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            row: tuple[int] | None = connection.execute("SELECT COUNT(*) FROM cookies").fetchone()
    except sqlite3.Error:
        return True
    return row is not None and row[0] > 0


def require_importable(dot_profile: Path) -> None:
    """Refuse the profile Incognito: Dot's Chrome empties it as it starts, so what an import put there would be gone
    before the check that follows it, and the check would blame the Keychain (item 529)."""
    if dot_profile == browser_incognito_dir():
        raise BrowserError(
            "Incognito keeps nothing, so sign-ins imported into it would be gone as Dot's Chrome starts; choose "
            "another profile (Settings › Browser › Use) and import again",
            {"profile": str(dot_profile)},
        )


def _require_closed(user_data_dir: Path, owner: str, how_to_close: str) -> None:
    holder: int | None = profile_holder_pid(user_data_dir)
    if holder is not None:
        raise BrowserError(
            f"{owner} has the profile open; {how_to_close} and try again",
            {"profile": str(user_data_dir), "pid": holder},
        )


def _folder_bytes(directory: Path) -> int:
    return sum(entry.stat().st_size for entry in directory.rglob("*") if entry.is_file())


def _chrome_time(unix_seconds: float) -> int:
    return int((unix_seconds + _CHROME_EPOCH_OFFSET_S) * _MICROSECONDS)


def _stage_cookies(source: Path, staged: Path) -> tuple[int, int]:
    """Copy the cookie database with the files SQLite keeps beside it, let SQLite settle the copy, and count the cookies
    that survive a restart and have not expired, and with the ones that have no expiry (what Chrome can be expected
    to read)."""
    staged.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *_COOKIE_SIDECARS):
        candidate: Path = source.with_name(source.name + suffix)
        if candidate.is_file():
            shutil.copy2(candidate, staged.with_name(staged.name + suffix))
    try:
        with contextlib.closing(sqlite3.connect(staged)) as connection:
            lasting: tuple[int] | None = connection.execute(
                "SELECT COUNT(*) FROM cookies WHERE is_persistent = 1 AND expires_utc > ?", (_chrome_time(time.time()),)
            ).fetchone()
            session: tuple[int] | None = connection.execute(
                "SELECT COUNT(*) FROM cookies WHERE is_persistent = 0"
            ).fetchone()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as error:
        raise BrowserError(
            "the copied cookie database could not be read", {"source": str(source), "error": str(error)}
        ) from error
    if lasting is None or session is None:
        raise BrowserError("the copied cookie database returned no count", {"source": str(source)})
    return lasting[0], lasting[0] + session[0]


def _stage_local_storage(source: Path, staged: Path) -> int:
    if not source.is_dir():
        return 0
    shutil.copytree(source, staged)
    return _folder_bytes(staged)


def _discard(path: Path) -> None:
    """Remove a file, or a folder with what it holds; nothing when it is gone."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _put_back(held: list[tuple[Path, Path]], put_in: list[Path]) -> list[Path]:
    """Take away what a failed import put into the profile and move back what the profile held; the paths that could
    not be put right: a new file that stays, a file of the profile that was not moved back."""
    stranded: list[Path] = []
    for path in put_in:
        try:
            _discard(path)
        except OSError:
            stranded.append(path)
    for aside, original in held:
        try:
            os.replace(aside, original)
        except OSError:
            stranded.append(aside)
    return stranded


def _remove_staging(staging: Path) -> Path | None:
    """Remove the staging folder, which holds a plain copy of the local storage of the owner's Chrome; the folder when
    it could not be removed."""
    try:
        shutil.rmtree(staging)
    except FileNotFoundError:
        return None
    except OSError:
        return staging
    return None


def _remove_staging_after(error: BaseException, staging: Path) -> None:
    """Remove the staging folder of an import that failed with ``error``; when that fails too, say so (an exception of
    this import is made to carry it, not one of the user's: a stop is let through)."""
    left: Path | None = _remove_staging(staging)
    if left is not None and isinstance(error, Exception):
        raise BrowserError(
            "the import failed, and its staging folder, which holds a plain copy of the local storage of your Chrome, "
            "could not be removed: delete it",
            {"staging": str(left), "error": str(error)},
        ) from error


def _swap_in(staging: Path, dot_profile: Path) -> Path | None:
    """Put the staged cookies and local storage into Dot's profile and mark the profile as written with the system
    keychain, all or nothing (item 529). What the profile held goes aside into the staging folder first and comes back
    when a step fails: cookies written with the real key under a marker that says mock are cookies Chrome cannot
    decrypt, and it deletes them as it starts. The staging folder, which holds a plain copy of the local storage until
    it is moved in, and then what the profile held, is removed at the end (what could not be removed is the answer);
    when a failure could not be put right it is kept, with what the profile held in ``previous``, and the error names
    what stays. The preferences are given the word that keeps the session cookies (:func:`keep_session_cookies`)
    last, so that a failed import leaves them as they were."""
    target: Path = dot_profile / _PROFILE_DIR
    aside: Path = staging / _ASIDE
    aside.mkdir(parents=True)
    names: tuple[str, ...] = tuple(_COOKIES + suffix for suffix in ("", *_COOKIE_SIDECARS))
    steps: list[tuple[Path, Path]] = [
        *((staging / name, target / name) for name in names),
        (staging / _LOCAL_STORAGE, target / _LOCAL_STORAGE),
    ]
    marker: Path = marker_path(dot_profile)
    held: list[tuple[Path, Path]] = []
    put_in: list[Path] = []
    try:
        for staged, destination in steps:
            if destination.exists():
                os.replace(destination, aside / destination.name)
                held.append((aside / destination.name, destination))
            if staged.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, destination)
                put_in.append(destination)
        if marker.exists():
            os.replace(marker, aside / marker.name)
            held.append((aside / marker.name, marker))
        write_keychain_mode(dot_profile, "system")
        put_in.append(marker)
        keep_session_cookies(dot_profile, create=True)
    except BaseException as error:
        stranded: list[Path] = _put_back(held, put_in)
        if stranded and isinstance(error, Exception):
            raise BrowserError(
                "the import failed and could not put the profile back as it was; the files of the profile that "
                "are not back are kept in the staging folder, and the new ones named here are still in the profile",
                {"staging": str(staging), "files": ", ".join(str(path) for path in stranded), "error": str(error)},
            ) from error
        if not stranded:
            _remove_staging_after(error, staging)
        raise
    # What the profile held is no use now. It is renamed first, so that ``previous`` is only ever what a failed import
    # could not put back: a staging folder that stays after a made import holds ``replaced``, which a later import does
    # not take for that.
    left: Path | None = None
    try:
        os.replace(aside, staging / _REPLACED)
    except OSError:
        left = aside
    if left is None:
        left = _remove_staging(staging / _REPLACED)
    return left if left is not None else _remove_staging(staging)


def import_chrome_profile(data_dir: Path, profile_name: str, dot_profile: Path) -> ImportReport:
    """Replace the cookies and local storage of Dot's profile with those of the Chrome profile ``profile_name`` and
    switch Dot's profile to the system keychain. Both Chrome and Dot's browser must be closed. A failure leaves the
    profile as it was and no staging folder behind, or says what it could not put right."""
    require_importable(dot_profile)
    available: list[str] = chrome_profile_names(data_dir)
    if profile_name not in available:
        raise BrowserError(
            "that Chrome profile has no cookies to import",
            {
                "data_dir": str(data_dir),
                "profile": profile_name,
                "looked_for": str(data_dir / profile_name / _COOKIES),
                "available": ", ".join(available) or "none",
            },
        )
    _require_closed(data_dir, "Chrome", "quit it (Cmd+Q) so its files are complete")
    # Held until the files are in place (item 554): the Dot service starts its Chrome on a profile that is not open, and
    # one that started during the copy would write to the files this import moves away from under it. It is looked at
    # again under the lock, which a start that is under way holds until its Chrome holds the profile.
    with held_for_import(dot_profile):
        _require_closed(
            dot_profile, "Dot's browser", "stop the Dot service (dotx service uninstall) or close its window"
        )
        return _replace_sign_ins(data_dir / profile_name, dot_profile)


def _replace_sign_ins(source: Path, dot_profile: Path) -> ImportReport:
    """The import itself, with Dot's profile held and closed (:func:`import_chrome_profile`)."""
    staging: Path = dot_profile / _STAGING
    if (staging / _ASIDE).is_dir() and any((staging / _ASIDE).iterdir()):
        raise BrowserError(
            "an earlier import left files of this profile in its staging folder: if that import failed and could not "
            "put the profile back, move them back by hand; if it was made, they are what the profile held before it "
            "and the folder can be deleted; then import again",
            {"staging": str(staging)},
        )
    if staging.exists():
        shutil.rmtree(staging)
    try:
        cookies, loadable = _stage_cookies(source / _COOKIES, staging / _COOKIES)
        if cookies == 0:
            raise BrowserError(
                "that Chrome profile holds no sign-in cookies that are still valid; nothing was changed",
                {"source": str(source)},
            )
        storage_bytes: int = _stage_local_storage(source / _LOCAL_STORAGE, staging / _LOCAL_STORAGE)
    except BaseException as error:
        _remove_staging_after(error, staging)
        raise
    left: Path | None = _swap_in(staging, dot_profile)
    return ImportReport(
        source=str(source),
        cookies=cookies,
        loadable=loadable,
        local_storage_bytes=storage_bytes,
        staging_left=None if left is None else str(left),
    )
