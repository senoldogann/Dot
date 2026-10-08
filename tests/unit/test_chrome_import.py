"""Copying the sign-ins of a Chrome profile into Dot's profile, on a synthetic profile with the real file layout."""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from dotagent.browser.chrome_import import ImportReport, chrome_profile_names, import_chrome_profile
from dotagent.browser.keychain import read_keychain_mode
from dotagent.core.errors import BrowserError

_CHROME_EPOCH_OFFSET_S: int = 11_644_473_600
_HOUR_US: int = 3_600 * 1_000_000


def chrome_time(offset_us: int) -> int:
    return int((time.time() + _CHROME_EPOCH_OFFSET_S) * 1_000_000) + offset_us


def make_chrome(data_dir: Path) -> Path:
    """A Chrome data directory whose Default profile holds one valid, one expired and one session cookie."""
    cookies: Path = data_dir / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    with closing(sqlite3.connect(cookies)) as connection:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER, is_persistent INTEGER)"
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, ?, ?)",
            [
                (".example.com", "valid", chrome_time(_HOUR_US), 1),
                (".example.com", "expired", chrome_time(-_HOUR_US), 1),
                (".example.com", "session", 0, 0),
            ],
        )
        connection.commit()
    storage: Path = data_dir / "Default" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "000003.log").write_bytes(b"token")
    return data_dir


def test_the_valid_cookies_and_the_local_storage_are_copied_and_the_profile_switches_to_the_system_keychain(
    tmp_path: Path,
) -> None:
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    before: bytes = (data_dir / "Default" / "Cookies").read_bytes()
    report: ImportReport = import_chrome_profile(data_dir, "Default", dot_profile)
    assert report.cookies == 1
    assert report.local_storage_bytes == len(b"token")
    with closing(sqlite3.connect(dot_profile / "Default" / "Cookies")) as copy:
        assert copy.execute("SELECT COUNT(*) FROM cookies").fetchone() == (3,)
    assert (dot_profile / "Default" / "Local Storage" / "leveldb" / "000003.log").read_bytes() == b"token"
    assert read_keychain_mode(dot_profile) == "system"
    assert not (dot_profile / "import-staging").exists()
    assert (data_dir / "Default" / "Cookies").read_bytes() == before


def test_an_open_chrome_or_an_unknown_profile_is_refused_and_nothing_is_changed(tmp_path: Path) -> None:
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    assert chrome_profile_names(data_dir) == ["Default"]
    with pytest.raises(BrowserError, match="no cookies to import"):
        import_chrome_profile(data_dir, "Profile 7", dot_profile)
    os.symlink(f"testhost-{os.getpid()}", data_dir / "SingletonLock")
    with pytest.raises(BrowserError, match="Chrome has the profile open"):
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert not dot_profile.exists()
    assert read_keychain_mode(dot_profile) == "mock"
