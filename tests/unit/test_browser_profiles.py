"""The profiles of Dot's own Chrome (item 355): Default and Incognito are always there, the ones added are listed by
name and can be deleted, a name is a folder's and nothing else, and clearing a profile deletes the cookies, the storage
of the sites and the logins Chrome saved, with the caches, while its history and settings stay (item 532). The zoom
Dot's own Chrome draws pages at (item 358) is written into a profile's preferences, the others kept, and so is the word
that has Chrome keep the session cookies of its last run (item 453)."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from dotagent.browser.profiles import (
    INCOGNITO_PROFILE,
    MAX_ADDED_PROFILES,
    ProfileView,
    add_profile,
    clear_site_data,
    delete_profile,
    empty_incognito,
    keep_session_cookies,
    listed_profiles,
    profile_dir,
    write_default_zoom,
)
from dotagent.config import BrowserProfileName
from dotagent.core.errors import BrowserError, InvalidValueError, NotFoundError
from dotagent.core.paths import browser_incognito_dir

_NAMES: TypeAdapter[str] = TypeAdapter(BrowserProfileName)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "dot"))
    return tmp_path / "dot"


def test_default_and_incognito_are_listed_first_and_added_ones_by_name(home: Path) -> None:
    assert listed_profiles() == (
        ProfileView(name="default", kind="default"),
        ProfileView(name="incognito", kind="incognito"),
    )
    add_profile("Work")
    add_profile("Bank 2")
    assert [view.name for view in listed_profiles()] == ["default", "incognito", "Bank 2", "Work"]
    assert profile_dir("Work") == home / "browser-profiles" / "Work"
    assert profile_dir("default") == home / "browser-profile"


@pytest.mark.parametrize("name", ["Work", "bank-2", "My_profile", "a" * 48, "Ev 1", "İş", "Çalışma"])
def test_a_name_is_a_folders(name: str) -> None:
    assert _NAMES.validate_python(name) == name


@pytest.mark.parametrize(
    "name", ["", " Work", "Work ", "../x", "a/b", ".hidden", "-x", "_x", "a" * 49, "a\nb", "Incognito", "DEFAULT"]
)
def test_a_name_that_is_not_a_folders_alone_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        _NAMES.validate_python(name)


def test_adding_twice_or_a_reserved_name_or_too_many_is_refused(home: Path) -> None:
    add_profile("Work")
    for taken in ("Work", "work", "Default", "INCOGNITO"):
        with pytest.raises(InvalidValueError):
            add_profile(taken)
    for number in range(MAX_ADDED_PROFILES - 1):
        add_profile(f"p{number}")
    with pytest.raises(InvalidValueError):
        add_profile("one more")


def test_only_an_added_profile_is_deleted(home: Path) -> None:
    add_profile("Work")
    (profile_dir("Work") / "Default").mkdir(parents=True)
    delete_profile("Work")
    assert not profile_dir("Work").exists()
    for kept in ("default", INCOGNITO_PROFILE):
        with pytest.raises(InvalidValueError):
            delete_profile(kept)
    with pytest.raises(NotFoundError):
        delete_profile("Gone")


# What a site leaves in a profile and Chrome saves for it, as a Chrome 155 profile names it (item 532).
_SITE_FOLDERS: tuple[str, ...] = (
    "Cache/Cache_Data",
    "Code Cache/js",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "Local Storage/leveldb",
    "Session Storage",
    "IndexedDB/http_example.com_0.indexeddb.leveldb",
    "Service Worker/CacheStorage",
    "Shared Dictionary/cache",
    "WebStorage",
    "File System",
    "databases",
    "blob_storage",
)
_SITE_FILES: tuple[str, ...] = (
    "Cookies",
    "Cookies-journal",
    "Trust Tokens",
    "Trust Tokens-journal",
    "Reporting and NEL",
    "Device Bound Sessions",
    "Login Data",
    "Login Data-journal",
    "Login Data For Account",
    "Login Data For Account-journal",
    "Web Data",
    "Web Data-journal",
    "Account Web Data",
    "Affiliation Database",
    "Affiliation Database-journal",
)
_PROFILES_OWN: tuple[str, ...] = ("Extension State", "Extensions", "History", "Preferences", "Secure Preferences")


def test_clearing_signs_the_sites_out_of_the_profile_and_keeps_the_rest(home: Path) -> None:
    profile: Path = profile_dir("default") / "Default"
    for folder in _SITE_FOLDERS:
        (profile / folder).mkdir(parents=True)
        (profile / folder / "data").write_text("x", encoding="utf-8")
    for name in (*_SITE_FILES, *_PROFILES_OWN[2:]):
        (profile / name).write_text("x", encoding="utf-8")
    for name in _PROFILES_OWN[:2]:
        (profile / name).mkdir()
    clear_site_data(profile_dir("default"))
    assert sorted(entry.name for entry in profile.iterdir()) == sorted(_PROFILES_OWN)


def test_clearing_a_profile_with_nothing_to_clear_is_no_error(home: Path) -> None:
    clear_site_data(profile_dir("default"))
    (profile_dir("default") / "Default").mkdir(parents=True)
    clear_site_data(profile_dir("default"))


def test_clearing_deletes_a_link_and_not_what_it_points_to(home: Path, tmp_path: Path) -> None:
    profile: Path = profile_dir("default") / "Default"
    outside: Path = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("x", encoding="utf-8")
    profile.mkdir(parents=True)
    (profile / "Local Storage").symlink_to(outside, target_is_directory=True)
    (profile / "IndexedDB").symlink_to(tmp_path / "missing")
    clear_site_data(profile_dir("default"))
    assert list(profile.iterdir()) == []
    assert (outside / "kept.txt").read_text(encoding="utf-8") == "x"


def test_incognito_is_emptied(home: Path) -> None:
    (profile_dir(INCOGNITO_PROFILE) / "Default").mkdir(parents=True)
    (profile_dir(INCOGNITO_PROFILE) / "Default" / "Cookies").write_text("x", encoding="utf-8")
    empty_incognito()
    assert not profile_dir(INCOGNITO_PROFILE).exists()
    # Nothing there is nothing to empty.
    empty_incognito()


def test_the_zoom_is_written_into_the_profile_and_its_other_preferences_stay(tmp_path: Path) -> None:
    preferences: Path = tmp_path / "Default" / "Preferences"
    write_default_zoom(tmp_path, 125)
    assert json.loads(preferences.read_text(encoding="utf-8"))["partition"]["default_zoom_level"]["x"] == pytest.approx(
        1.2239, abs=1e-4
    )
    assert preferences.stat().st_mode & 0o777 == 0o600
    # Asked again for the zoom it holds, the file is left as it is.
    written: int = preferences.stat().st_mtime_ns
    write_default_zoom(tmp_path, 125)
    assert preferences.stat().st_mtime_ns == written
    preferences.write_text(
        json.dumps(
            {"profile": {"name": "Dot"}, "partition": {"default_zoom_level": {"x": 1.0}, "per_host_zoom_levels": {}}}
        ),
        encoding="utf-8",
    )
    write_default_zoom(tmp_path, 100)
    kept = json.loads(preferences.read_text(encoding="utf-8"))
    assert kept["profile"] == {"name": "Dot"} and kept["partition"]["per_host_zoom_levels"] == {}
    assert kept["partition"]["default_zoom_level"] == {"x": 0.0}


@pytest.mark.parametrize("held", ["[]", '{"partition": []}', '{"partition": {"default_zoom_level": 1.5}}'])
def test_preferences_dot_does_not_know_are_refused_and_left_as_they_were(tmp_path: Path, held: str) -> None:
    (tmp_path / "Default").mkdir()
    (tmp_path / "Default" / "Preferences").write_text(held, encoding="utf-8")
    with pytest.raises(BrowserError):
        write_default_zoom(tmp_path, 125)
    assert (tmp_path / "Default" / "Preferences").read_text(encoding="utf-8") == held


def test_a_profile_keeps_the_session_cookies_of_its_last_run_and_its_other_preferences_stay(home: Path) -> None:
    """Item 453: the profile says its last run crashed, after which Chrome gives the cookies without an expiry back."""
    folder: Path = home / "browser-profile"
    preferences: Path = folder / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps({"profile": {"name": "Dot", "exit_type": "Normal", "exited_cleanly": True}, "zoom": 1}),
        encoding="utf-8",
    )
    keep_session_cookies(folder)
    kept = json.loads(preferences.read_text(encoding="utf-8"))
    assert kept == {"profile": {"name": "Dot", "exit_type": "Crashed", "exited_cleanly": False}, "zoom": 1}
    assert preferences.stat().st_mode & 0o777 == 0o600
    # Said already, the file is left as it is.
    written: int = preferences.stat().st_mtime_ns
    keep_session_cookies(folder)
    assert preferences.stat().st_mtime_ns == written


def test_incognito_and_a_profile_chrome_never_started_on_are_not_written(home: Path) -> None:
    fresh: Path = home / "browser-profiles" / "Work"
    keep_session_cookies(fresh)
    assert not (fresh / "Default" / "Preferences").exists()
    incognito: Path = browser_incognito_dir()
    (incognito / "Default").mkdir(parents=True)
    (incognito / "Default" / "Preferences").write_text('{"profile": {"exit_type": "Normal"}}', encoding="utf-8")
    keep_session_cookies(incognito)
    assert json.loads((incognito / "Default" / "Preferences").read_text(encoding="utf-8")) == {
        "profile": {"exit_type": "Normal"}
    }


def test_a_profile_state_dot_does_not_know_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()
    (tmp_path / "Default" / "Preferences").write_text('{"profile": "Dot"}', encoding="utf-8")
    with pytest.raises(BrowserError):
        keep_session_cookies(tmp_path)
    assert (tmp_path / "Default" / "Preferences").read_text(encoding="utf-8") == '{"profile": "Dot"}'
