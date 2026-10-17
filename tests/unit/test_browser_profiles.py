"""The profiles of Dot's own Chrome (item 355): Default and Incognito are always there, the ones added are listed by
name and can be deleted, a name is a folder's and nothing else, and clearing a profile deletes its cookies and caches
while its settings stay. The zoom Dot's own Chrome draws pages at (item 358) is written into a profile's preferences,
the others kept."""

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
    listed_profiles,
    profile_dir,
    write_default_zoom,
)
from dotagent.config import BrowserProfileName
from dotagent.core.errors import BrowserError, InvalidValueError, NotFoundError

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


def test_clearing_deletes_the_cookies_and_the_caches_and_keeps_the_rest(home: Path) -> None:
    profile: Path = profile_dir("default") / "Default"
    (profile / "Cache" / "Cache_Data").mkdir(parents=True)
    (profile / "Code Cache").mkdir()
    for name in ("Cookies", "Cookies-journal", "Preferences", "History"):
        (profile / name).write_text("x", encoding="utf-8")
    clear_site_data(profile_dir("default"))
    assert sorted(entry.name for entry in profile.iterdir()) == ["History", "Preferences"]


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
