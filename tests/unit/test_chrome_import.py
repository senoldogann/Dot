"""Copying the sign-ins of a Chrome profile into Dot's profile, on a synthetic profile with the real file layout."""

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from dotagent.browser import chrome_import
from dotagent.browser.chrome_import import (
    ImportReport,
    chrome_profile_names,
    has_stored_cookies,
    import_chrome_profile,
)
from dotagent.browser.keychain import read_keychain_mode, write_keychain_mode
from dotagent.core.errors import BrowserError
from dotagent.core.paths import browser_incognito_dir

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
    # The cookie that lasts and the one with no expiry, which Chrome gives back: what it can be expected to read.
    assert report.loadable == 2
    assert report.staging_left is None
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


def _dot_profile_with_sign_ins(folder: Path) -> Path:
    """A Dot profile that already holds a cookie, a local storage and the marker of the mock keychain."""
    cookies: Path = folder / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    with closing(sqlite3.connect(cookies)) as connection:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER, is_persistent INTEGER)"
        )
        connection.execute("INSERT INTO cookies VALUES ('.old.example', 'old', ?, 1)", (chrome_time(_HOUR_US),))
        connection.commit()
    storage: Path = folder / "Default" / "Local Storage" / "leveldb"
    storage.mkdir(parents=True)
    (storage / "old.log").write_bytes(b"old-token")
    write_keychain_mode(folder, "mock")
    return folder


def _files(profile: Path) -> dict[str, bytes]:
    """What the profile holds, file by file; its preferences too, as the word that keeps the session cookies is the last
    thing an import writes, so a failed one leaves them as they were."""
    return {str(path.relative_to(profile)): path.read_bytes() for path in sorted(profile.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("fails_at", range(1, 7))
def test_an_import_that_fails_at_any_step_leaves_the_profile_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails_at: int
) -> None:
    """Cookies written with the real key under a marker that says mock are cookies Chrome cannot decrypt and deletes,
    so the cookies, the local storage and the marker are replaced together or not at all (item 529)."""
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = _dot_profile_with_sign_ins(tmp_path / "dot")
    before: dict[str, bytes] = _files(dot_profile)
    real_replace: Callable[[Path, Path], None] = os.replace
    calls: list[int] = []

    def failing(source: Path, destination: Path) -> None:
        calls.append(1)
        if len(calls) == fails_at:
            raise OSError("disk trouble")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises((OSError, BrowserError)):
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert len(calls) >= fails_at
    assert _files(dot_profile) == before
    assert read_keychain_mode(dot_profile) == "mock"
    assert not (dot_profile / "import-staging").exists()


def test_the_staging_folder_does_not_stay_when_the_copy_fails_or_finds_nothing_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging folder holds a plain copy of the local storage and of the cookie file of the owner's Chrome."""
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = _dot_profile_with_sign_ins(tmp_path / "dot")
    before: dict[str, bytes] = _files(dot_profile)

    def no_room(source: Path, staged: Path) -> int:
        raise OSError("no room")

    with monkeypatch.context() as patched:
        patched.setattr(chrome_import, "_stage_local_storage", no_room)
        with pytest.raises(OSError, match="no room"):
            import_chrome_profile(data_dir, "Default", dot_profile)
    assert _files(dot_profile) == before
    assert not (dot_profile / "import-staging").exists()
    with closing(sqlite3.connect(data_dir / "Default" / "Cookies")) as connection:
        connection.execute("DELETE FROM cookies WHERE name = 'valid'")
        connection.commit()
    with pytest.raises(BrowserError, match="no sign-in cookies that are still valid"):
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert _files(dot_profile) == before
    assert not (dot_profile / "import-staging").exists()


def test_an_import_marks_the_profile_so_that_chrome_keeps_the_session_cookies_it_copied(tmp_path: Path) -> None:
    """Chrome deletes the cookies without an expiry as it starts, unless the last run of the profile crashed
    (item 453); the first start on an imported profile is the check that ends the import."""
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    import_chrome_profile(data_dir, "Default", dot_profile)
    preferences: dict[str, object] = json.loads((dot_profile / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert preferences["profile"] == {"exit_type": "Crashed", "exited_cleanly": False}


def test_a_profile_holds_cookies_when_its_cookie_file_has_a_row(tmp_path: Path) -> None:
    profile: Path = tmp_path / "dot"
    assert not has_stored_cookies(profile)
    cookies: Path = profile / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    # Chrome makes the file as it starts, and a site has set nothing in it yet.
    cookies.write_bytes(b"")
    assert not has_stored_cookies(profile)
    with closing(sqlite3.connect(cookies)) as connection:
        connection.execute("CREATE TABLE cookies (host_key TEXT)")
        connection.commit()
    assert not has_stored_cookies(profile)
    with closing(sqlite3.connect(cookies)) as connection:
        connection.execute("INSERT INTO cookies VALUES ('.example.com')")
        connection.commit()
    assert has_stored_cookies(profile)
    cookies.write_bytes(b"this is not a database")
    assert has_stored_cookies(profile)


def test_the_incognito_profile_is_refused_before_anything_is_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dot's Chrome empties Incognito as it starts, so an import would be gone before the check that ends it."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    with pytest.raises(BrowserError, match="Incognito keeps nothing"):
        import_chrome_profile(data_dir, "Default", browser_incognito_dir())
    assert not browser_incognito_dir().exists()


def test_a_failure_that_cannot_be_put_right_says_what_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh profile gets the cookies, then the move of the local storage fails and the new cookie file cannot be
    removed either: the profile is left with real-key cookies under no marker, and the error has to say so."""
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    real_replace: Callable[[Path, Path], None] = os.replace
    calls: list[int] = []

    def failing(source: Path, destination: Path) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk trouble")
        real_replace(source, destination)

    def busy(path: Path) -> None:
        raise OSError("busy")

    monkeypatch.setattr(os, "replace", failing)
    monkeypatch.setattr(chrome_import, "_discard", busy)
    with pytest.raises(BrowserError, match="could not put the profile back") as failure:
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert str(dot_profile / "Default" / "Cookies") in str(failure.value.context["files"])
    assert (dot_profile / "import-staging").exists()


def test_a_staging_folder_that_cannot_be_removed_is_said_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The staging folder holds a plain copy of the local storage of the owner's Chrome: a removal that fails is not
    silent, after an import that was made and after one that failed."""
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    staging: Path = dot_profile / "import-staging"
    real_rmtree: Callable[[Path], None] = shutil.rmtree

    def stubborn(path: Path, *args: object, **named: object) -> None:
        if Path(path) == staging:
            raise OSError("busy")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", stubborn)
    report: ImportReport = import_chrome_profile(data_dir, "Default", dot_profile)
    assert report.staging_left == str(staging)
    assert read_keychain_mode(dot_profile) == "system"
    real_rmtree(staging)

    def no_room(source: Path, staged: Path) -> int:
        raise OSError("no room")

    monkeypatch.setattr(chrome_import, "_stage_local_storage", no_room)
    with pytest.raises(BrowserError, match=r"staging folder.*could not be removed") as failure:
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert isinstance(failure.value.__cause__, OSError)


def test_an_import_does_not_remove_what_an_earlier_one_could_not_put_back(tmp_path: Path) -> None:
    data_dir: Path = make_chrome(tmp_path / "Chrome")
    dot_profile: Path = tmp_path / "dot"
    kept: Path = dot_profile / "import-staging" / "previous" / "Cookies"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"what the profile held")
    with pytest.raises(BrowserError, match="earlier import"):
        import_chrome_profile(data_dir, "Default", dot_profile)
    assert kept.read_bytes() == b"what the profile held"
