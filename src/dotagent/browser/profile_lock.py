"""Who holds a Chrome user data directory: Chrome marks the directory it runs on with a ``SingletonLock`` symlink. And
who may change it: an import of sign-ins takes the profile for as long as it swaps files in, and Dot's own Chrome takes
it while it starts (item 554), so that neither runs under the other."""

import contextlib
import fcntl
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from dotagent.core.errors import BrowserError

# The file in the profile folder that the import and the start of Dot's Chrome lock (``flock``: shared for a start,
# exclusive for an import). It stays empty; the kernel lets go of the lock when the process holding it ends.
IMPORT_LOCK: str = "import.lock"
_PROFILE_MODE: int = 0o700


def profile_holder_pid(profile: Path) -> int | None:
    """Pid of the live process that holds the Chrome profile lock, or ``None`` when the profile is free."""
    lock: Path = profile / "SingletonLock"
    try:
        target: str = os.readlink(lock)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BrowserError(
            "the browser profile lock could not be read", {"path": str(lock), "error": str(error)}
        ) from error
    owner: str = target.rsplit("-", 1)[-1]
    if not owner.isdigit():
        return None
    pid: int = int(owner)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


# What the command line of Playwright's driver holds: a Chrome it started is a run's (the daemon's, or a dotx
# command's), not a window opened on the profile (Sign in…, or the owner's).
_PLAYWRIGHT_DRIVER: str = "playwright/driver"
_PS_TIMEOUT_S: float = 5.0


def _ps(field: str, pid: int) -> str:
    """The column ``field`` of ``ps`` for ``pid``, empty when the process is gone or ``ps`` cannot say."""
    try:
        done: subprocess.CompletedProcess[str] = subprocess.run(
            ["/bin/ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            check=False,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip()


def started_by_dot(pid: int) -> bool:
    """Whether the Chrome ``pid`` is a run's: its parent is Playwright's driver. A window opened on the profile (Sign
    in…, or the owner's) is not, and neither is a Chrome whose parent cannot be read."""
    parent: str = _ps("ppid", pid)
    return parent.isdigit() and _PLAYWRIGHT_DRIVER in _ps("command", int(parent))


@contextlib.contextmanager
def _locked(profile: Path, mode: int, refusal: str) -> Iterator[None]:
    """Hold ``flock`` ``mode`` on the profile's lock file while the block runs, or raise ``refusal`` at once when
    another process holds it in a way that excludes this one."""
    profile.mkdir(mode=_PROFILE_MODE, parents=True, exist_ok=True)
    try:
        handle: int = os.open(profile / IMPORT_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        raise BrowserError(
            "the lock of the browser profile could not be opened", {"profile": str(profile), "error": str(error)}
        ) from error
    try:
        try:
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrowserError(refusal, {"profile": str(profile)}) from None
        yield
    finally:
        os.close(handle)


def held_for_import(profile: Path) -> contextlib.AbstractContextManager[None]:
    """The profile held for an import of sign-ins (blocking, item 554): nothing else imports into it, and Dot's own
    Chrome does not start on it (:func:`held_for_start`) while the files are swapped in. Refused when Dot's Chrome is
    starting on it now, or another import runs."""
    return _locked(
        profile,
        fcntl.LOCK_EX,
        "Dot's browser is starting on this profile, or another import is under way; try again in a moment",
    )


def held_for_start(profile: Path) -> contextlib.AbstractContextManager[None]:
    """The profile held while Dot's own Chrome starts on it (blocking, item 554), until the Chrome holds it itself:
    refused while an import is under way, which would otherwise swap the files of a profile a Chrome is starting on."""
    return _locked(
        profile,
        fcntl.LOCK_SH,
        "sign-ins are being imported into the Dot browser profile (dotx browser import-chrome); try again when the "
        "import has ended",
    )
