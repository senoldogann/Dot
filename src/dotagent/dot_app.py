"""Dot.app (item 288): the small app launchd runs for the Dot service, so that macOS names Dot when the daemon asks for
a privacy permission, and keeps what the owner grants when Python changes.

macOS holds the program launchd starts responsible for all that program starts. When that program was the daemon's
Python, macOS's questions and System Settings named ``python3.13``, and a grant went to that Python binary alone: the
next one (a uv update, another version) had none. Dot.app's program (``dot_app.c``) starts the daemon's Python as its
child and waits for it, so that the app is responsible for the daemon. It is built on this Mac with clang (Xcode's
Command Line Tools) and signed ad hoc, and macOS knows an app signed ad hoc by the hash of its code: the app is built
again only when its source or its Info.plist change, and is otherwise left as it is, with what was granted to it.

The desktop app (item 575) installs Dot on a Mac that has no Xcode tools: it carries the program already built,
universal and signed, and ``ensure_app`` takes it (``launcher``) in place of the clang build. The bundle is then signed
ad hoc and kept as before; a launcher other than the one the app was made from takes the old app's place. The program is
kept in the app as well, so that ``dotx service install`` typed in a terminal, which is given no launcher, makes the app
again from the program it was made from and does not fall back to clang, which that Mac has not.

``app_path``, ``app_program`` and ``info_plist`` are pure; ``ensure_app`` builds the app.
"""

