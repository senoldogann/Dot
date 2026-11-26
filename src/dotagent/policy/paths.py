"""Where a filesystem path sits relative to the workspace, the user's home, Dot's own folders and protected
locations."""

import errno
import fcntl
import fnmatch
import functools
import os
import tempfile
from pathlib import Path
from typing import Literal

from dotagent.core.errors import NulCharacterError, PolicyDenied

# ``dot`` is what Dot loads and runs, and its emergency stop (``Runtime.dot_paths``): a run reads it as it reads the
# home folder; Dot's own file tools change it once the user agrees (item 398), a command line and OpenCode's agent
# never do.
PathClass = Literal["workspace", "temp", "home", "system", "dot", "protected"]
# The reason the policy gives when a run would change a ``dot`` path.
CHANGES_DOT: str = "changes what Dot loads and runs (its code, plugins, skills and playbooks) or its emergency stop"
# The reason the policy gives when a run would write a file that git runs as code (:func:`runs_as_git_code`).
CHANGES_GIT_CODE: str = "changes a file git runs as code (a repository's config or hooks): the next git command runs it"
_GIT_CONFIGS: tuple[str, ...] = ("config", "config.worktree")
_GIT_HOOKS: str = "hooks"
_GIT_FOLDER: str = ".git"
# The folders of a git directory that only hold data (a branch may be named ``config`` or ``hooks``).
_GIT_DATA: frozenset[str] = frozenset({"refs", "objects", "logs"})

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
# The other names macOS gives the places under its root (item 399): through the Data volume, which firmlinks join to
# the system volume (``/System/Volumes/Data/Users`` is ``/Users``); behind the prefixes that ask the kernel to follow no
# link (``/.nofollow/Users``) or to resolve as its flags say (``/.resolve/1/Users``); and through the links at the root
# into ``/private`` (``/var/…`` is ``/private/var/…``). :func:`kernel_path` names a path through any of them as the
# place it reaches; a glob, which names no place yet, is matched through each (:func:`glob_reaches_protected`).
_DATA_VOLUME: tuple[str, ...] = ("System", "Volumes", "Data")
_NO_FOLLOW: str = ".nofollow"
_RESOLVE: str = ".resolve"
_ROOT_LINKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tmp", ("private", "tmp")),
    ("var", ("private", "var")),
    ("etc", ("private", "etc")),
)
# The volfs namespace: ``/.vol/<device>/<inode>`` names a file by its number, a name for every place that no list of
# places holds, so a path, a glob or a text that spells one reaches every protected location (item 399). A folder
# spelled so is named as the place it is (:func:`kernel_path`); a file cannot be.
_BY_NUMBER: str = ".vol"
# The room F_GETPATH writes a path into: MAXPATHLEN on macOS.
_KERNEL_PATH_BYTES: int = 1024


# How a folder is opened to be asked its path: for its events alone, which reads nothing of it, and only when it is a
# folder and not a link. A file, a pipe or a device is never opened: a pipe nobody writes to would hold the open, and
# opening a device can wake it. Read at the call: ``O_EVTONLY`` is macOS's, and this module is imported where Dot's
# unit tests run on Linux (item 1270).
def _folder_flags() -> int:
    return os.O_EVTONLY | os.O_DIRECTORY | os.O_NOFOLLOW


# What such an open says of a place that is no folder this process can open: not there, a file, a pipe, a socket, a
# device or a link (ENOTDIR), a loop of links, closed to it (by its mode or by macOS's privacy), a name too long, or a
# prefix that names no place by itself (``/.resolve/<flags>``, EINVAL). Any other failure (no descriptor left, an I/O
# error) is raised, not taken for one of these.
_NOT_A_FOLDER: frozenset[int] = frozenset(
    {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EACCES, errno.EPERM, errno.ENAMETOOLONG, errno.EINVAL}
)


