"""The pictures of an answer (item 617): which files an answer names as pictures, which of them may leave the Mac with
it, and the answer's text without the paths of those that go.

An answer names a picture as a Markdown image (``![words](path)``) or as a path to an image file standing in its
words: written whole (it may hold spaces, ``Application Support``), with ``~/``, as a ``file://`` address, or relative
to the workspace. What is named is looked at on disk, and a picture goes only when every one of these holds:

- it is a regular file in Dot's workspace or the owner's home folder, never in a hidden folder (``~/.ssh``,
  ``~/.config``), nor in ``~/Library`` outside Dot's own folder, nor in a place the policy keeps from a run
  (``[policy] protected_paths`` and ``sealed_paths``); links are followed first, so that a link in the workspace to a
  file elsewhere is judged by the place it leads to;
- its first bytes say it is a PNG, a JPEG, a GIF, a HEIC or a WebP, whatever its name says;
- it is not over 20 MB, and at most four go with one message.

What an answer names that is not sent (it is a file, and a limit above holds) is said in one line, :meth:`Found.note`.
Nothing here reads a file beyond its first bytes except :meth:`Pictures.copy_to`, which a channel that needs a copy of
the file calls: it opens the file once, judges the file it opened (not the name it was found by, which may lead
elsewhere by then) and copies from that."""

import fcntl
import os
import re
import stat
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from dotagent.channels.text import LineKind, line_kinds
from dotagent.core.errors import ChannelError, PolicyDenied
from dotagent.policy.paths import kernel_path, within

type Kind = Literal["png", "jpeg", "gif", "heic", "webp"]

# The most pictures that go with one message, and the biggest of them.
MAX_PICTURES: int = 4
MAX_BYTES: int = 20 * 1024 * 1024
# Why a file an answer names is not sent: each reads after the file's name in the line that says what was left out.
TOO_MANY: str = f"{MAX_PICTURES} at most in a message"
TOO_BIG: str = f"over {MAX_BYTES // (1024 * 1024)} MB"
NOT_A_PICTURE: str = "not a PNG, JPEG, GIF, HEIC or WebP"
OUT_OF_PLACE: str = "in a place Dot does not send pictures from"
UNREADABLE: str = "cannot be read"
# How many bytes tell the type of a picture, how many places an answer may make Dot look at on disk, and the longest
# path and line that are looked at (a blob of data is no sentence that names a picture).
_HEAD_BYTES: int = 32
_MAX_LOOKS: int = 80
_MAX_PATH_CHARS: int = 1024
_MAX_LINE_CHARS: int = 4000
_NAME_CHARS: int = 40
_LEFT_LISTED: int = 3
_KERNEL_PATH_BYTES: int = 1024
_COPY_CHUNK: int = 1 << 20
_FILE_MODE: int = 0o600
_EXTENSIONS: str = "png|jpe?g|gif|heic|heif|webp"
# A Markdown image: its words, then its address, which may be written in angle brackets (and then may hold anything)
# or bare with spaces in it (a model writes one so), and the title an address may be followed by.
_IMAGE: re.Pattern[str] = re.compile(
    r"!\[[^\]\n]*\]\(\s*(?:<(?P<angled>[^<>\n]+)>|(?P<plain>[^()\n]+?))\s*(?:\"[^\"\n]*\")?\s*\)"
)
# The end of a name that stands in an image's extension (not followed by more of a name: ``.pngx``, ``.png.bak``) and
# the places a path may start: a slash or ``~/``, or a ``file://`` address, not in the middle of a word or an address.
_END: re.Pattern[str] = re.compile(rf"\.(?:{_EXTENSIONS})(?!\w|[./-]\w)", re.IGNORECASE)
_START: re.Pattern[str] = re.compile(r"(?<![\w/.~:-])(?:file://)?(?:~/|/)")
# A name that is relative to the workspace: no spaces, and the name of an image at its end.
_RELATIVE: re.Pattern[str] = re.compile(
    rf"(?<![\w/.~:-])(?:[\w.-]+/)*[\w.-]+?\.(?:{_EXTENSIONS})(?!\w|[./-]\w)", re.IGNORECASE
)
_QUOTES: str = "`'\"<>"
_HEIC_BRANDS: frozenset[bytes] = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
)
# The extension a copy of each kind wears: the type says what it is, and a name that says otherwise would be believed
# by the app that shows it.
EXTENSIONS: dict[Kind, str] = {"png": "png", "jpeg": "jpg", "gif": "gif", "heic": "heic", "webp": "webp"}


