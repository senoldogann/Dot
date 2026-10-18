"""What Settings › System › Diagnostics shows (item 370), read when the page asks: the daemon's process and those it
started, with what each takes of the CPU and of memory; the room Dot's own folders take on disk; and the logs, the last
lines of one. A process is named by its executable alone, as its arguments may carry a key, and a log's lines go out
with every secret Dot holds masked, word for word, and the forms of the others: a log may be the raw output of an MCP
server or of launchd. The owner's own Chrome is never measured: only the folders Dot keeps."""

import asyncio
import os
import re
from collections.abc import Sequence
from pathlib import Path

from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.paths import (
    browser_incognito_dir,
    browser_profile_dir,
    browser_profiles_dir,
    db_path,
    dot_home,
    downloads_dir,
    evals_dir,
    log_dir,
    playbooks_dir,
    plugins_dir,
    screenshots_dir,
    skills_dir,
    themes_dir,
    undo_dir,
    workspace_dir,
)
from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel
from dotagent.secrets import known_secrets

# The columns `ps` is asked for, without a header: the executable's path comes last, as it may hold spaces.
_PS_COLUMNS: str = "pid=,ppid=,rss=,%cpu=,etime=,comm="
# `ps`'s elapsed time: [[dd-]hh:]mm:ss.
_ELAPSED: re.Pattern[str] = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")
# How much of a log's end is read for its last lines: a few hundred lines of the structured log.
_TAIL_BYTES: int = 262_144
# The most lines a page may ask of a log.
MAX_TAIL_LINES: int = 1000
# Where a private key in PEM starts and ends (core.text masks one from its start).
_KEY_BEGIN: re.Pattern[str] = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END: re.Pattern[str] = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


class DiagnosticsError(DotError):
    """What the diagnostics read could not be read: `ps` failed, or wrote what it does not write."""


class PsRow(FrozenModel):
    """One line of `ps`: a process, its parent, its resident memory in KiB, its share of one CPU, how long it has run
    and the path of its executable (empty for a process that has exited and waits for its parent)."""

    pid: int
    parent: int
    rss_kib: int
    cpu_percent: float
    running_s: int
    executable: str


class ProcessRow(FrozenModel):
    """A process of the daemon's tree: how deep under the daemon it is (0 for the daemon itself) and the name of its
    executable."""

    pid: int
    parent: int
    depth: int
    name: str
    cpu_percent: float
    memory_bytes: int
    running_s: int


class ProcessesView(FrozenModel):
    """The daemon's tree of processes, with what they take together."""

    daemon_pid: int
    processes: tuple[ProcessRow, ...]
    cpu_percent: float
    memory_bytes: int


class FolderUse(FrozenModel):
    """The room one kind of what Dot keeps takes on disk, and the folders or files it is in."""

    name: str
    paths: tuple[str, ...]
    bytes: int


class DiskView(FrozenModel):
    folders: tuple[FolderUse, ...]


class LogFile(FrozenModel):
    name: str
    bytes: int
    modified_at: float


class LogsView(FrozenModel):
    """The logs in Dot's log folder, the newest first."""

    folder: str
    files: tuple[LogFile, ...]


class LogTail(FrozenModel):
    """The last lines of a log, their secrets masked."""

    name: str
    lines: tuple[str, ...]


