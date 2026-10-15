"""What macOS lets Dot do with an application of the Mac (item 278): whether it is installed, and whether the app that
runs Dot may send it Apple events (Privacy & Security › Automation), read without a question or asked for. macOS tells
about an application only while it runs: one that does not is started hidden, without the front, to be asked about.
The questions go through ``AEDeterminePermissionToAutomateTarget``, which pyobjc does not wrap, so it is called with
ctypes."""

import ctypes
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import Literal

import AppKit

from dotagent.core.errors import DesktopError

type AutomationState = Literal["allowed", "not_asked", "refused", "not_open"]

_CORE_SERVICES: str = "/System/Library/Frameworks/CoreServices.framework/CoreServices"
# The answers of AEDeterminePermissionToAutomateTarget: noErr, errAEEventWouldRequireUserConsent,
# errAEEventNotPermitted, and procNotFound for an application that does not run.
_ANSWERS: dict[int, AutomationState] = {0: "allowed", -1744: "not_asked", -1743: "refused", -600: "not_open"}
# typeApplicationBundleID, and typeWildCard for any event class and id.
_BUNDLE_ID: str = "bund"
_ANY: str = "****"
_OPEN: str = "/usr/bin/open"
_OPEN_TIMEOUT_S: float = 10.0
# How long an application started hidden has to come up, and how often macOS is asked about it meanwhile.
_START_WAIT_S: float = 15.0
_START_POLL_S: float = 0.25


class _AEDesc(ctypes.Structure):
    _fields_ = (("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p))


def _code(text: str) -> int:
    """A four-character code of the Apple Event Manager as the number it stands for."""
    return int.from_bytes(text.encode("ascii"), "big")


@cache
def _core_services() -> ctypes.CDLL:
    library: ctypes.CDLL = ctypes.CDLL(_CORE_SERVICES)
    library.AECreateDesc.argtypes = (ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(_AEDesc))
    library.AECreateDesc.restype = ctypes.c_int16
    library.AEDisposeDesc.argtypes = (ctypes.POINTER(_AEDesc),)
    library.AEDisposeDesc.restype = ctypes.c_int16
    library.AEDeterminePermissionToAutomateTarget.argtypes = (
        ctypes.POINTER(_AEDesc),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_bool,
    )
    library.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
    return library


def automation_answer(status: int, bundle_id: str) -> AutomationState:
    """The state an answer of macOS about the application ``bundle_id`` tells; another answer is an error."""
    state: AutomationState | None = _ANSWERS.get(status)
    if state is None:
        raise DesktopError(
            "macOS gave an answer about Automation that Dot does not know", {"bundle_id": bundle_id, "status": status}
        )
    return state


def _determine(bundle_id: str, ask: bool) -> AutomationState:
    library: ctypes.CDLL = _core_services()
    target: _AEDesc = _AEDesc()
    data: bytes = bundle_id.encode("utf-8")
    made: int = library.AECreateDesc(_code(_BUNDLE_ID), data, len(data), ctypes.byref(target))
    if made != 0:
        raise DesktopError(
            "macOS could not name the application to ask about", {"bundle_id": bundle_id, "status": made}
        )
    try:
        status: int = library.AEDeterminePermissionToAutomateTarget(ctypes.byref(target), _code(_ANY), _code(_ANY), ask)
    finally:
        library.AEDisposeDesc(ctypes.byref(target))
    return automation_answer(status, bundle_id)


def automation_state(bundle_id: str) -> AutomationState:
    """Whether the app that runs Dot may control the application ``bundle_id``, read without showing anything."""
    return _determine(bundle_id, False)


def ask_automation(bundle_id: str) -> AutomationState:
    """Show macOS's question whether the app that runs Dot may control the application ``bundle_id``, and wait for the
    answer: it blocks until the owner answers. macOS asks once; an answer given before comes back at once."""
    return _determine(bundle_id, True)


def installed_app(bundle_id: str) -> Path | None:
    """Where Launch Services finds the application ``bundle_id``; ``None`` when it is not installed."""
    url: object = AppKit.NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle_id)
    return None if url is None else Path(str(AppKit.NSURL.path(url)))


def _open(bundle_id: str, flags: tuple[str, ...]) -> None:
    outcome: subprocess.CompletedProcess[str] = subprocess.run(
        [_OPEN, *flags, "-b", bundle_id], capture_output=True, text=True, timeout=_OPEN_TIMEOUT_S, check=False
    )
    if outcome.returncode != 0:
        raise DesktopError(
            "the application could not be opened",
            {"bundle_id": bundle_id, "code": outcome.returncode, "stderr": outcome.stderr.strip()},
        )


def open_app(bundle_id: str) -> None:
    """Open the application ``bundle_id`` as from the Dock, for the owner to use, or bring it up when it runs."""
    _open(bundle_id, ())


def start_hidden(bundle_id: str) -> AutomationState:
    """Start the application ``bundle_id`` hidden and without the front (``open -g -j``), for a question about it, and
    wait until macOS can tell about it; returns what it tells then, ``not_open`` when the application never came up."""
    _open(bundle_id, ("-g", "-j"))
    deadline: float = time.monotonic() + _START_WAIT_S
    while True:
        state: AutomationState = automation_state(bundle_id)
        if state != "not_open" or time.monotonic() >= deadline:
            return state
        time.sleep(_START_POLL_S)
