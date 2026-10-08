"""Bringing the sign-ins of a personal Chrome profile over to Dot's browser profile.

Chrome refuses remote debugging on its default data directory (since version 136) and locks a profile while it runs, so Dot
cannot drive the personal profile itself. It copies the two things a signed-in site needs, the cookies and the local
storage, into its own profile instead, while Chrome is quit. Nothing is decrypted or read: the cookie values stay encrypted
with the Keychain key they were written with, so the profile switches to the system keychain (see ``keychain``). The
personal profile is only read, never changed. The copy is made in a staging directory first and moved into place at the
end, so a failure leaves the profile as it was.
"""

import contextlib
import os
import shutil
import sqlite3
import time
from pathlib import Path

from dotagent.browser.keychain import write_keychain_mode
from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.core.errors import BrowserError
from dotagent.core.types import FrozenModel

# Chrome keeps the profile it opens in a user data directory under this name.
_PROFILE_DIR: str = "Default"
# Chrome 154 keeps the cookie database in the profile folder itself; a smoke test with a real Chrome pins this layout.
_COOKIES: str = "Cookies"
_COOKIE_SIDECARS: tuple[str, ...] = ("-journal", "-wal", "-shm")
_LOCAL_STORAGE: Path = Path("Local Storage")
_STAGING: str = "import-staging"
# Chrome stores times as microseconds since 1601-01-01.
_CHROME_EPOCH_OFFSET_S: int = 11_644_473_600
_MICROSECONDS: int = 1_000_000


class ImportReport(FrozenModel):
    source: str
    cookies: int
    local_storage_bytes: int


def chrome_profile_names(data_dir: Path) -> list[str]:
    """The profiles in a Chrome data directory that have cookies, e.g. ``Default`` and ``Profile 1``."""
    if not data_dir.is_dir():
        return []
    return sorted(entry.name for entry in data_dir.iterdir() if (entry / _COOKIES).is_file())


def has_stored_cookies(dot_profile: Path) -> bool:
    """Whether Dot's own profile already holds a cookie database, whose sign-ins an import would replace."""
    return (dot_profile / _PROFILE_DIR / _COOKIES).is_file()


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


def _stage_cookies(source: Path, staged: Path) -> int:
    """Copy the cookie database with the files SQLite keeps beside it, let SQLite settle the copy, and count the cookies
    that survive a restart and have not expired."""
    staged.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *_COOKIE_SIDECARS):
        candidate: Path = source.with_name(source.name + suffix)
        if candidate.is_file():
            shutil.copy2(candidate, staged.with_name(staged.name + suffix))
    try:
        with contextlib.closing(sqlite3.connect(staged)) as connection:
            row: tuple[int] | None = connection.execute(
                "SELECT COUNT(*) FROM cookies WHERE is_persistent = 1 AND expires_utc > ?", (_chrome_time(time.time()),)
            ).fetchone()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as error:
        raise BrowserError(
            "the copied cookie database could not be read", {"source": str(source), "error": str(error)}
        ) from error
    if row is None:
        raise BrowserError("the copied cookie database returned no count", {"source": str(source)})
    return row[0]


def _stage_local_storage(source: Path, staged: Path) -> int:
    if not source.is_dir():
        return 0
    shutil.copytree(source, staged)
    return _folder_bytes(staged)


def _install_cookies(staging: Path, target: Path) -> None:
    for suffix in ("", *_COOKIE_SIDECARS):
        destination: Path = target / (_COOKIES + suffix)
        destination.unlink(missing_ok=True)
        staged: Path = staging / (_COOKIES + suffix)
        if staged.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)


def _install_local_storage(staging: Path, target: Path) -> None:
    destination: Path = target / _LOCAL_STORAGE
    if destination.is_dir():
        shutil.rmtree(destination)
    staged: Path = staging / _LOCAL_STORAGE
    if staged.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, destination)


def import_chrome_profile(data_dir: Path, profile_name: str, dot_profile: Path) -> ImportReport:
    """Replace the cookies and local storage of Dot's profile with those of the Chrome profile ``profile_name`` and
    switch Dot's profile to the system keychain. Both Chrome and Dot's browser must be closed."""
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
    _require_closed(dot_profile, "Dot's browser", "stop the Dot service (dotx service uninstall) or close its window")
    source: Path = data_dir / profile_name
    staging: Path = dot_profile / _STAGING
    dot_profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    cookies: int = _stage_cookies(source / _COOKIES, staging / _COOKIES)
    if cookies == 0:
        raise BrowserError(
            "that Chrome profile holds no sign-in cookies that are still valid; nothing was changed",
            {"source": str(source)},
        )
    storage_bytes: int = _stage_local_storage(source / _LOCAL_STORAGE, staging / _LOCAL_STORAGE)
    target: Path = dot_profile / _PROFILE_DIR
    _install_cookies(staging, target)
    _install_local_storage(staging, target)
    write_keychain_mode(dot_profile, "system")
    shutil.rmtree(staging)
    return ImportReport(source=str(source), cookies=cookies, local_storage_bytes=storage_bytes)
