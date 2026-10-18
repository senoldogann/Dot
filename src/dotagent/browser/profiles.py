"""The profiles of Dot's own Chrome (item 355): each keeps cookies and sign-ins of its own, and one is in use at a time
(``[browser] profile``). Default is the profile Dot always had (``browser-profile`` in its home); Incognito keeps
nothing, as its folder is emptied before Chrome starts on it and after it closes; the ones the owner adds live under
``browser-profiles``, a folder each, named as the profile. The Chrome of the user has its own profiles, so these apply
to Dot's own Chrome only. The zoom Dot's own Chrome draws pages at (item 358) is written into the preferences of the
profile in use before Chrome starts on it.
"""

import math
import shutil
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from dotagent.browser.look import zoom_level
from dotagent.core.errors import BrowserError, InvalidValueError, NotFoundError
from dotagent.core.files import write_whole
from dotagent.core.paths import browser_incognito_dir, browser_profile_dir, browser_profiles_dir
from dotagent.core.types import FrozenModel, JsonObject

DEFAULT_PROFILE: str = "default"
INCOGNITO_PROFILE: str = "incognito"
# The most profiles that can be added beside Default and Incognito.
MAX_ADDED_PROFILES: int = 24
# What Chrome keeps of the sites in a profile's folder (its first profile, "Default"): the cookies with the files SQLite
# keeps beside them, and the caches. Read on Chrome 155 on macOS (2026-10-17); chrome_import.py copies the same cookies.
_PROFILE_DIR: str = "Default"
# The file Chrome keeps a profile's settings in, and the key of its default storage partition, under which its default
# zoom level is kept (``partition.default_zoom_level.x``, as Chrome's own settings page writes it).
_PREFERENCES: str = "Preferences"
_DEFAULT_PARTITION: str = "x"
_PROFILE_FOLDER_MODE: int = 0o700
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
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


def write_default_zoom(folder: Path, percent: int) -> None:
    """Have the Chrome that starts on the profile in ``folder`` draw pages at ``percent`` of their size (item 358):
    Chrome's default zoom, kept in the profile's preferences, which Chrome reads when it starts. Blocking, and while no
    Chrome holds the profile; the other preferences stay as they are, and a file that holds that zoom already is left
    as it is."""
    preferences: Path = folder / _PROFILE_DIR / _PREFERENCES
    kept: JsonObject = _preferences(preferences)
    partition: JsonValue = kept.get("partition", {})
    zooms: JsonValue = partition.get("default_zoom_level", {}) if isinstance(partition, dict) else None
    if not isinstance(partition, dict) or not isinstance(zooms, dict):
        raise BrowserError(
            "Chrome's preferences of the profile keep the zoom in a way Dot does not know", {"path": str(preferences)}
        )
    wanted: float = zoom_level(percent)
    held: JsonValue = zooms.get(_DEFAULT_PARTITION, 0.0)
    if isinstance(held, int | float) and not isinstance(held, bool) and math.isclose(held, wanted, abs_tol=1e-9):
        return
    changed: JsonObject = {
        **kept,
        "partition": {**partition, "default_zoom_level": {**zooms, _DEFAULT_PARTITION: wanted}},
    }
    try:
        preferences.parent.mkdir(mode=_PROFILE_FOLDER_MODE, parents=True, exist_ok=True)
        write_whole(preferences, _JSON_OBJECT.dump_json(changed).decode("utf-8"))
    except OSError as error:
        raise BrowserError(
            "the zoom could not be written into Chrome's preferences of the profile",
            {"path": str(preferences), "error": str(error)},
        ) from error


def _preferences(path: Path) -> JsonObject:
    """The preferences Chrome keeps in ``path``, none when it has not written them yet. What the file holds is not put
    into an error: Chrome's preferences can name the accounts signed in."""
    try:
        text: str = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise BrowserError(
            "Chrome's preferences of the profile could not be read", {"path": str(path), "error": str(error)}
        ) from error
    try:
        return _JSON_OBJECT.validate_json(text)
    except ValidationError as error:
        raise BrowserError(
            "Chrome's preferences of the profile are not a JSON object",
            {"path": str(path), "errors": ", ".join(item["type"] for item in error.errors()[:5])},
        ) from error
