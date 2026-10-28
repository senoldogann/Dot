"""A folder of Dot's own that Messages can read, for the pictures it sends (item 617).

Messages takes a file to send from an AppleScript, but on a recent macOS it does not read every folder the script
names: a file in a protected folder such as ``~/Documents`` fails after the text of the message has gone, and
``~/Pictures`` is the folder reported to work. Dot's workspace is not known to be one Messages may read, so a picture
is never sent from where it is: it is copied into a folder of its own under ``~/Pictures`` (``~/Pictures/Dot``, see
:func:`~dotagent.core.paths.staging_dir`), a new folder for each, with the file's name and the extension its type gives
it, and Messages is handed the copy.

The copy is removed afterwards, but not at once: ``osascript`` returns when Messages has taken the message, and what
Messages does with the file after that is not known (whether it copies it into its own attachments or reads it again
while it uploads and shows it), so a copy stays :data:`STAGED_KEPT_S` and is removed then. A copy of a send that failed
is removed at once. Those left by a daemon that stopped are removed as the next one starts.

The staging folder is the owner's, not Dot's, and Dot removes only what it made: a folder is one of Dot's when it is a
real folder directly under the staging folder, its name is the time it was made and eight hexadecimal digits, and it
holds :data:`MARKER`, a file this module writes into it as it makes it. A folder without the mark is somebody else's
whatever its name says, and is left alone; so is a marked folder that holds a folder (only the files in a folder of
Dot's are removed, with the folder, never a tree)."""

import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from dotagent.channels.pictures import EXTENSIONS, Picture, Pictures
from dotagent.core.errors import ChannelError
from dotagent.core.logs import get_logger

log = get_logger(__name__)

# How long a copy stays after the picture was handed to Messages: long enough for a slow upload and a retry.
STAGED_KEPT_S: float = 3600.0
# The file this module writes into every folder it makes; it removes no folder that does not hold it.
MARKER: str = ".made-by-dot"
_MARK_START: str = "Made by Dot"
_MARK_WORDS: str = f"{_MARK_START}: the copy of a picture it sends over iMessage. Dot removes this folder.\n"
# The name of a folder this module made: when it was made (seconds since the epoch), then eight hexadecimal digits.
_FOLDER: re.Pattern[str] = re.compile(r"^(?P<made>\d{1,12})-[0-9a-f]{8}$")
_UNSAFE: re.Pattern[str] = re.compile(r"[^\w.\- ]+")
_STEM_CHARS: int = 60
_FOLDER_MODE: int = 0o700
_FILE_MODE: int = 0o600
_MARK_BYTES: int = 64


@dataclass(frozen=True)
class Staged:
    """A copy of a picture, ready to be sent: the ``folder`` it was made in (removed with it) and its ``path``."""

    folder: Path
    path: Path


@dataclass(frozen=True)
class _Waiting:
    folder: Path
    remove_at: float


def staged_name(picture: Picture) -> str:
    """The name a copy of ``picture`` wears: the stem of its name made safe (letters, digits, dots, dashes, spaces and
    underscores, not a hidden name, at most 60 characters) and the extension its type gives it."""
    stem: str = _UNSAFE.sub("_", Path(picture.name).stem).strip(". ")[:_STEM_CHARS].strip(". ")
    return f"{stem or 'picture'}.{EXTENSIONS[picture.kind]}"