def elapsed_seconds(etime: str) -> int:
    """`ps`'s elapsed time, ``[[dd-]hh:]mm:ss``, in seconds."""
    match: re.Match[str] | None = _ELAPSED.match(etime.strip())
    if match is None:
        raise DiagnosticsError("ps wrote an elapsed time it does not write", {"etime": etime})
    days, hours, minutes, seconds = (0 if part is None else int(part) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def parse_ps(output: str) -> tuple[PsRow, ...]:
    """The rows of ``ps -A -o pid=,ppid=,rss=,%cpu=,etime=,comm=``, run in the C locale."""
    rows: list[PsRow] = []
    for line in output.splitlines():
        fields: list[str] = line.split(None, 5)
        if not fields:
            continue
        if len(fields) < 5:
            raise DiagnosticsError("ps wrote a line it does not write", {"line": line})
        rows.append(
            PsRow(
                pid=int(fields[0]),
                parent=int(fields[1]),
                rss_kib=int(fields[2]),
                cpu_percent=float(fields[3]),
                running_s=elapsed_seconds(fields[4]),
                executable=fields[5].strip() if len(fields) == 6 else "",
            )
        )
    return tuple(rows)


def process_tree(rows: Sequence[PsRow], root: int) -> tuple[ProcessRow, ...]:
    """The process ``root`` and every process under it, each right after its parent, the eldest first among the
    children of one parent."""
    by_pid: dict[int, PsRow] = {row.pid: row for row in rows}
    if root not in by_pid:
        raise DiagnosticsError("ps did not list the daemon's own process", {"pid": root})
    children: dict[int, list[PsRow]] = {}
    for row in rows:
        if row.pid != row.parent:
            children.setdefault(row.parent, []).append(row)

    def branch(row: PsRow, depth: int) -> list[ProcessRow]:
        own: ProcessRow = ProcessRow(
            pid=row.pid,
            parent=row.parent,
            depth=depth,
            name=Path(row.executable).name,
            cpu_percent=row.cpu_percent,
            memory_bytes=row.rss_kib * 1024,
            running_s=row.running_s,
        )
        below: list[ProcessRow] = [
            item
            for child in sorted(children.get(row.pid, []), key=lambda child: (-child.running_s, child.pid))
            for item in branch(child, depth + 1)
        ]
        return [own, *below]

    return tuple(branch(by_pid[root], 0))


async def read_processes(root: int) -> ProcessesView:
    """The tree of the process ``root`` (the daemon's), as ``ps`` lists it now."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "ps",
        "-A",
        "-o",
        _PS_COLUMNS,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
    )
    out, err = await process.communicate()
    if process.returncode != 0:
        raise DiagnosticsError(
            "ps could not list the processes",
            {"status": process.returncode, "stderr": err.decode("utf-8", errors="replace")[:300]},
        )
    # The `ps` that lists them is the daemon's child too while it runs: it is left out.
    listed: tuple[PsRow, ...] = parse_ps(out.decode("utf-8", errors="replace"))
    tree: tuple[ProcessRow, ...] = process_tree([row for row in listed if row.pid != process.pid], root)
    return ProcessesView(
        daemon_pid=root,
        processes=tree,
        cpu_percent=round(sum(row.cpu_percent for row in tree), 1),
        memory_bytes=sum(row.memory_bytes for row in tree),
    )


def shown_path(path: Path) -> str:
    """``path`` as the page shows it: under the owner's home folder as ``~/…``."""
    home: str = str(Path.home())
    text: str = str(path)
    return f"~{text[len(home) :]}" if text == home or text.startswith(f"{home}/") else text


def _unreadable(error: OSError) -> None:
    """A folder Dot keeps that it cannot read: its size would be short, so the measure fails and says where. A folder
    that went while it was counted (a cache of Chrome's, a run's own) takes nothing, as a file that went does."""
    if isinstance(error, FileNotFoundError):
        return
    raise DiagnosticsError(
        "a folder Dot keeps could not be read", {"path": str(error.filename), "error": error.strerror or str(error)}
    )


def _size(path: Path) -> int:
    """The bytes the file ``path``, or the files under the folder ``path``, take; none for a path that is not there."""
    if not path.exists():
        return 0
    if not path.is_dir():
        return path.lstat().st_size
    total: int = 0
    for folder, _, names in os.walk(path, onerror=_unreadable):
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except FileNotFoundError:
                continue  # a cache file of Chrome, or a run's, went while the folder was counted: it takes nothing
    return total


def _folders() -> tuple[tuple[str, tuple[Path, ...]], ...]:
    """What Dot keeps, by kind, and where: its store with its journal files, its working folder, the profiles of its
    own Chrome, what its runs saved, its journals of file changes, its eval reports, its skills, plugins and themes,
    its logs, and its home folder in all."""
    store: Path = db_path()
    return (
        ("Store", (store, store.with_name(f"{store.name}-wal"), store.with_name(f"{store.name}-shm"))),
        ("Workspace", (workspace_dir(),)),
        ("Dot's Chrome", (browser_profile_dir(), browser_profiles_dir(), browser_incognito_dir())),
        ("Screenshots", (screenshots_dir(),)),
        ("Downloads", (downloads_dir(),)),
        ("Undo journals", (undo_dir(),)),
        ("Eval reports", (evals_dir(),)),
        ("Skills, plugins and themes", (skills_dir(), playbooks_dir(), plugins_dir(), themes_dir())),
        ("Logs", (log_dir(),)),
        ("Dot's home in all", (dot_home(),)),
    )


def measure_disk() -> DiskView:
    """The room each kind of what Dot keeps takes now."""
    return DiskView(
        folders=tuple(
            FolderUse(
                name=name, paths=tuple(shown_path(path) for path in paths), bytes=sum(_size(path) for path in paths)
            )
            for name, paths in _folders()
        )
    )


def list_logs(folder: Path) -> LogsView:
    """The logs of ``folder``, the one most lately written first; none when the folder is not there yet."""
    files: list[LogFile] = []
    if folder.is_dir():
        for entry in folder.iterdir():
            if entry.is_file() and ".log" in entry.name:
                stat: os.stat_result = entry.stat()
                files.append(LogFile(name=entry.name, bytes=stat.st_size, modified_at=stat.st_mtime))
    return LogsView(
        folder=shown_path(folder), files=tuple(sorted(files, key=lambda file: (-file.modified_at, file.name)))
    )


def tail_lines(data: bytes, cut: bool, count: int, known: Sequence[str]) -> tuple[str, ...]:
    """The last ``count`` lines of ``data``, the end of a log, with every value of ``known`` (the secrets Dot holds)
    masked and the known forms of the others; when ``cut``, the read began inside the file and its first line is left
    out, as it may be the end of one. The end read is masked whole, before it is cut into lines, so a secret of many
    lines (a private key in PEM) is masked whatever lines of it are shown, a key whose start the read cut off too."""
    lines: list[str] = data.decode("utf-8", errors="replace").splitlines()
    kept: str = "\n".join(lines[1:] if cut else lines)
    # A read that began inside a private key holds its end without its start, which the masking finds the key by:
    # all that comes before that end is the key's.
    end: re.Match[str] | None = _KEY_END.search(kept)
    begin: re.Match[str] | None = _KEY_BEGIN.search(kept)
    if end is not None and (begin is None or begin.start() > end.start()):
        kept = f"***{kept[end.end() :]}"
    return tuple(redact_known(kept, known).splitlines()[-count:])


def read_tail(folder: Path, name: str, count: int) -> LogTail:
    """The last ``count`` lines of the log ``name`` of ``folder``; a name that is not one of its logs is refused."""
    if name not in {file.name for file in list_logs(folder).files}:
        raise NotFoundError("Dot has no log of this name", {"name": name, "folder": str(folder)})
    path: Path = folder / name
    try:
        size: int = path.stat().st_size
        start: int = max(0, size - _TAIL_BYTES)
        with path.open("rb") as handle:
            handle.seek(start)
            data: bytes = handle.read()
    except FileNotFoundError as error:
        raise NotFoundError("the log went while it was read", {"name": name, "folder": str(folder)}) from error
    return LogTail(name=name, lines=tail_lines(data, start > 0, count, known_secrets()))
