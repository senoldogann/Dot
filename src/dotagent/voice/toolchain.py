"""Whether this Mac can write a recording down, and the compiler that builds the program that does it (item 626).

It can when all three hold, and otherwise says which is missing, in words an owner can act on:

- macOS 26 or later, which has the dictation model (``DictationTranscriber``);
- Apple's developer tools, which ``xcode-select -p`` says are installed. Without them macOS's ``swiftc`` is a stub that
  opens a dialog on the owner's screen asking to install them, so nothing but ``xcode-select -p`` is run before that is
  known to succeed;
- a Swift compiler and a macOS SDK in them, which ``xcrun --find swiftc`` and ``xcrun --sdk macosx --show-sdk-path``
  name by their full paths. The compiler is run by that path, never the stub at ``/usr/bin/swiftc``, and is told the
  SDK: started directly, in the small environment of :func:`small_environment`, it cannot find the standard library by
  itself (the stub does, by setting ``SDKROOT``).
"""

import asyncio
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The first macOS with the dictation model the program writes with.
FIRST_MACOS: int = 26
_XCODE_SELECT: str = "/usr/bin/xcode-select"
_XCRUN: str = "/usr/bin/xcrun"
_PROBE_TIMEOUT_S: float = 10.0
INSTALL_TOOLS: str = "Install Apple's developer tools on the Mac: xcode-select --install"
OLD_MACOS: str = f"This Mac needs macOS {FIRST_MACOS} or later"
NO_COMPILER: str = (
    "Apple's developer tools on this Mac cannot build programs: install Xcode, or the tools with xcode-select --install"
)
# What a program started here is given of the daemon's own environment: who runs it and where its files go, with a
# ``PATH`` of the system's own folders, as an MCP server is given a short list of variables and the shell's ``PATH``.
_SYSTEM_PATH: str = "/usr/bin:/bin:/usr/sbin:/sbin"
_KEPT_VARIABLES: tuple[str, ...] = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG")


def small_environment() -> dict[str, str]:
    """The environment of every program this package starts: nothing of the daemon's own (a key of its among it) but
    ``_KEPT_VARIABLES``."""
    return {"PATH": _SYSTEM_PATH, **{name: os.environ[name] for name in _KEPT_VARIABLES if name in os.environ}}


@dataclass(frozen=True)
class Toolchain:
    """What builds the program: the ``compiler`` and the macOS ``sdk`` it is told (``None`` for a compiler named by
    ``DOT_SWIFTC``, which is run as it is), or, when there is none, ``why`` in plain words."""

    compiler: Path | None
    why: str | None
    sdk: Path | None = None


def compiler_override() -> str | None:
    """The compiler ``DOT_SWIFTC`` names, so that tests point at their own and never build with the owner's: it is used
    as it is, without the checks of the Mac that follow."""
    override: str | None = os.environ.get("DOT_SWIFTC")
    return override if override is not None and override != "" else None


def _macos_major() -> int:
    """The Mac's major version of macOS, 0 where it cannot be read."""
    version: str = platform.mac_ver()[0]
    head: str = version.split(".")[0]
    return int(head) if head.isdigit() else 0


def _asked(arguments: list[str]) -> str | None:
    """What the command printed, or ``None`` when it could not run, failed or took too long."""
    try:
        done: subprocess.CompletedProcess[str] = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
            env=small_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _executable(path: str) -> Path | None:
    """The file at ``path`` when it is a program that can be run."""
    file: Path = Path(path)
    return file if file.is_absolute() and file.is_file() and os.access(file, os.X_OK) else None


def _folder(path: str | None) -> Path | None:
    """The folder at ``path``, when one is named and is there."""
    return Path(path) if path is not None and Path(path).is_absolute() and Path(path).is_dir() else None


def look_for_compiler() -> Toolchain:
    """What this Mac has to build the program with, found by the steps of the module's docstring in order, each only if
    the one before it held."""
    override: str | None = compiler_override()
    if override is not None:
        found: Path | None = _executable(override)
        return Toolchain(found, None if found is not None else NO_COMPILER)
    if _macos_major() < FIRST_MACOS:
        return Toolchain(None, OLD_MACOS)
    if _folder(_asked([_XCODE_SELECT, "-p"])) is None:
        return Toolchain(None, INSTALL_TOOLS)
    named: str | None = _asked([_XCRUN, "--find", "swiftc"])
    compiler: Path | None = None if named is None else _executable(named)
    sdk: Path | None = _folder(_asked([_XCRUN, "--sdk", "macosx", "--show-sdk-path"]))
    if compiler is None or sdk is None:
        return Toolchain(None, NO_COMPILER)
    return Toolchain(compiler, None, sdk)


async def find_compiler() -> Toolchain:
    """:func:`look_for_compiler`, off the event loop: it starts programs and waits for them."""
    return await asyncio.to_thread(look_for_compiler)
