"""The profiles of Dot's own Chrome (item 355, after T3 Code's): each keeps cookies and sign-ins of its own, and one is
in use at a time (``[browser] profile``). Default is the profile Dot always had (``browser-profile`` in its home);
Incognito keeps nothing, as its folder is emptied before Chrome starts on it and after it closes; the ones the owner
adds live under ``browser-profiles``, a folder each, named as the profile. The Chrome of the user has its own profiles,
so these apply to Dot's own Chrome only.
"""

import shutil
from pathlib import Path
from typing import Literal

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.paths import browser_incognito_dir, browser_profile_dir, browser_profiles_dir
from dotagent.core.types import FrozenModel

DEFAULT_PROFILE: str = "default"
INCOGNITO_PROFILE: str = "incognito"
# As many as T3 Code allows.
MAX_ADDED_PROFILES: int = 24
# What Chrome keeps of the sites in a profile's folder (its first profile, "Default"): the cookies with the files SQLite
# keeps beside them, and the caches. Read on Chrome 155 on macOS (2026-10-17); chrome_import.py copies the same cookies.
_PROFILE_DIR: str = "Default"
_COOKIES: tuple[str, ...] = ("Cookies", "Cookies-journal", "Cookies-wal", "Cookies-shm")
_CACHES: tuple[str, ...] = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")

type ProfileKind = Literal["default", "incognito", "added"]


class ProfileView(FrozenModel):
    """A profile of Dot's own Chrome as Settings › Browser lists it."""

    name: str
    kind: ProfileKind


def _reserved(name: str) -> bool:
    return name.casefold() in (DEFAULT_PROFILE, INCOGNITO_PROFILE)


def profile_dir(name: str) -> Path:
    """The folder of the profile ``name`` (a name ``[browser] profile`` allows)."""
    if name == DEFAULT_PROFILE:
        return browser_profile_dir()
    if name == INCOGNITO_PROFILE:
        return browser_incognito_dir()
    return browser_profiles_dir() / name


def listed_profiles() -> tuple[ProfileView, ...]:
    """Default, Incognito, then the profiles the owner added, by name (blocking: it lists a folder)."""
    root: Path = browser_profiles_dir()
    added: list[str] = sorted(entry.name for entry in root.iterdir() if entry.is_dir()) if root.is_dir() else []
    return (
        ProfileView(name=DEFAULT_PROFILE, kind="default"),
        ProfileView(name=INCOGNITO_PROFILE, kind="incognito"),
        *(ProfileView(name=name, kind="added") for name in added),
    )


def add_profile(name: str) -> None:
    """Make the profile ``name`` (a valid name, checked by the request): an empty folder, which Chrome fills on its
    first start on it (blocking)."""
    if _reserved(name):
        raise InvalidValueError("Default and Incognito are there already; choose another name", {"name": name})
    added: tuple[ProfileView, ...] = tuple(view for view in listed_profiles() if view.kind == "added")
    if any(view.name.casefold() == name.casefold() for view in added):
        raise InvalidValueError("a profile of that name is there already", {"name": name})
    if len(added) >= MAX_ADDED_PROFILES:
        raise InvalidValueError(f"at most {MAX_ADDED_PROFILES} profiles can be added", {"name": name})
    (browser_profiles_dir() / name).mkdir(parents=True)


def known_profile(name: str) -> ProfileView:
    """The listed profile ``name``, or :class:`NotFoundError`."""
    found: ProfileView | None = next((view for view in listed_profiles() if view.name == name), None)
    if found is None:
        raise NotFoundError("there is no profile of that name", {"name": name})
    return found


def delete_profile(name: str) -> None:
    """Delete a profile the owner added, with what it kept (blocking); Default and Incognito stay."""
    if known_profile(name).kind != "added":
        raise InvalidValueError("only a profile you added can be deleted", {"name": name})
    shutil.rmtree(browser_profiles_dir() / name)


def clear_site_data(folder: Path) -> None:
    """Delete the cookies and the caches the profile in ``folder`` keeps, while no Chrome holds it (blocking); its
    settings, extensions and history stay."""
    profile: Path = folder / _PROFILE_DIR
    for name in _COOKIES:
        (profile / name).unlink(missing_ok=True)
    for name in _CACHES:
        cache: Path = profile / name
        if cache.is_dir():
            shutil.rmtree(cache)


def empty_incognito() -> None:
    """Empty Incognito's folder (blocking): what a Chrome on it kept goes."""
    folder: Path = browser_incognito_dir()
    if folder.exists():
        shutil.rmtree(folder)
