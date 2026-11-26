"""The number the next roadmap item takes (items 540, 541 and 1891): one more than the highest that is held anywhere a
session in this clone or on GitHub may have put one, and it is reserved at once.

Where numbers are held:

- `origin/main`'s `roadmap/` and `changes/` files;
- pull requests, open and the last closed ones (a closed one may be reopened): their titles (`Item <n>:`,
  `Items <n>, <m> and <k>:`), bodies, branch names and, for open ones, the entries they add;
- every branch on `origin` and every local branch (all worktrees of this repository share them): their names
  (`item-<n>-...`), the subjects of their commits not on main and the entries those commits add;
- every worktree of this repository: its checked-out commit (detached ones too) and the `roadmap/` and `changes/`
  files in its folder, committed or not;
- the reservations file, `item-reservations.json` in the repository's common git directory, which every worktree of this
  clone shares: each number this script gave (with the branch it was given on and when) for three days.

Reading the reservations, taking the next numbers and writing them back happen under a lock
(`item-reservations.lock`), so two sessions asking at the same moment never get the same number. A batch takes its
numbers together: `scripts/next_item.py 3` prints three consecutive ones.

Usage: uv run python scripts/next_item.py [count]
"""

import fcntl
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_FILE: re.Pattern[str] = re.compile(r"(?:roadmap|changes)/([1-9]\d*)\.md")
_TITLE: re.Pattern[str] = re.compile(r"\bItems? ([1-9]\d*(?:(?:, | and )[1-9]\d*)*)")
_BRANCH: re.Pattern[str] = re.compile(r"\bitem-([1-9]\d*)\b")
RESERVATION_DAYS: timedelta = timedelta(days=3)
_CLOSED_PULLS: int = 100
_OPEN_PULLS: int = 500


def numbers_in(text: str) -> set[int]:
    """The item numbers a file path, a pull request title or a branch name holds."""
    found: set[int] = {int(number) for number in _FILE.findall(text)}
    found |= {int(number) for number in _BRANCH.findall(text)}
    for group in _TITLE.findall(text):
        found |= {int(number) for number in re.findall(r"\d+", group)}
    return found


def taken(*sources: tuple[str, Iterable[str]]) -> dict[int, str]:
    """The item numbers in use, each with the name of the first of ``sources`` (``(name, texts)``) that holds it."""
    found: dict[int, str] = {}
    for name, texts in sources:
        for text in texts:
            for number in numbers_in(text):
                found.setdefault(number, name)
    return found


def next_item(*sources: tuple[str, Iterable[str]]) -> int:
    """One more than the highest number in use; 1 when none is."""
    return max(taken(*sources), default=0) + 1


@dataclass(frozen=True)
class Reservation:
    """A number this script gave: on which branch and when (ISO 8601, UTC)."""

    number: int
    branch: str
    time: str


def live(reservations: Iterable[Reservation], now: datetime, days: timedelta) -> list[Reservation]:
    """The reservations made less than ``days`` before ``now``."""
    return [held for held in reservations if now - datetime.fromisoformat(held.time) < days]


def choose(
    found: dict[int, str], reservations: list[Reservation], count: int, branch: str, now: datetime
) -> list[Reservation]:
    """``count`` consecutive numbers after the highest of ``found`` and ``reservations``, reserved for ``branch``."""
    first: int = max([*found, *(held.number for held in reservations)], default=0) + 1
    return [Reservation(number, branch, now.isoformat()) for number in range(first, first + count)]


class SourceError(RuntimeError):
    """A place that holds item numbers could not be read, so a number would be a guess."""


def _read(path: Path) -> list[Reservation]:
    if not path.exists():
        return []
    rows: list[dict[str, int | str]] = json.loads(path.read_text(encoding="utf-8"))
    held: list[Reservation] = []
    for row in rows:
        number, branch, time = row["number"], row["branch"], row["time"]
        if not (isinstance(number, int) and isinstance(branch, str) and isinstance(time, str)):
            raise SourceError(f"{path} holds a reservation of the wrong shape: {row!r}; fix or delete that row")
        held.append(Reservation(number, branch, time))
    return held


