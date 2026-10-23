"""Dot.app (item 288): the small app launchd runs for the Dot service, so that macOS names Dot when the daemon asks for
a privacy permission, and keeps what the owner grants when Python changes.

macOS holds the program launchd starts responsible for all that program starts. When that program was the daemon's
Python, macOS's questions and System Settings named ``python3.13``, and a grant went to that Python binary alone: the
next one (a uv update, another version) had none. Dot.app's program (``dot_app.c``) starts the daemon's Python as its
child and waits for it, so that the app is responsible for the daemon. It is built on this Mac with clang (Xcode's
Command Line Tools) and signed ad hoc, and macOS knows an app signed ad hoc by the hash of its code: the app is built
again only when its source or its Info.plist change, and is otherwise left as it is, with what was granted to it.

``app_path``, ``app_program`` and ``info_plist`` are pure; ``ensure_app`` builds the app.
"""

import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

from dotagent.core.errors import ServiceError

APP_NAME: str = "Dot"
# The app's bundle identifier, the launchd job's label.
BUNDLE_ID: str = "dev.dotagent.dot"
SOURCE: Path = Path(__file__).parent / "dot_app.c"
BUILD_FOLDER: str = ".dot-app-build"

_XCRUN: str = "/usr/bin/xcrun"
_CODESIGN: str = "/usr/bin/codesign"
_BUILD_TIMEOUT_S: float = 120.0
_OUTPUT_CHARS: int = 600
# What macOS's questions say Dot does with what it asks for.
_FOLDER_USE: str = "Dot works with the files in this folder for you, and starts from it when it is installed there."


class InfoPlist(TypedDict):
    """The keys of the app's Info.plist, spelled the way Apple spells them."""

    CFBundleExecutable: str
    CFBundleIdentifier: str
    CFBundleName: str
    CFBundleDisplayName: str
    CFBundlePackageType: str
    CFBundleInfoDictionaryVersion: str
    CFBundleShortVersionString: str
    CFBundleVersion: str
    LSUIElement: bool
    NSAppleEventsUsageDescription: str
    NSDesktopFolderUsageDescription: str
    NSDocumentsFolderUsageDescription: str
    NSDownloadsFolderUsageDescription: str
    NSLocalNetworkUsageDescription: str


def app_path(home: Path) -> Path:
    """Where Dot.app is: in Dot's home."""
    return home / f"{APP_NAME}.app"


def build_path(home: Path) -> Path:
    """Where Dot.app is built and signed before it takes the old one's place: beside it, in Dot's home."""
    return home / BUILD_FOLDER


def app_program(app: Path) -> Path:
    """The program of the app at ``app``, which launchd runs."""
    return app / "Contents" / "MacOS" / APP_NAME


def info_plist() -> InfoPlist:
    """The app's Info.plist. Its version does not follow Dot's, since another Info.plist is another app for macOS;
    ``LSUIElement`` keeps the app out of the Dock."""
    return InfoPlist(
        CFBundleExecutable=APP_NAME,
        CFBundleIdentifier=BUNDLE_ID,
        CFBundleName=APP_NAME,
        CFBundleDisplayName=APP_NAME,
        CFBundlePackageType="APPL",
        CFBundleInfoDictionaryVersion="6.0",
        CFBundleShortVersionString="1.0",
        CFBundleVersion="1",
        LSUIElement=True,
        NSAppleEventsUsageDescription="Dot uses this application for you when you ask it to.",
        NSDesktopFolderUsageDescription=_FOLDER_USE,
        NSDocumentsFolderUsageDescription=_FOLDER_USE,
        NSDownloadsFolderUsageDescription=_FOLDER_USE,
        NSLocalNetworkUsageDescription="Dot answers your phone and your other devices on your network.",
    )


def render_info_plist() -> bytes:
    return plistlib.dumps(info_plist(), fmt=plistlib.FMT_XML, sort_keys=True)


def _clip(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _OUTPUT_CHARS else f"{flat[: _OUTPUT_CHARS - 1]}…"


def _run(arguments: list[str], failure: str, fix: str) -> None:
    outcome: subprocess.CompletedProcess[str] = subprocess.run(
        arguments, capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S, check=False
    )
    if outcome.returncode != 0:
        raise ServiceError(
            failure,
            {"command": " ".join(arguments), "code": outcome.returncode, "stderr": _clip(outcome.stderr), "fix": fix},
        )


def _current(app: Path, source: str, info: bytes) -> bool:
    """Whether the app at ``app`` was built and signed from ``source`` with the Info.plist ``info``."""
    kept_source: Path = app / "Contents" / "Resources" / SOURCE.name
    kept_info: Path = app / "Contents" / "Info.plist"
    return (
        app_program(app).is_file()
        and (app / "Contents" / "_CodeSignature").is_dir()
        and kept_source.is_file()
        and kept_source.read_text(encoding="utf-8") == source
        and kept_info.is_file()
        and kept_info.read_bytes() == info
    )


def ensure_app(home: Path) -> Path:
    """Dot.app in ``home``, built when it is missing or was built from another source or Info.plist; returns the path of
    its program. An app that is current is left as it is, so that what macOS granted it holds. A new app is built and
    signed aside, then takes the old one's place."""
    app: Path = app_path(home)
    source: str = SOURCE.read_text(encoding="utf-8")
    info: bytes = render_info_plist()
    if _current(app, source, info):
        return app_program(app)
    aside: Path = build_path(home)
    if aside.exists():
        shutil.rmtree(aside)
    built: Path = aside / app.name
    contents: Path = built / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    (contents / "Info.plist").write_bytes(info)
    kept_source: Path = contents / "Resources" / SOURCE.name
    kept_source.write_text(source, encoding="utf-8")
    _run(
        [_XCRUN, "clang", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(app_program(built)), str(kept_source)],
        "Dot.app could not be built: clang failed",
        "install Xcode's Command Line Tools with 'xcode-select --install', then run 'dotx service install' again",
    )
    _run(
        [_CODESIGN, "--force", "--sign", "-", str(built)],
        "Dot.app could not be signed",
        "codesign's error is in stderr; run 'dotx service install' again once it is fixed",
    )
    old: Path = aside / f"{app.name}.old"
    if app.exists():
        app.rename(old)
    built.rename(app)
    shutil.rmtree(aside)
    return app_program(app)