def _folder_path(folder: Path) -> Path | None:
    """The path the kernel names for ``folder`` (``F_GETPATH``), or None when ``folder`` is no folder this process can
    open: not there, a file, a pipe, a device, a link, or closed to it (:data:`_NOT_A_FOLDER`)."""
    try:
        descriptor: int = os.open(folder, _folder_flags())
    except OSError as error:
        if error.errno in _NOT_A_FOLDER:
            return None
        raise
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
    names (:func:`kernel_path`). A path with a NUL character names no file: the call is refused for what it gave (item
    727), never a crash of the run."""
    if "\0" in raw:
        raise NulCharacterError("a path cannot hold a NUL character", {"path": raw.replace("\0", "\\0")[:200]})
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


def _named(part: str, name: str) -> bool:
    """Whether the path component ``part`` is ``name`` (letter case aside) or a glob that can match it."""
    return fnmatch.fnmatchcase(name, part.casefold())


def _is_git_directory(folder: Path) -> bool:
    """Whether ``folder`` holds ``HEAD`` and ``objects``: a bare repository, whose config and hooks git runs like a
    ``.git``'s."""
    return (folder / "HEAD").is_file() and (folder / "objects").is_dir()


def _holds_git_code(inside: tuple[str, ...]) -> bool:
    """Whether the path ``inside`` a ``.git`` folder is its config or its hooks (or under them)."""
    if not inside or any(name.casefold() in _GIT_DATA for name in inside[:-1]):
        return False
    return any(_named(name, _GIT_HOOKS) for name in inside) or any(_named(inside[-1], name) for name in _GIT_CONFIGS)


def runs_as_git_code(path: Path) -> bool:
    """Whether ``path`` is a file git runs as code, or the folder of them: the ``config`` of a git directory (it can
    name a program for ``core.fsmonitor``, ``core.sshCommand``, a filter or an alias), its ``config.worktree``, and what
    lies under its ``hooks``. A git directory is a ``.git`` folder (also the ``.git/modules/<name>`` of a submodule) or
    a folder that holds ``HEAD`` and ``objects`` (a bare repository). The data folders (``refs``, ``objects``,
    ``logs``) hold none. A component of the path may be a glob that can match the name. It judges the exact path a file
    tool is given; what git writes itself (``git config``, ``git init``) is a command and never reaches this. A shell
    line that writes there by a redirect or a copy is not judged by it (item 1301, not covered)."""
    parts: tuple[str, ...] = path.parts
    for index, part in enumerate(parts):
        if _named(part, _GIT_FOLDER) and _holds_git_code(parts[index + 1 :]):
            return True
        names_code: bool = _named(part, _GIT_HOOKS) or (
            index == len(parts) - 1 and any(_named(part, name) for name in _GIT_CONFIGS)
        )
        if index and names_code and _is_git_directory(Path(*parts[:index])):
            return True
    return False


@functools.cache
def _resolved_roots(prefixes: tuple[str, ...]) -> tuple[Path, ...]:
    """``prefixes`` with their links followed, resolved once for each set (item 4 of the review of 2026-11-26): every
    path a tool names was classified with a ``realpath`` of each, a lookup of the disk on the event loop that froze the
    daemon for 10-15 s at a time on a slow disk. They are the system's own places and links (``/tmp`` is
    ``/private/tmp``), which a run cannot change; the temporary folder of this process is one too, held from the first
    look, so a link a run later puts in its place does not move it."""
    return tuple(Path(os.path.realpath(prefix)) for prefix in prefixes)


def _is_temp(path: Path) -> bool:
    # ``gettempdir`` reads ``TMPDIR`` once for the process and keeps it; the folder it names is a key of the cache, so a
    # process that names another one (``tempfile.tempdir``) has its own roots resolved.
    return any(within(path, root) for root in _resolved_roots((*_TEMP_PREFIXES, tempfile.gettempdir())))


def _is_system(path: Path) -> bool:
    return any(within(path, root) for root in _resolved_roots(_SYSTEM_PREFIXES))


def _without_root_names(parts: tuple[str, ...]) -> tuple[str, ...]:
    """The parts of an absolute path without the prefixes that name the root again (the Data volume, ``/.nofollow``,
    ``/.resolve/<flags>``), however many lead it."""
    rest: tuple[str, ...] = parts[1:]
    data_volume: tuple[str, ...] = tuple(name.casefold() for name in _DATA_VOLUME)
    while True:
        lead: tuple[str, ...] = tuple(name.casefold() for name in rest[: len(data_volume)])
        if lead[:1] == (_NO_FOLLOW,):
            rest = rest[1:]
        elif lead[:1] == (_RESOLVE,) and len(rest) > 1:
            rest = rest[2:]
        elif lead == data_volume:
            rest = rest[len(data_volume) :]
        else:
            return (*parts[:1], *rest)