@dataclass(frozen=True)
class Picture:
    """A file an answer names that may go with it: ``path`` is the file with its links followed, ``name`` the name the
    answer wrote for it (never the name of what a link leads to), ``kind`` what its first bytes say it is and ``size``
    its size in bytes, as they were when it was looked at."""

    path: Path
    name: str
    kind: Kind
    size: int


@dataclass(frozen=True)
class Left:
    """A file an answer names that is not sent: its ``name`` and ``why`` (one of the reasons above), and ``path``, which
    file it is (two files may have one name); it takes no part in comparing two."""

    name: str
    why: str
    path: Path | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Found:
    """What an answer names as pictures: ``text``, the answer without the paths of the pictures that go (a Markdown
    image goes whole, a path is shortened to the file's name), the ``pictures`` that go, in the order the answer names
    them, and what is ``left_out`` and why."""

    text: str
    pictures: tuple[Picture, ...]
    left_out: tuple[Left, ...]

    def note(self) -> str:
        """The one line that says what was left out (``Left out: big.png (over 20 MB), 2 more (4 at most in a
        message).``), or an empty string when nothing was."""
        if not self.left_out:
            return ""
        many: int = sum(1 for left in self.left_out if left.why == TOO_MANY)
        named: list[Left] = [left for left in self.left_out if left.why != TOO_MANY]
        parts: list[str] = [f"{_shown(left.name)} ({left.why})" for left in named[:_LEFT_LISTED]]
        if len(named) > _LEFT_LISTED:
            parts.append(f"{len(named) - _LEFT_LISTED} more")
        if many:
            parts.append(f"{many} more ({TOO_MANY})")
        return f"Left out: {', '.join(parts)}."


@dataclass(frozen=True)
class _Mention:
    """A place in the text that names a file: where it stands, and whether it is a Markdown image."""

    start: int
    end: int
    image: bool


def _shown(name: str) -> str:
    return name if len(name) <= _NAME_CHARS else f"{name[: _NAME_CHARS - 1]}…"


def _printable(name: str) -> str:
    """``name`` for a message: what a file's name holds that is not printable (a line break, a control mark) is a space,
    so that no name makes a line of its own in the text or the note."""
    return "".join(char if char.isprintable() else " " for char in name).strip() or "picture"


