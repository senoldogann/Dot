"""The keychain that protects the cookies in Dot's browser profile.

Chrome encrypts every cookie it saves. An ordinary Chrome window takes the key from the macOS Keychain ("Chrome Safe
Storage"); Playwright starts Chrome with a mock keychain, a fixed key that never touches the Keychain. A profile written
with one key cannot be read with the other, so the window in which a person signs in and the automated sessions that use
that sign-in have to agree. The profile says which keychain it was written with in a marker file. A profile without one
is a profile Dot made itself and uses the mock keychain; ``dotx browser import-chrome`` marks the profile it fills with
the cookies of a personal Chrome profile as ``system``, because those cookies were written with the real key.
"""

from pathlib import Path
from typing import Literal

from dotagent.core.errors import BrowserError

KeychainMode = Literal["mock", "system"]

_MARKER_NAME: str = "dot-keychain"
# What Chrome needs to stay away from the Keychain; Playwright adds the same two switches on its own.
_MOCK_SWITCHES: tuple[str, ...] = ("--use-mock-keychain", "--password-store=basic")
_MARKER_PREVIEW_CHARS: int = 40


def read_keychain_mode(profile: Path) -> KeychainMode:
    """The keychain the cookies of ``profile`` are written with."""
    marker: Path = profile / _MARKER_NAME
    try:
        text: str = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "mock"
    except OSError as error:
        raise BrowserError(
            "the keychain marker of the browser profile could not be read", {"path": str(marker), "error": str(error)}
        ) from error
    match text:
        case "mock":
            return "mock"
        case "system":
            return "system"
        case _:
            raise BrowserError(
                "the keychain marker of the browser profile holds an unknown mode",
                {"path": str(marker), "content": text[:_MARKER_PREVIEW_CHARS], "expected": "mock or system"},
            )


def marker_path(profile: Path) -> Path:
    """The file in which ``profile`` records its keychain (a missing one reads as ``mock``)."""
    return profile / _MARKER_NAME


def write_keychain_mode(profile: Path, mode: KeychainMode) -> None:
    """Record the keychain the cookies of ``profile`` are written with. The profile directory must exist."""
    marker_path(profile).write_text(f"{mode}\n", encoding="utf-8")


def keychain_switches(mode: KeychainMode) -> list[str]:
    """Switches that put Chrome on the keychain of ``mode``; the system keychain is what Chrome does by itself."""
    match mode:
        case "mock":
            return list(_MOCK_SWITCHES)
        case "system":
            return []


def playwright_defaults_to_drop(mode: KeychainMode) -> list[str]:
    """Playwright's own default switches that would put the automated Chrome on a keychain other than ``mode``."""
    match mode:
        case "mock":
            return []
        case "system":
            return list(_MOCK_SWITCHES)