def _by_number(path: Path) -> bool:
    """Whether ``path`` names a file by its number (``/.vol/…``), behind any prefix that names the root again."""
    return tuple(name.casefold() for name in _without_root_names(path.parts)[1:2]) == (_BY_NUMBER,)


def classify_path(path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]) -> PathClass:
    """Classify an already resolved absolute path. A file named by its number (``/.vol``) is protected."""
    if _by_number(path) or any(within(path, location) for location in protected):
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


def _spelled_from(location: Path, lead: tuple[str, ...], spelled: str) -> str | None:
    """``location`` with its leading parts ``lead`` (compared letter case aside) written as ``spelled``, or None when it
    does not begin with them."""
    if tuple(name.casefold() for name in location.parts[: len(lead)]) != tuple(name.casefold() for name in lead):
        return None
    return spelled + "".join(f"/{name}" for name in location.parts[len(lead) :])


def mentions_protected(text: str, protected: tuple[Path, ...]) -> bool:
    """True if free text (a shell command, a script) names a protected location in any common spelling, letter case
    aside (from the home folder, or through a link at the root: ``/var/…`` for ``/private/var/…``), or names a file by
    its number (``/.vol/``), which reaches any of them."""
    folded: str = text.casefold()
    if protected and f"/{_BY_NUMBER}/" in folded:
        return True
    home: tuple[str, ...] = Path.home().parts
    for location in protected:
        spellings: list[str] = [str(location)]
        for lead, spelled in (
            *((("/", *target), f"/{name}") for name, target in _ROOT_LINKS),
            *((home, start) for start in ("~", "$HOME", "${HOME}")),
        ):
            spelling: str | None = _spelled_from(location, lead, spelled)
            if spelling is not None:
                spellings.append(spelling)
        if any(spelling.casefold() in folded for spelling in spellings):
            return True
    return False


def _part_matches(glob: str, name: str) -> bool:
    """Whether one part of a glob matches one name as the shell matches it: letter case aside, and a name with a
    leading dot only by a part that starts with a dot (zsh and bash leave hidden files out of ``*``)."""
    if name.startswith(".") and not glob.startswith("."):
        return False
    return fnmatch.fnmatchcase(name.casefold(), glob.casefold())


def _parts_match(globs: tuple[str, ...], names: tuple[str, ...]) -> bool:
    """Whether ``globs`` match ``names`` part by part, as far as ``names`` go."""
    return len(globs) >= len(names) and all(_part_matches(glob, name) for glob, name in zip(globs, names, strict=False))


def _glob_reaches(parts: tuple[str, ...], location: tuple[str, ...]) -> bool:
    """Whether the absolute glob ``parts`` can name the place ``location`` or one inside it: as written, or after a
    prefix that names the root again, or through a link at the root, as the shell would find them. One that can name a
    file by its number reaches every place."""
    if _parts_match(parts, location):
        return True
    rest: tuple[str, ...] = parts[1:]
    if not rest:
        return False
    if _part_matches(rest[0], _BY_NUMBER):
        return True
    beyond: list[tuple[str, ...]] = [
        (*target, *rest[1:]) for name, target in _ROOT_LINKS if _part_matches(rest[0], name)
    ]
    if _part_matches(rest[0], _NO_FOLLOW):
        beyond.append(rest[1:])
    if _part_matches(rest[0], _RESOLVE) and len(rest) > 1:
        beyond.append(rest[2:])
    if len(rest) > len(_DATA_VOLUME) and _parts_match(rest, _DATA_VOLUME):
        beyond.append(rest[len(_DATA_VOLUME) :])
    return any(_glob_reaches((*parts[:1], *after), location) for after in beyond)


def glob_reaches_protected(pattern: Path, protected: tuple[Path, ...]) -> bool:
    """Whether the glob ``pattern`` (an absolute path with ``*``, ``?`` or ``[``) can name a protected location or a
    file inside one: its parts match the location's, part by part, as far as the location goes, through any other name
    of the root too (item 399). ``~/.s?h/id_rsa``, ``~/.s*``, ``/S*/V*/D*/U*/*/.ssh`` and ``/.resolve/1/U*/*/.ssh``
    reach ``~/.ssh``; ``~/*`` does not, since ``*`` leaves the hidden ``.ssh`` out. A glob that can name a file by its
    number (``/.v*/…``) reaches every location."""
    return any(_glob_reaches(pattern.parts, location.parts) for location in protected)