def sniff(head: bytes) -> Kind | None:
    """The kind of picture whose first bytes are ``head``, or ``None``."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "heic"
    return None


def _prose(text: str) -> Iterator[tuple[int, str]]:
    """The lines of ``text`` that are prose (a block of code names no picture), each with its offset in ``text``."""
    lines: list[str] = text.split("\n")
    kinds: list[LineKind] = line_kinds(lines)
    offset: int = 0
    for line, kind in zip(lines, kinds, strict=True):
        if kind == "prose" and len(line) <= _MAX_LINE_CHARS:
            yield offset, line
        offset += len(line) + 1


def _kernel(path: Path) -> Path | None:
    """``path`` as the kernel names the place it reaches (the folders on it in the letter case they are stored in), or
    ``None`` when it names none."""
    try:
        return kernel_path(path)
    except (OSError, PolicyDenied):
        return None


def _opened_path(descriptor: int) -> Path | None:
    """The path the kernel names for the file open as ``descriptor``, or ``None`` when it names none."""
    try:
        named: bytes = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(_KERNEL_PATH_BYTES))
    except OSError:
        return None
    return Path(os.fsdecode(named.split(b"\0", 1)[0]))


class Pictures:
    """Which files an answer names as pictures and which of them go (see the module). ``workspace`` is Dot's workspace,
    ``home`` the owner's home folder, ``refused`` the places the policy keeps from a run."""

    def __init__(self, workspace: Path, home: Path, refused: Sequence[Path] = ()) -> None:
        self._workspace: Path = _kernel(Path(os.path.realpath(workspace))) or workspace
        self._home: Path = _kernel(Path(os.path.realpath(home))) or home
        self._refused: tuple[Path, ...] = tuple(refused)

    # ----- the answer ------------------------------------------------------------------------

    def find(self, markdown: str) -> Found:
        """The pictures ``markdown`` names, in the order it names them, and what it names that is not sent. The files
        are looked at on disk (a few places at most), so this is called off the event loop."""
        taken: dict[Path, Picture] = {}
        left: dict[tuple[Path | str, str], Left] = {}
        cuts: list[tuple[int, int, str]] = []
        for mention, outcome in self._named_in(markdown):
            if isinstance(outcome, Left):
                left.setdefault((outcome.path or outcome.name, outcome.why), outcome)
                continue
            if outcome.path not in taken:
                if len(taken) >= MAX_PICTURES:
                    left.setdefault((outcome.path, TOO_MANY), Left(outcome.name, TOO_MANY, outcome.path))
                    continue
                taken[outcome.path] = outcome
            cuts.append((mention.start, mention.end, "" if mention.image else outcome.name))
        return Found(_without(markdown, cuts), tuple(taken.values()), tuple(left.values()))

    def _named_in(self, markdown: str) -> Iterator[tuple[_Mention, Picture | Left]]:
        """What the prose of ``markdown`` names that is a file, in order, with what it comes to: on each line the images
        first, then the paths in the rest of it."""
        budget: list[int] = [_MAX_LOOKS]
        for offset, line in _prose(markdown):
            covered: list[tuple[int, int]] = []
            for image in _IMAGE.finditer(line):
                covered.append(image.span())
                outcome = self._look(image.group("angled") or image.group("plain") or "", budget)
                if outcome is not None:
                    yield _Mention(offset + image.start(), offset + image.end(), True), outcome
            for start, end, outcome in self._paths_in(line, covered, budget):
                yield _Mention(offset + start, offset + end, False), outcome

    def _paths_in(
        self, line: str, covered: list[tuple[int, int]], budget: list[int]
    ) -> Iterator[tuple[int, int, Picture | Left]]:
        """The paths in ``line`` outside ``covered`` that name a file. A path in words may hold spaces, so it is tried
        from each place a path may start up to the end of each image name, the longest first; the first that is a file
        is the one the words name. Then the names with no folder, which are in the workspace."""

        def inside(position: int) -> bool:
            return any(begin <= position < finish for begin, finish in covered)

        starts: list[int] = [found.start() for found in _START.finditer(line) if not inside(found.start())]
        done: int = 0
        for ending in _END.finditer(line):
            end: int = ending.end()
            if end <= done or inside(end - 1):
                continue
            for start in starts:
                if start < done or end - start > _MAX_PATH_CHARS:
                    continue
                if start >= end:
                    break
                outcome = self._look(line[start:end], budget)
                if outcome is not None:
                    covered.append((start, end))
                    done = end
                    yield start, end, outcome
                    break
        for found in _RELATIVE.finditer(line):
            if inside(found.start()):
                continue
            outcome = self._look(found.group(), budget)
            if outcome is not None:
                yield found.start(), found.end(), outcome

    # ----- one file --------------------------------------------------------------------------

    def _look(self, raw: str, budget: list[int]) -> Picture | Left | None:
        """What the path ``raw`` names: a picture that may go, a file that is left out and why, or ``None`` when it
        names no file (or when the answer has made Dot look at as many places as it looks at)."""
        for path in self._spelled(raw):
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            found: tuple[int, Picture] | Left | None = self._open(path)
            if isinstance(found, tuple):
                os.close(found[0])
                return found[1]
            if found is not None:
                return found
        return None

    def _spelled(self, raw: str) -> list[Path]:
        """The files ``raw`` may name, as the answer wrote it: ``~/`` is the owner's home, a name with no folder in
        front of it is in the workspace, a ``file://`` address is the path in it, and an address with percent-encoded
        marks (a Markdown image's) is tried decoded too."""
        text: str = raw.strip().strip(_QUOTES).strip()
        if text.startswith("file://"):
            text = urlsplit(text).path
        elif "://" in text:
            return []
        if not text or len(text) > _MAX_PATH_CHARS or "\0" in text:
            return []
        spellings: list[str] = [text]
        if "%" in text and "\0" not in unquote(text) and unquote(text) != text:
            spellings.append(unquote(text))
        return [self._expanded(spelling) for spelling in spellings]

    def _expanded(self, text: str) -> Path:
        if text == "~" or text.startswith("~/"):
            return self._home / text[2:]
        if text.startswith("/"):
            return Path(text)
        return self._workspace / text

    def _open(self, path: Path) -> tuple[int, Picture] | Left | None:
        """Open ``path`` for reading and judge the file that was opened: ``(descriptor, picture)`` when it may go (the
        caller closes the descriptor), a :class:`Left` when it is a file and may not, ``None`` when it is no regular
        file or is not there. The place is judged twice, by the name and by the path the kernel names for the file
        opened, so that a link changed between the two cannot lead out of the places."""
        try:
            real: Path = Path(os.path.realpath(path))
            if not stat.S_ISREG(os.stat(real).st_mode):
                return None
        except (OSError, ValueError):
            return None
        named: Path | None = _kernel(real)
        if named is None:
            return None
        # The file is called by the name the answer wrote for it, never by the name of what a link leads to: the line of
        # what was left out goes to the owner's phone, and a link in the workspace to ``~/.ssh/<name>.png`` would tell
        # the name of a file the answer never named.
        shown: str = _printable(path.name)
        if not self._may_send_from(named):
            return Left(shown, OUT_OF_PLACE, real)
        try:
            descriptor: int = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            return Left(shown, UNREADABLE, real)
        try:
            judged: tuple[int, Picture] | Left | None = self._judged(descriptor, shown)
        except OSError:
            os.close(descriptor)
            return Left(shown, UNREADABLE, real)
        except BaseException:
            os.close(descriptor)
            raise
        if not isinstance(judged, tuple):
            os.close(descriptor)
        return judged

    def _judged(self, descriptor: int, name: str) -> tuple[int, Picture] | Left | None:
        """The file open as ``descriptor`` judged: where it is, how big, and what its first bytes say. The descriptor
        stays open (and at the start of the file) only for a picture that may go."""
        info: os.stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        opened: Path | None = _opened_path(descriptor)
        if opened is None or not self._may_send_from(_kernel(opened) or opened):
            return Left(name, OUT_OF_PLACE)
        if info.st_size > MAX_BYTES:
            return Left(name, TOO_BIG)
        kind: Kind | None = sniff(os.read(descriptor, _HEAD_BYTES))
        if kind is None:
            return Left(name, NOT_A_PICTURE)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, Picture(path=opened, name=name, kind=kind, size=info.st_size)

    def _may_send_from(self, path: Path) -> bool:
        """Whether a picture may be sent from ``path`` (the kernel's name for it, links followed): in Dot's workspace or
        the owner's home folder, in no hidden folder, in ``~/Library`` only where it is Dot's workspace, and in no place
        the policy keeps from a run."""
        if any(within(path, refused) for refused in self._refused):
            return False
        for root in (self._workspace, self._home):
            if not within(path, root):
                continue
            rest: tuple[str, ...] = path.parts[len(root.parts) :]
            if any(part.startswith(".") for part in rest):
                return False
            return root == self._workspace or not rest or rest[0].casefold() != "library"
        return False

    def copy_to(self, picture: Picture, target: Path) -> None:
        """Copy the file ``picture`` names to ``target`` (a new file, private to the owner), judging the file as it is
        now: it must still be a picture of the kind it was, in a place a picture goes from, and within the limit. The
        file is read once, from the descriptor it was opened as. Raises :class:`ChannelError` when it is not, or cannot
        be read or written."""
        found: tuple[int, Picture] | Left | None = self._open(picture.path)
        if not isinstance(found, tuple):
            why: str = "it is gone" if found is None else found.why
            raise ChannelError(f"{picture.name} cannot be sent now: {why}", {"path": str(picture.path)})
        source, now = found
        try:
            if now.kind != picture.kind:
                raise ChannelError(f"{picture.name} changed after it was looked at", {"path": str(picture.path)})
            _copy(source, target, picture)
        finally:
            os.close(source)