import hashlib
import os
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
# Where the desktop app installs Dot's Python and its package, in Dot's home (item 575; the shell writes the same name).
RUNTIME_FOLDER: str = "runtime"
# The folder of links to the tools the desktop app carries (ripgrep, uv), in the runtime folder (item 575; the shell
# writes the same name).
TOOLS_FOLDER: str = "app-tools"
# The variable that names the prebuilt program when ``dotx service install`` is not given ``--launcher``.
LAUNCHER_ENV: str = "DOT_APP_LAUNCHER"
# What a prebuilt program is kept beside in the app: the hash of the program the app was made from, and the program
# itself, as the desktop app carried it.
_LAUNCHER_DIGEST: str = "launcher.sha256"
_LAUNCHER_COPY: str = "launcher.program"
# The first four bytes of a Mach-O program, thin (64-bit) or universal, either byte order.
_MACHO_MAGICS: frozenset[bytes] = frozenset(
    {
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)
_PROGRAM_MODE: int = 0o755

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


def runtime_path(home: Path) -> Path:
    """Where the desktop app installs Dot's package, the Python it runs on, what uv downloaded for them and the marker
    of the install (item 575): in Dot's home. The daemon runs from it, as the launcher does from Dot.app, so a run's
    commands may not change it."""
    return home / RUNTIME_FOLDER


def tools_path(home: Path) -> Path:
    """The folder of links to the tools the desktop app carries, which the daemon's job puts first on its ``PATH`` (item
    575): in the runtime folder, made again by the app at each of its starts. It is there only where the app installed
    Dot."""
    return runtime_path(home) / TOOLS_FOLDER


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


def read_launcher(launcher: Path) -> bytes:
    """The bytes of the prebuilt program ``launcher`` (the one the desktop app carries): a program, named by a full
    path, that macOS can run. Anything else is refused with what to do, before any app is touched."""
    context_of: dict[str, str] = {"launcher": str(launcher)}
    if not launcher.is_absolute():
        raise ServiceError(
            "the launcher is not named by a full path",
            {**context_of, "fix": "give the full path of the program, such as the desktop app's own"},
        )
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ServiceError(
            "the launcher is not a program that can be run",
            {**context_of, "fix": "name the executable file the desktop app carries, or leave --launcher out"},
        )
    program: bytes = launcher.read_bytes()
    if program[:4] not in _MACHO_MAGICS:
        raise ServiceError(
            "the launcher is not a macOS program",
            {**context_of, "fix": "name the executable file the desktop app carries, or leave --launcher out"},
        )
    return program


def kept_launcher(app: Path) -> bytes | None:
    """The prebuilt program the app at ``app`` was made from, as it is kept in the app; ``None`` for an app built with
    clang, one made before the program was kept, and a kept program that is not the one its hash says."""
    resources: Path = app / "Contents" / "Resources"
    kept: Path = resources / _LAUNCHER_COPY
    kept_digest: Path = resources / _LAUNCHER_DIGEST
    if not kept.is_file() or not kept_digest.is_file():
        return None
    program: bytes = kept.read_bytes()
    if program[:4] not in _MACHO_MAGICS or hashlib.sha256(program).hexdigest() != kept_digest.read_text(
        encoding="utf-8"
    ):
        return None
    return program


def has_kept_launcher(home: Path) -> bool:
    """Whether the Dot.app in ``home`` was made from a prebuilt program that it keeps: ``dotx service install`` then
    needs no Xcode tools, with a launcher named or without."""
    return kept_launcher(app_path(home)) is not None


def _current(app: Path, source: str | None, info: bytes, digest: str | None) -> bool:
    """Whether the app at ``app`` was built and signed from ``source`` with the Info.plist ``info``, and, when
    ``digest`` names the prebuilt program the app is to hold, from that program. ``source`` ``None`` leaves the source
    unasked: an app made from a prebuilt program was not made from it."""
    kept_source: Path = app / "Contents" / "Resources" / SOURCE.name
    kept_info: Path = app / "Contents" / "Info.plist"
    kept_digest: Path = app / "Contents" / "Resources" / _LAUNCHER_DIGEST
    return (
        app_program(app).is_file()
        and (app / "Contents" / "_CodeSignature").is_dir()
        and (source is None or (kept_source.is_file() and kept_source.read_text(encoding="utf-8") == source))
        and kept_info.is_file()
        and kept_info.read_bytes() == info
        and (digest is None or (kept_digest.is_file() and kept_digest.read_text(encoding="utf-8") == digest))
    )


def ensure_app(home: Path, launcher: Path | None = None) -> Path:
    """Dot.app in ``home``, built when it is missing or was built from another source or Info.plist; returns the path of
    its program. An app that is current is left as it is, so that what macOS granted it holds. A new app is built and
    signed aside, then takes the old one's place.

    With ``launcher`` (item 575), the program is that prebuilt one, copied in: clang is not run, so Xcode's tools are
    not needed. The app is current when it was made from that very program (its hash is kept in the app); a call
    without ``launcher`` leaves an app made from one as it is. The program is kept in the app, and a call without
    ``launcher`` that finds the app made from one but out of date (Dot's Info.plist changed) makes it again from the
    kept program: a ``dotx service install`` typed in a terminal does not need clang, which a Mac the desktop app
    installed Dot on has not. A program that changed is the desktop app's to bring, with ``launcher``."""
    app: Path = app_path(home)
    source: str = SOURCE.read_text(encoding="utf-8")
    info: bytes = render_info_plist()
    prebuilt: bytes | None = None if launcher is None else read_launcher(launcher)
    digest: str | None = None if prebuilt is None else hashlib.sha256(prebuilt).hexdigest()
    if _current(app, source, info, digest):
        return app_program(app)
    if prebuilt is None:
        prebuilt = kept_launcher(app)
        if prebuilt is not None:
            # Made from a program that did not come from this package's source: the source does not decide.
            digest = hashlib.sha256(prebuilt).hexdigest()
            if _current(app, None, info, digest):
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
    if prebuilt is None:
        _run(
            [_XCRUN, "clang", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(app_program(built)), str(kept_source)],
            "Dot.app could not be built: clang failed",
            "install Xcode's Command Line Tools with 'xcode-select --install', or, where the Dot desktop app is "
            "installed, give the program it carries with 'dotx service install --launcher "
            "/Applications/Dot.app/Contents/Resources/tools/dot-launcher' (its folder, if the app is elsewhere)",
        )
    else:
        app_program(built).write_bytes(prebuilt)
        os.chmod(app_program(built), _PROGRAM_MODE)
        (contents / "Resources" / _LAUNCHER_DIGEST).write_text(str(digest), encoding="utf-8")
        (contents / "Resources" / _LAUNCHER_COPY).write_bytes(prebuilt)
        os.chmod(contents / "Resources" / _LAUNCHER_COPY, _PROGRAM_MODE)
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