def reserve(directory: Path, found: dict[int, str], count: int, branch: str, now: datetime) -> list[Reservation]:
    """Take ``count`` numbers past ``found`` and the live reservations in ``directory``, and record them there.

    The lock is held from reading the reservations to replacing the file, so no two callers get one number."""
    path: Path = directory / "item-reservations.json"
    with (directory / "item-reservations.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        kept: list[Reservation] = live(_read(path), now, RESERVATION_DAYS)
        given: list[Reservation] = choose(found, kept, count, branch, now)
        written: Path = path.with_suffix(f".{os.getpid()}.tmp")
        written.write_text(json.dumps([asdict(held) for held in [*kept, *given]], indent=1) + "\n", encoding="utf-8")
        os.replace(written, path)
        return given


def _lines(*command: str) -> list[str]:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SourceError(f"`{' '.join(command)}` failed: {done.stderr.strip() or done.returncode}")
    return done.stdout.splitlines()


def _pulls() -> list[str]:
    texts: list[str] = []
    fields: str = "title,body,headRefName"
    for pull in json.loads(
        "\n".join(
            _lines("gh", "pr", "list", "--state", "open", "--limit", str(_OPEN_PULLS), "--json", f"{fields},files")
        )
    ):
        texts.extend([pull["title"], pull["body"], pull["headRefName"], *(file["path"] for file in pull["files"])])
    closed: list[str] = _lines("gh", "pr", "list", "--state", "closed", "--limit", str(_CLOSED_PULLS), "--json", fields)
    for pull in json.loads("\n".join(closed)):
        texts.extend([pull["title"], pull["body"], pull["headRefName"]])
    return texts


def _worktrees() -> tuple[list[str], list[str]]:
    """The commits every worktree has checked out, and the entries in their folders."""
    heads: list[str] = []
    files: list[str] = []
    for block in "\n".join(_lines("git", "worktree", "list", "--porcelain")).split("\n\n"):
        fields: dict[str, str] = dict(line.partition(" ")[::2] for line in block.splitlines() if line)
        if "worktree" not in fields or "prunable" in fields or "bare" in fields:
            continue
        heads.append(fields["HEAD"])
        for folder in ("roadmap", "changes"):
            files.extend(f"{folder}/{entry.name}" for entry in (Path(fields["worktree"]) / folder).glob("*.md"))
    return heads, files


def _sources() -> list[tuple[str, list[str]]]:
    _lines("git", "fetch", "--quiet", "--prune", "origin")
    on_main: list[str] = _lines("git", "ls-tree", "-r", "--name-only", "origin/main", "roadmap", "changes")
    heads, in_folders = _worktrees()
    refs: list[str] = ["--remotes=origin", "--branches", *heads, "--not", "origin/main"]
    branches: list[str] = _lines(
        "git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"
    )
    branches.extend(_lines("git", "log", "--format=%s", *refs))
    branches.extend(_lines("git", "log", "--format=", "--name-only", *refs, "--", "roadmap", "changes"))
    return [
        ("main", on_main),
        ("pull request", _pulls()),
        ("branch", branches),
        ("worktree folder", in_folders),
    ]


def _here() -> tuple[Path, str]:
    """The repository's common git directory, which all its worktrees share, and the branch checked out here."""
    common: str = _lines("git", "rev-parse", "--path-format=absolute", "--git-common-dir")[0]
    return Path(common), _lines("git", "rev-parse", "--abbrev-ref", "HEAD")[0]


def main(arguments: list[str]) -> int:
    if arguments and not (len(arguments) == 1 and arguments[0].isdigit() and int(arguments[0]) > 0):
        print(__doc__, file=sys.stderr)
        return 2
    count: int = int(arguments[0]) if arguments else 1
    try:
        directory, branch = _here()
        found: dict[int, str] = taken(*_sources())
        given: list[Reservation] = reserve(directory, found, count, branch, datetime.now(UTC))
    except (SourceError, OSError, KeyError, ValueError, TypeError) as error:
        print(f"next_item: {error}; no number is given, as it could collide with one it did not see", file=sys.stderr)
        return 1
    print(" ".join(str(held.number) for held in given))
    if found:
        highest: int = max(found)
        print(f"({highest} is the highest taken, in {found[highest]}; reserved for {branch})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
