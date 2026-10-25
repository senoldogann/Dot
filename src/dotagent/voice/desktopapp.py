"""Whether macOS can give the desktop app the microphone, and System Settings shown where it is allowed (item 636).

The composer's microphone in the desktop app (Dot.app, bundle ``dev.dot.desktop``) is WebKit's speech recognition.
WebKit refuses the microphone and speech recognition to an app that does not say why it asks for them
(``NSMicrophoneUsageDescription`` and ``NSSpeechRecognitionUsageDescription`` in its ``Info.plist``) before it asks
macOS, and under the hardened runtime macOS gives the microphone only to an app entitled to it
(``com.apple.security.device.audio-input``). A Dot.app built before item 594 has neither, and System Settings can list
it as allowed all the same: showing the owner System Settings for it fixes nothing, and only a newer Dot.app does.

:class:`DesktopApp` finds the Dot.app that runs (``NSRunningApplication.runningApplicationsWithBundleIdentifier_``,
which asks Launch Services each time: from a process with no run loop, as the daemon is, it saw an app start and end
within a second, where ``NSWorkspace.runningApplications`` saw neither) and says whether it *listens*: both reasons are
in its ``Info.plist``, and its signature has no hardened runtime or carries the microphone entitlement (``codesign``).
What was read of an app is kept while its ``Info.plist`` and the seal of its signature stay as they were.
:func:`open_url` shows a pane of System Settings. The programs that are run, the apps that are found and the opener are
given, so that a test needs no real app and opens nothing; ``DOT_OPEN`` names another ``open`` for the daemon of a
test, as for the other pages the daemon opens.
"""

import plistlib
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.parsers.expat import ExpatError

import AppKit
from pydantic import BaseModel, ConfigDict

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.desktop.show_dot import DESKTOP_APP
from dotagent.tailnet import open_program

log = get_logger(__name__)

# The panes of System Settings › Privacy & Security where Dot is switched on: for the microphone, and for speech
# recognition.
type VoicePane = Literal["microphone", "speech"]
_SETTINGS: str = "x-apple.systempreferences:com.apple.preference.security?"
_PANES: dict[VoicePane, str] = {"microphone": "Privacy_Microphone", "speech": "Privacy_SpeechRecognition"}
# The desktop app that runs: ``ready``, it listens, so that what is left to the owner is to switch Dot on in System
# Settings; ``old``, it cannot be given the microphone whatever System Settings shows; ``unknown``, none runs under its
# bundle identifier (a build that is not a bundle, or one that just ended).
type AppState = Literal["ready", "old", "unknown"]

# What a program is run with: its argument list (never a shell), to its end.
type Run = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
# The bundles (``.../Dot.app``) of the applications that run under a bundle identifier.
type Running = Callable[[str], Sequence[Path]]
# What shows an address (``open``).
type Opener = Callable[[str], None]
_CODESIGN: str = "/usr/bin/codesign"
# ``codesign`` reads files and ``open`` hands an address to the system: ten seconds is long for either.
_TIMEOUT_S: float = 10.0
_INFO_PLIST: Path = Path("Contents") / "Info.plist"
# The file ``codesign`` rewrites each time it signs a bundle: it tells a bundle signed again from one left alone.
_SEAL: Path = Path("Contents") / "_CodeSignature" / "CodeResources"
AUDIO_INPUT: str = "com.apple.security.device.audio-input"
# ``CodeDirectory v=20500 size=36187 flags=0x10000(runtime) hashes=...``: the names of the flags are in the brackets.
_FLAGS: re.Pattern[str] = re.compile(r"\bflags=0x[0-9a-fA-F]+\((?P<names>[^)]*)\)")
_HARDENED_RUNTIME: str = "runtime"
_NOT_SIGNED: str = "not signed at all"
_ERROR_CHARS: int = 400


def settings_url(pane: VoicePane) -> str:
    """The address of the pane of System Settings where Dot is allowed the microphone, or speech recognition."""
    return _SETTINGS + _PANES[pane]