def _copy(source: int, target: Path, picture: Picture) -> None:
    """Write what is left of the file open as ``source`` to the new file ``target``, at most :data:`MAX_BYTES`."""
    try:
        descriptor: int = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE)
    except OSError as error:
        raise ChannelError(f"a copy of {picture.name} could not be made", {"target": str(target)}) from error
    copied: int = 0
    try:
        with os.fdopen(descriptor, "wb") as out:
            while chunk := os.read(source, _COPY_CHUNK):
                copied += len(chunk)
                if copied > MAX_BYTES:
                    raise ChannelError(f"{picture.name} grew past {TOO_BIG}", {"path": str(picture.path)})
                out.write(chunk)
    except OSError as error:
        raise ChannelError(f"{picture.name} could not be copied", {"path": str(picture.path)}) from error


def _without(text: str, cuts: Sequence[tuple[int, int, str]]) -> str:
    """``text`` with each cut ``(start, end, words)`` replaced by its words. A Markdown image that is all its line holds
    takes the line with it, and one in a sentence takes a space (so that two are not run together)."""
    pieces: list[str] = []
    cursor: int = 0
    for start, end, words in sorted(cuts):
        if start < cursor:
            continue
        before: str = text[cursor:start]
        after: int = end
        if words == "":
            line_start: int = text.rfind("\n", 0, start) + 1
            line_end: int = text.find("\n", end)
            line_end = len(text) if line_end == -1 else line_end
            if cursor <= line_start and text[line_start:start].strip() == "" and text[end:line_end].strip() == "":
                before, after = text[cursor:line_start], min(line_end + 1, len(text))
            elif before.endswith((" ", "\t")) and text[end : end + 1] in ("", " ", "\t", "\n"):
                before = before[:-1]
        pieces.extend((before, words))
        cursor = after
    pieces.append(text[cursor:])
    return "".join(pieces)
