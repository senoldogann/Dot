"""Where a filesystem path sits relative to the workspace, the user's home and protected locations."""

import os
import tempfile
from pathlib import Path
from typing import Literal

PathClass = Literal["workspace", "temp", "home", "system", "protected"]

_SYSTEM_PREFIXES: tuple[str, ...] = (
    "/System",
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/libexec",
    "/etc",
    "/private/etc",
    "/Library",
    "/Applications",
    "/opt",
    "/var/db",
    "/private/var/db",
)

_TEMP_PREFIXES: tuple[str, ...] = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


def expand_path(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` (``~`` and ``$VAR`` aware) against ``cwd`` without requiring it to exist."""
    expanded: str = os.path.expandvars(os.path.expanduser(raw))
    candidate: Path = Path(expanded)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return Path(os.path.realpath(candidate))


def expand_protected(raw_paths: list[str], extra: tuple[Path, ...]) -> tuple[Path, ...]:
    expanded: list[Path] = [Path(os.path.realpath(Path(raw).expanduser())) for raw in raw_paths]
    expanded.extend(Path(os.path.realpath(path)) for path in extra)
    return tuple(expanded)


def _under(path: Path, prefix: Path) -> bool:
    return path == prefix or prefix in path.parents


def _is_temp(path: Path) -> bool:
    roots: list[Path] = [Path(os.path.realpath(prefix)) for prefix in _TEMP_PREFIXES]
    roots.append(Path(os.path.realpath(tempfile.gettempdir())))
    return any(_under(path, root) for root in roots)


def _is_system(path: Path) -> bool:
    return any(_under(path, Path(os.path.realpath(prefix))) for prefix in _SYSTEM_PREFIXES)


def classify_path(path: Path, workspace: Path, protected: tuple[Path, ...]) -> PathClass:
    """Classify an already resolved absolute path."""
    if any(_under(path, location) for location in protected):
        return "protected"
    if _under(path, Path(os.path.realpath(workspace))):
        return "workspace"
    if _is_temp(path):
        return "temp"
    if _is_system(path):
        return "system"
    return "home"


def mentions_protected(text: str, protected: tuple[Path, ...]) -> bool:
    """True if free text (a shell command, a script) names a protected location in any common spelling."""
    home: str = str(Path.home())
    for location in protected:
        absolute: str = str(location)
        spellings: list[str] = [absolute]
        if absolute.startswith(home):
            relative: str = absolute[len(home) :]
            spellings.extend((f"~{relative}", f"$HOME{relative}", f"${{HOME}}{relative}"))
        if any(spelling in text for spelling in spellings):
            return True
    return False