class _Reasons(BaseModel):
    """The keys of an ``Info.plist`` that say why the app asks for the microphone and for speech recognition."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    NSMicrophoneUsageDescription: str = ""
    NSSpeechRecognitionUsageDescription: str = ""


class Signature(FrozenModel):
    """What the signature of an app says that decides whether macOS gives it the microphone: ``hardened``, it is signed
    with the hardened runtime, which keeps the microphone from an app that is not entitled to it, and ``audio_input``,
    it carries the entitlement that entitles it."""

    hardened: bool
    audio_input: bool

    @property
    def allows_microphone(self) -> bool:
        return not self.hardened or self.audio_input


@dataclass(frozen=True)
class _Seen:
    """Whether an app listens, with the state of the files that decided it then."""

    files: tuple[int, int]
    listens: bool


def run_program(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """``arguments`` run to their end; a DesktopError when the program is not there or does not answer in time."""
    program: str = Path(arguments[0]).name
    try:
        return subprocess.run(
            list(arguments),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as error:
        raise DesktopError(f"{program} did not answer in {_TIMEOUT_S:g} s", {"command": " ".join(arguments)}) from error
    except OSError as error:
        raise DesktopError(
            f"{program} could not be run: {error.strerror or error}", {"command": " ".join(arguments)}
        ) from error


def running_apps(bundle_id: str) -> tuple[Path, ...]:
    """The bundles of the applications that run under ``bundle_id``, each once."""
    found: list[Path] = []
    for application in AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id):
        url = application.bundleURL()
        if url is None or application.isTerminated():
            continue
        bundle: Path = Path(str(url.path()))
        if bundle not in found:
            found.append(bundle)
    return tuple(found)


def open_url(url: str) -> None:
    """Show ``url`` with macOS's ``open`` (the program ``DOT_OPEN`` names, if it names one); a DesktopError when it
    cannot."""
    done: subprocess.CompletedProcess[str] = run_program([open_program(), url])
    if done.returncode != 0:
        raise DesktopError(
            "macOS could not open System Settings",
            {"url": url, "code": done.returncode, "stderr": truncate_middle(done.stderr.strip(), _ERROR_CHARS)},
        )


def _said(done: subprocess.CompletedProcess[str]) -> str:
    return truncate_middle((done.stderr + done.stdout).strip(), _ERROR_CHARS)


def _reasons_given(info: Path) -> bool:
    """Whether the ``Info.plist`` at ``info`` says why the app asks for the microphone and for speech recognition, each
    in words."""
    try:
        reasons: _Reasons = _Reasons.model_validate(plistlib.loads(info.read_bytes()))
    except (OSError, ExpatError, ValueError) as error:
        raise DesktopError(
            "the Info.plist of the desktop app could not be read", {"file": str(info), "error": str(error)}
        ) from error
    return (
        reasons.NSMicrophoneUsageDescription.strip() != "" and reasons.NSSpeechRecognitionUsageDescription.strip() != ""
    )


def _entitled(xml: str, app: Path) -> bool:
    """Whether the entitlements ``codesign`` printed (a plist; nothing when there are none) hold the microphone's."""
    if xml.strip() == "":
        return False
    try:
        entitlements: object = plistlib.loads(xml.encode("utf-8"))
    except (ExpatError, ValueError) as error:
        raise DesktopError(
            "the entitlements of the desktop app could not be read",
            {"app": str(app), "said": truncate_middle(xml, _ERROR_CHARS), "error": str(error)},
        ) from error
    return isinstance(entitlements, dict) and entitlements.get(AUDIO_INPUT) is True


def _files(app: Path) -> tuple[int, int]:
    """When the files that decide whether the app listens last changed: its ``Info.plist`` and the seal of its
    signature (0 for an app with none)."""
    try:
        info: int = (app / _INFO_PLIST).stat().st_mtime_ns
    except OSError as error:
        raise DesktopError(
            "the Info.plist of the desktop app could not be read", {"app": str(app), "error": str(error)}
        ) from error
    try:
        seal: int = (app / _SEAL).stat().st_mtime_ns
    except FileNotFoundError:
        seal = 0
    return info, seal


class DesktopApp:
    """The desktop app that runs under ``bundle_id`` and whether macOS can give it the microphone. Blocking: it reads
    files and runs ``codesign``, so a server calls it off the event loop."""

    def __init__(self, bundle_id: str = DESKTOP_APP, running: Running = running_apps, run: Run = run_program) -> None:
        self._bundle_id: str = bundle_id
        self._running: Running = running
        self._run: Run = run
        self._seen: dict[Path, _Seen] = {}

    def state(self) -> AppState:
        """``unknown`` when none runs, ``ready`` when one that runs listens, else ``old``."""
        running: Sequence[Path] = self._running(self._bundle_id)
        if not running:
            return "unknown"
        return "ready" if any(self.listens(app) for app in running) else "old"

    def listens(self, app: Path) -> bool:
        """Whether the app at ``app`` can be given the microphone and speech recognition: it says why it asks for both,
        and either is not under the hardened runtime or is entitled to the microphone under it."""
        files: tuple[int, int] = _files(app)
        seen: _Seen | None = self._seen.get(app)
        if seen is not None and seen.files == files:
            return seen.listens
        reasons: bool = _reasons_given(app / _INFO_PLIST)
        signature: Signature | None = self.signature(app) if reasons else None
        listens: bool = signature is not None and signature.allows_microphone
        self._seen[app] = _Seen(files, listens)
        log.info(
            "desktop_app_read",
            app=str(app),
            reasons=reasons,
            hardened=None if signature is None else signature.hardened,
            audio_input=None if signature is None else signature.audio_input,
            listens=listens,
        )
        return listens

    def signature(self, app: Path) -> Signature:
        """What the signature of the app at ``app`` says: whether it has the hardened runtime and, if it has, whether
        it is entitled to the microphone. An app that is not signed has no runtime to keep the microphone from it."""
        flags: subprocess.CompletedProcess[str] = self._run([_CODESIGN, "-dv", str(app)])
        if flags.returncode != 0:
            if _NOT_SIGNED in flags.stderr:
                return Signature(hardened=False, audio_input=False)
            raise DesktopError(
                "codesign could not read the signature of the desktop app",
                {"app": str(app), "code": flags.returncode, "said": _said(flags)},
            )
        named: re.Match[str] | None = _FLAGS.search(flags.stderr)
        if named is None:
            raise DesktopError(
                "codesign did not say the flags of the signature of the desktop app",
                {"app": str(app), "said": _said(flags)},
            )
        if _HARDENED_RUNTIME not in named["names"].split(","):
            return Signature(hardened=False, audio_input=False)
        entitlements: subprocess.CompletedProcess[str] = self._run(
            [_CODESIGN, "-d", "--entitlements", "-", "--xml", str(app)]
        )
        if entitlements.returncode != 0:
            raise DesktopError(
                "codesign could not read the entitlements of the desktop app",
                {"app": str(app), "code": entitlements.returncode, "said": _said(entitlements)},
            )
        return Signature(hardened=True, audio_input=_entitled(entitlements.stdout, app))
