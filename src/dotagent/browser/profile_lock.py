"""Who holds a Chrome user data directory: Chrome marks the directory it runs on with a ``SingletonLock`` symlink."""

import os
from pathlib import Path

from dotagent.core.errors import BrowserError


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
