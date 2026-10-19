"""Where a filesystem path sits relative to the workspace, the user's home, Dot's own folders and protected
locations."""

import fcntl
import fnmatch
import os
import tempfile
from pathlib import Path
from typing import Literal

from dotagent.core.errors import PolicyDenied

# ``dot`` is what Dot loads and runs, and its emergency stop (``Runtime.dot_paths``): a run reads it as it reads the
# home folder, and changes it as it would a protected location.
PathClass = Literal["workspace", "temp", "home", "system", "dot", "protected"]
# The reason the policy gives when a run would change a ``dot`` path.
CHANGES_DOT: str = "changes what Dot loads and runs (its plugins, skills and playbooks) or its emergency stop"

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
# The room F_GETPATH writes a path into: MAXPATHLEN on macOS.
_KERNEL_PATH_BYTES: int = 1024
# How a folder is opened to be asked its path: for its events alone, which reads nothing of it, and only when it is a
# folder and not a link. A file, a pipe or a device is never opened: a pipe nobody writes to would hold the open, and
# opening a device can wake it.
_FOLDER_FLAGS: int = os.O_EVTONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _folder_path(folder: Path) -> Path | None:
    """The path the kernel names for ``folder`` (``F_GETPATH``), or None when ``folder`` is no folder this process can
    open: not there, a file, a pipe, a device, a link, or closed to it."""
    try:
        descriptor: int = os.open(folder, _FOLDER_FLAGS)
    except OSError:
        return None
    try:
        named: bytes = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(_KERNEL_PATH_BYTES))
    finally:
        os.close(descriptor)
    return Path(os.fsdecode(named.split(b"\0", 1)[0]))


def kernel_path(path: Path) -> Path:
    """``path`` (absolute, its links resolved) as the kernel names the place it reaches (item 399): the deepest folder
    on it that this process can open is asked its path, and the rest of ``path`` follows that. A spelling through the
    Data volume (``/System/Volumes/Data/Users/…``, which the firmlinks of macOS join to ``/Users/…``) or in another
    letter case is so classified by the place it names, where ``realpath`` follows links alone."""
    folder: Path = path
    rest: list[str] = []
    named: Path | None = _folder_path(folder)
    while named is None:
        if folder.parent == folder:
            raise PolicyDenied("the kernel names no folder of the path", {"path": str(path)})
        rest.insert(0, folder.name)
        folder = folder.parent
        named = _folder_path(folder)
    return named.joinpath(*rest)


def expand_path(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` (``~`` and ``$VAR`` aware) against ``cwd`` without requiring it to exist, to the path the kernel
    names (:func:`kernel_path`)."""
    expanded: str = os.path.expandvars(os.path.expanduser(raw))
    candidate: Path = Path(expanded)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return kernel_path(Path(os.path.realpath(candidate)))


def expand_protected(raw_paths: list[str], extra: tuple[Path, ...]) -> tuple[Path, ...]:
    """The protected locations ``raw_paths`` and the paths ``extra``, resolved as :func:`expand_path` resolves what a
    tool names, so that both sides of a comparison name places the same way."""
    expanded: list[Path] = [kernel_path(Path(os.path.realpath(Path(raw).expanduser()))) for raw in raw_paths]
    expanded.extend(kernel_path(Path(os.path.realpath(path))) for path in extra)
    return tuple(expanded)


def within(path: Path, prefix: Path) -> bool:
    """Whether ``path`` is ``prefix`` or inside it, letter case aside: a Mac's volume ignores case by default, so
    ``~/.SSH/id_rsa`` is the file ``~/.ssh/id_rsa`` (item 116)."""
    folded: str = str(path).casefold()
    root: str = str(prefix).casefold().rstrip("/")
    return folded == root or folded.startswith(f"{root}/") or root == ""


def _is_temp(path: Path) -> bool:
    roots: list[Path] = [Path(os.path.realpath(prefix)) for prefix in _TEMP_PREFIXES]
    roots.append(Path(os.path.realpath(tempfile.gettempdir())))
    return any(within(path, root) for root in roots)


def _is_system(path: Path) -> bool:
    return any(within(path, Path(os.path.realpath(prefix))) for prefix in _SYSTEM_PREFIXES)


def classify_path(path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> PathClass:
    """Classify an already resolved absolute path."""
    if any(within(path, location) for location in protected):
        return "protected"
    if any(within(path, location) for location in dot_paths):
        return "dot"
    if within(path, kernel_path(Path(os.path.realpath(workspace)))):
        return "workspace"
    if _is_temp(path):
        return "temp"
    if _is_system(path):
        return "system"
    return "home"


def mentions_protected(text: str, protected: tuple[Path, ...]) -> bool:
    """True if free text (a shell command, a script) names a protected location in any common spelling, letter case
    aside."""
    home: str = str(Path.home())
    folded: str = text.casefold()
    for location in protected:
        absolute: str = str(location)
        spellings: list[str] = [absolute]
        if absolute.startswith(home):
            relative: str = absolute[len(home) :]
            spellings.extend((f"~{relative}", f"$HOME{relative}", f"${{HOME}}{relative}"))
        if any(spelling.casefold() in folded for spelling in spellings):
            return True
    return False


def _part_matches(glob: str, name: str) -> bool:
    """Whether one part of a glob matches one name as the shell matches it: letter case aside, and a name with a
    leading dot only by a part that starts with a dot (zsh and bash leave hidden files out of ``*``)."""
    if name.startswith(".") and not glob.startswith("."):
        return False
    return fnmatch.fnmatchcase(name.casefold(), glob.casefold())


def glob_reaches_protected(pattern: Path, protected: tuple[Path, ...]) -> bool:
    """Whether the glob ``pattern`` (an absolute path with ``*``, ``?`` or ``[``) can name a protected location or a
    file inside one: its parts match the location's, part by part, as far as the location goes. ``~/.s?h/id_rsa`` and
    ``~/.s*`` reach ``~/.ssh``; ``~/*`` does not, since ``*`` leaves the hidden ``.ssh`` out."""
    for location in protected:
        if len(pattern.parts) >= len(location.parts) and all(
            _part_matches(glob, name) for glob, name in zip(pattern.parts, location.parts, strict=False)
        ):
            return True
    return False