def _mark(folder: Path) -> None:
    """Write the mark into the new folder ``folder``."""
    descriptor: int = os.open(
        folder / MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(_MARK_WORDS)


def _marked(folder: Path) -> bool:
    """Whether ``folder`` holds the mark of a folder of Dot's: a regular file of that name (never a link) that starts
    with the words this module writes."""
    try:
        descriptor: int = os.open(folder / MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False
        return os.read(descriptor, _MARK_BYTES).startswith(_MARK_START.encode("utf-8"))
    except OSError:
        return False
    finally:
        os.close(descriptor)


def _unmade(folder: Path) -> None:
    """Take back the folder ``folder`` made a moment ago, whose mark could not be written: it holds nothing but what
    the mark left of itself."""
    try:
        (folder / MARKER).unlink(missing_ok=True)
        folder.rmdir()
    except OSError as error:
        log.warning("staged_picture_not_removed", folder=str(folder), error=str(error))


def _a_real_folder(path: Path) -> bool:
    """Whether ``path`` is a folder and not a link to one."""
    return path.is_dir() and not path.is_symlink()


class Staging:
    """The staging folder ``root`` and the copies made in it. ``pictures`` judges the file as it is copied, ``kept_s``
    is how long the copy of a picture that was sent stays."""

    def __init__(self, root: Path, pictures: Pictures, kept_s: float = STAGED_KEPT_S) -> None:
        self._root: Path = root
        self._pictures: Pictures = pictures
        self._kept_s: float = kept_s
        self._waiting: list[_Waiting] = []
        self._waiting_lock: threading.Lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def stage(self, picture: Picture, now: float) -> Staged:
        """Copy ``picture`` into a folder of its own made at ``now`` under the staging folder, marked as Dot's. Raises
        :class:`ChannelError` when the folder cannot be made or the file is no longer one that may go."""
        folder: Path = self._root / f"{int(now)}-{secrets.token_hex(4)}"
        try:
            self._root.mkdir(mode=_FOLDER_MODE, parents=True, exist_ok=True)
            folder.mkdir(mode=_FOLDER_MODE)
        except OSError as error:
            raise ChannelError(
                f"Dot could not make its folder for pictures at {self._root}", {"error": str(error)}
            ) from error
        try:
            _mark(folder)
        except OSError as error:
            _unmade(folder)
            raise ChannelError(
                f"Dot could not make its folder for pictures at {self._root}", {"error": str(error)}
            ) from error
        target: Path = folder / staged_name(picture)
        try:
            self._pictures.copy_to(picture, target)
        except BaseException:
            self._remove(folder)
            raise
        return Staged(folder, target)

    def sent(self, staged: Staged, now: float) -> None:
        """The copy was handed to Messages at ``now``: it stays :attr:`kept_s` and is removed by a sweep then."""
        with self._waiting_lock:
            self._waiting.append(_Waiting(staged.folder, now + self._kept_s))

    def drop(self, staged: Staged) -> None:
        """Remove the copy at once (nothing was sent)."""
        self._remove(staged.folder)

    def due(self, now: float) -> bool:
        """Whether a copy that was sent is due to be removed at ``now``."""
        return any(waiting.remove_at <= now for waiting in self._waiting)

    def sweep(self, now: float) -> None:
        """Remove the copies that were sent and are due at ``now``. The sweep runs in a thread while a picture is sent
        on the loop (the review of every module): the list is taken and given back under a lock, so that a copy sent
        meanwhile is not dropped from it and left for good."""
        with self._waiting_lock:
            due: list[_Waiting] = [waiting for waiting in self._waiting if waiting.remove_at <= now]
            self._waiting = [waiting for waiting in self._waiting if waiting.remove_at > now]
        for waiting in due:
            self._remove(waiting.folder)

    def sweep_old(self, now: float) -> None:
        """Remove the folders of Dot's that are older than the time a copy stays, as the daemon starts: the copies a
        daemon that stopped left behind. Nothing else in the staging folder is touched, however it is named: a folder is
        Dot's only when it holds the mark."""
        try:
            entries: list[Path] = list(self._root.iterdir())
        except OSError:
            return
        for entry in entries:
            made: re.Match[str] | None = _FOLDER.match(entry.name)
            if made is None or int(made.group("made")) + self._kept_s > now or not _a_real_folder(entry):
                continue
            if _marked(entry):
                self._remove(entry)
            else:
                log.info("staged_picture_folder_left_alone", folder=str(entry), why="it holds no mark of Dot's")

    def _remove(self, folder: Path) -> None:
        """Remove ``folder`` with the copy in it, if it is a folder of Dot's: a real folder directly under the staging
        folder, named as those are, that holds the mark. Anything else is refused and logged, a folder with a folder in
        it too (its files are not removed either); a failure is logged."""
        if not folder.exists() and not folder.is_symlink():
            return
        if folder.parent != self._root or _FOLDER.match(folder.name) is None or not _a_real_folder(folder):
            log.warning("staged_picture_folder_refused", folder=str(folder))
            return
        if not _marked(folder):
            log.warning("staged_picture_folder_refused", folder=str(folder), why="it holds no mark of Dot's")
            return
        try:
            with os.scandir(folder) as scanned:
                entries: list[os.DirEntry[str]] = list(scanned)
            if any(entry.is_dir(follow_symlinks=False) for entry in entries):
                log.warning("staged_picture_folder_refused", folder=str(folder), why="there is a folder in it")
                return
            # The mark goes last, so that a folder left half removed is still known as Dot's.
            for entry in sorted(entries, key=lambda found: (found.name == MARKER, found.name)):
                os.unlink(entry.path)
            folder.rmdir()
        except FileNotFoundError:
            return
        except OSError as error:
            log.warning("staged_picture_not_removed", folder=str(folder), error=str(error))
