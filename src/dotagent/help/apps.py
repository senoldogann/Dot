"""The applications of this Mac that AppleScript can drive, each told by its own scripting dictionary (item 163).

An application is scriptable when its Info.plist names its dictionary file (``OSAScriptingDefinition``, an ``.sdef``
file in its resources) or turns AppleScript on (``NSAppleScriptEnabled``). The commands and the classes of the
dictionary's own suites say what the application can do and are listed; the terms of the suites that many applications
share (:data:`_SHARED_SUITES`) are ranked, not listed. Every application's ``use`` names ``sdef`` with its path, which
prints its whole dictionary, also the one of an older application that keeps it in no file of its own.
"""

import plistlib
import re
import shlex
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.help.types import HelpItem

log = get_logger(__name__)

# Where applications are installed, in the order they are looked at: of two applications with one name, the first.
APP_ROOTS: tuple[Path, ...] = (
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path("/System/Library/CoreServices"),
    Path.home() / "Applications",
)
_MAX_DICTIONARY_BYTES: int = 2 * 1024 * 1024
_SHARED_SUITES: frozenset[str] = frozenset({"standard suite", "text suite", "type definitions", "type names suite"})
_TRUE_TEXTS: frozenset[str] = frozenset({"yes", "true", "1"})
# An application's line names this many of its own commands, and as many of its classes, at most.
_LISTED_TERMS: int = 12
_TERM_CHARS: int = 40
_NOT_A_TERM: re.Pattern[str] = re.compile(r"[^\w -]+")


class _Dictionary(FrozenModel):
    """What a dictionary file holds: the commands and the classes of its own suites, in their order, and the terms of
    the suites that many applications share."""

    commands: tuple[str, ...]
    classes: tuple[str, ...]
    shared: tuple[str, ...]


def _term(name: str) -> str:
    """A term as a line shows it: its letters, digits, spaces and hyphens, at most :data:`_TERM_CHARS` of them."""
    return " ".join(_NOT_A_TERM.sub(" ", name).split())[:_TERM_CHARS]


def _read_dictionary(data: bytes) -> _Dictionary:
    """The terms of a dictionary file (sdef XML) that it does not hide. Raises :class:`ElementTree.ParseError` for a
    file that is not XML."""
    commands: list[str] = []
    classes: list[str] = []
    shared: list[str] = []
    for suite in ElementTree.fromstring(data).iter("suite"):
        if suite.get("hidden") == "yes":
            continue
        common: bool = suite.get("name", "").strip().lower() in _SHARED_SUITES
        for element in suite:
            name: str = _term(element.get("name", ""))
            if element.tag not in ("command", "class") or element.get("hidden") == "yes" or not name:
                continue
            if common:
                shared.append(name)
            elif element.tag == "command":
                commands.append(name)
            else:
                classes.append(name)
    return _Dictionary(
        commands=tuple(dict.fromkeys(commands)),
        classes=tuple(dict.fromkeys(classes)),
        shared=tuple(dict.fromkeys(shared)),
    )


def _listed(terms: Sequence[str]) -> str:
    more: int = len(terms) - _LISTED_TERMS
    shown: str = ", ".join(terms[:_LISTED_TERMS])
    return shown if more <= 0 else f"{shown} and {more} more"


def _summary(dictionary: _Dictionary) -> str:
    parts: list[str] = [
        *([f"commands {_listed(dictionary.commands)}"] if dictionary.commands else []),
        *([f"classes {_listed(dictionary.classes)}"] if dictionary.classes else []),
    ]
    if not parts:
        return "scriptable; its dictionary has only the suites that many applications share"
    return "; ".join(["scriptable", *parts])


def _told(bundle: Path, definition: object) -> tuple[str, tuple[str, ...]]:
    """What the line of the application in ``bundle`` says of its dictionary, whose file Info.plist names
    ``definition`` in the bundle's resources, and the terms the application is ranked by. A name that leads out of
    the bundle names no file of it."""
    path: Path | None = bundle / "Contents" / "Resources" / definition if isinstance(definition, str) else None
    if path is None or not path.is_file() or not path.resolve().is_relative_to(bundle.resolve()):
        return "scriptable; its dictionary is in no file of its own", ()
    try:
        if path.stat().st_size > _MAX_DICTIONARY_BYTES:
            return f"scriptable; its dictionary file is larger than {_MAX_DICTIONARY_BYTES // (1024 * 1024)} MB", ()
        dictionary: _Dictionary = _read_dictionary(path.read_bytes())
    except (OSError, ElementTree.ParseError) as error:
        return f"scriptable; its dictionary file could not be read ({type(error).__name__})", ()
    return _summary(dictionary), (*dictionary.commands, *dictionary.classes, *dictionary.shared)


def bundle_info(bundle: Path) -> dict[str, object] | None:
    """The Info.plist of ``bundle``; ``None`` for a folder without one, which is no application, and for one that
    cannot be read, which is logged."""
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            info: object = plistlib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, ExpatError) as error:
        log.warning("app_info_unreadable", app=str(bundle), error=type(error).__name__)
        return None
    if not isinstance(info, dict):
        return None
    return {str(key): value for key, value in info.items()}


def _scriptable(info: dict[str, object]) -> bool:
    enabled: object = info.get("NSAppleScriptEnabled")
    return (
        isinstance(info.get("OSAScriptingDefinition"), str)
        or enabled is True
        or (isinstance(enabled, str) and enabled.strip().lower() in _TRUE_TEXTS)
    )


def _applescript_text(text: str) -> str:
    """``text`` as an AppleScript string."""
    escaped: str = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _app(bundle: Path, info: dict[str, object]) -> HelpItem:
    name: str = bundle.stem
    summary, terms = _told(bundle, info.get("OSAScriptingDefinition"))
    return HelpItem(
        kind="app",
        name=name,
        summary=summary,
        use=(
            f"apple_script with tell application {_applescript_text(name)} to ...; sdef {shlex.quote(str(bundle))} in "
            "the shell prints its whole dictionary"
        ),
        text=" ".join([name, name, *terms]),
    )


def app_identities(roots: Sequence[Path]) -> dict[str, frozenset[str]]:
    """The bundle ids of the applications in ``roots`` (the ``.app`` bundles right in each one), by each name a script
    may call one by, case folded: its bundle's name without ``.app``, its ``CFBundleName`` and its
    ``CFBundleDisplayName``. A name two applications share names both."""
    found: dict[str, set[str]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for bundle in sorted(root.glob("*.app")):
            info: dict[str, object] | None = bundle_info(bundle)
            identifier: object = None if info is None else info.get("CFBundleIdentifier")
            if info is None or not isinstance(identifier, str):
                continue
            for name in (bundle.stem, info.get("CFBundleName"), info.get("CFBundleDisplayName")):
                if isinstance(name, str) and name.strip():
                    found.setdefault(name.strip().casefold(), set()).add(identifier)
    return {name: frozenset(identifiers) for name, identifiers in found.items()}


def scriptable_apps(roots: Sequence[Path]) -> list[HelpItem]:
    """The scriptable applications in ``roots`` (the ``.app`` bundles right in each one; a root that does not exist
    holds none), each name once: of two applications with one name, only the one of the first root counts, scriptable
    or not."""
    found: list[HelpItem] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for bundle in sorted(root.glob("*.app")):
            info: dict[str, object] | None = None if bundle.stem in seen else bundle_info(bundle)
            if info is None:
                continue
            seen.add(bundle.stem)
            if _scriptable(info):
                found.append(_app(bundle, info))
    return found
