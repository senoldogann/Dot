"""The profiles of Dot's own Chrome (item 355): Default and Incognito are always there, the ones added are listed by
name and can be deleted, a name is a folder's and nothing else, and clearing a profile deletes its cookies and caches
while its settings stay."""

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
)
from dotagent.config import BrowserProfileName
from dotagent.core.errors import InvalidValueError, NotFoundError

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


@pytest.mark.parametrize("name", ["", " Work", "Work ", "../x", "a/b", ".hidden", "-x", "_x", "a" * 49, "a\nb"])
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
