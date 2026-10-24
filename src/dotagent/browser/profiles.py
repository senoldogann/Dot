"""The profiles of Dot's own Chrome (item 355): each keeps cookies and sign-ins of its own, and one is in use at a time
(``[browser] profile``). Default is the profile Dot always had (``browser-profile`` in its home); Incognito keeps
nothing, as its folder is emptied before Chrome starts on it and after it closes; the ones the owner adds live under
``browser-profiles``, a folder each, named as the profile. The Chrome of the user has its own profiles, so these apply
to Dot's own Chrome only. The zoom Dot's own Chrome draws pages at (item 358) is written into the preferences of the
profile in use before Chrome starts on it, and so is the word that keeps its session cookies (item 453).
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
# Where a profile's preferences say how Chrome's last run on it ended (``profile.exit_type``, with
# ``profile.exited_cleanly``), and the ending after which Chrome gives the cookies of that run without an expiry back.
_PROFILE_KEY: str = "profile"
_EXIT_TYPE: str = "exit_type"
_EXITED_CLEANLY: str = "exited_cleanly"
_CRASHED: str = "Crashed"
# Chrome's switch that keeps the bubble offering to restore the pages of a run that crashed away: a profile marked so
# opens no page of its last run either way (measured 2026-10-21), and the bubble would ask the owner for nothing.
HIDE_CRASH_BUBBLE: str = "--hide-crash-restore-bubble"
_PROFILE_FOLDER_MODE: int = 0o700
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_SQLITE_COMPANIONS: tuple[str, ...] = ("", "-journal", "-wal", "-shm")
# Where Chrome keeps the session of its last run: the history of each tab, with the body of every form it sent by POST
# (so that the page can be sent again), as it keeps it for a restore (item 621).
_SESSIONS: str = "Sessions"


def _databases(*names: str) -> tuple[str, ...]:
    """The files of the SQLite databases ``names``: each with the journal and the write-ahead log Chrome keeps beside
    it."""
    return tuple(f"{name}{companion}" for name in names for companion in _SQLITE_COMPANIONS)


_COOKIES: tuple[str, ...] = _databases("Cookies")
_CACHES: tuple[str, ...] = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")
# What else a site keeps in the profile, as Chrome 155 writes it (measured 2026-10-22 on a profile a page had filled
# with every kind of storage): local and session storage, IndexedDB, service workers with their caches, shared
# dictionaries, the quota database that counts all of them, the older file-system and WebSQL stores, the private-state
# tokens a site handed out, the network error reports it asked for, and the sessions it bound to the device. Many sites
# keep a sign-in here rather than in a cookie.
_SITE_STORAGE: tuple[str, ...] = (
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "Service Worker",
    "Shared Dictionary",
    "WebStorage",
    "File System",
    "databases",
    "blob_storage",
    *_databases("Trust Tokens", "Reporting and NEL", "Device Bound Sessions"),
)
# What Chrome's own password manager and autofill keep: the saved logins (and those of the Google account), the
# addresses and cards, and the table that tells which sites share a login.
_SAVED_SIGN_INS: tuple[str, ...] = _databases(
    "Login Data", "Login Data For Account", "Web Data", "Account Web Data", "Affiliation Database"
)

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


def _remove(path: Path) -> None:
    """Delete the file or the folder at ``path``, a link as the link; nothing there is nothing to delete."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def clear_site_data(folder: Path) -> None:
    """Delete what the sites left in the profile in ``folder`` and what Chrome saved for them, while no Chrome holds it
    (blocking): their cookies, their storage (local and session storage, IndexedDB, service workers), the logins and
    the autofill Chrome saved, and the caches, so that every site is signed out (item 532). Its settings, extensions
    and history stay."""
    profile: Path = folder / _PROFILE_DIR
    for name in (*_COOKIES, *_SITE_STORAGE, *_SAVED_SIGN_INS, *_CACHES):
        _remove(profile / name)


def clear_session_files(folder: Path) -> None:
    """Delete the files in which Chrome keeps the session of its last run on the profile in ``folder``, while no Chrome
    holds it (blocking): the history of each tab, with the body of every form it sent by POST, which can be a card's
    number or a password (item 621). Dot's Chrome opens no page of its last run (:func:`keep_session_cookies`), so
    nothing is lost; its sign-ins are in the cookies and the storage of the sites."""
    _remove(folder / _PROFILE_DIR / _SESSIONS)


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


def keep_session_cookies(folder: Path, *, create: bool = False) -> None:
    """Have the Chrome that starts on the profile in ``folder`` keep the cookies of its last run that have no expiry,
    as a real browser keeps a sign-in (item 453, the owner's word of 2026-10-21: "silmesin"). Chrome writes such
    session cookies into the profile while it runs and deletes them as it starts again, unless its last run crashed:
    then it gives them back. So the profile's preferences say the last run crashed, and Chrome starts with
    :data:`HIDE_CRASH_BUBBLE`; it opens no page of that run (measured 2026-10-21 with Chrome 155, headless and with a
    window: a session cookie came back after each of five restarts, and no tab did). Incognito keeps nothing (item
    355), and a profile whose preferences hold nothing yet has nothing to keep: neither is written, unless ``create``
    says the profile was just given cookies (the import of a Chrome profile's, item 529), which Chrome would delete as
    it first starts on it. A new profile whose zoom was just written (:func:`write_default_zoom`) gets the word too, and
    Chrome has nothing to give back on it. Blocking, and while no Chrome holds the profile; the other preferences stay
    as they are."""
    if folder == browser_incognito_dir():
        return
    preferences: Path = folder / _PROFILE_DIR / _PREFERENCES
    kept: JsonObject = _preferences(preferences)
    if not kept and not create:
        return
    ending: JsonValue = kept.get(_PROFILE_KEY, {})
    if not isinstance(ending, dict):
        raise BrowserError(
            "Chrome's preferences of the profile keep its state in a way Dot does not know", {"path": str(preferences)}
        )
    if ending.get(_EXIT_TYPE) == _CRASHED and ending.get(_EXITED_CLEANLY) is False:
        return
    changed: JsonObject = {**kept, _PROFILE_KEY: {**ending, _EXIT_TYPE: _CRASHED, _EXITED_CLEANLY: False}}
    try:
        preferences.parent.mkdir(mode=_PROFILE_FOLDER_MODE, parents=True, exist_ok=True)
        write_whole(preferences, _JSON_OBJECT.dump_json(changed).decode("utf-8"))
    except OSError as error:
        raise BrowserError(
            "the session cookies could not be kept in Chrome's preferences of the profile",
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
