"""The number the next roadmap item takes (item 540): one more than the highest that is taken on `origin/main` (its
`roadmap/` and `changes/` files), in an open pull request (`Item <n>: ...`) or on a pushed branch (`item-<n>-...`).

Several sessions take "the highest plus one" at once, and the second to push renumbers its files, its references and
its log; asking all three places at the moment of the first push avoids that.

A batch takes its numbers together (item 541): `scripts/next_item.py 3` prints three consecutive ones.

Usage: uv run python scripts/next_item.py [count]
"""

import json
import re
import subprocess
import sys
from collections.abc import Iterable

_FILE: re.Pattern[str] = re.compile(r"(?:roadmap|changes)/([1-9]\d*)\.md")
_TITLE: re.Pattern[str] = re.compile(r"\bItem ([1-9]\d*)\b")
_BRANCH: re.Pattern[str] = re.compile(r"\bitem-([1-9]\d*)\b")


def taken(files: Iterable[str], titles: Iterable[str], branches: Iterable[str]) -> dict[int, str]:
    """The item numbers in use, each with where it was seen first: ``main``, ``pull request`` or ``branch``."""
    found: dict[int, str] = {}
    for where, pattern, texts in (
        ("main", _FILE, files),
        ("pull request", _TITLE, titles),
        ("branch", _BRANCH, branches),
    ):
        for text in texts:
            for number in pattern.findall(text):
                found.setdefault(int(number), where)
    return found


def next_item(files: Iterable[str], titles: Iterable[str], branches: Iterable[str]) -> int:
    """One more than the highest number in use; 1 when none is."""
    return max(taken(files, titles, branches), default=0) + 1


class SourceError(RuntimeError):
    """A place that holds item numbers could not be read, so a number would be a guess."""


def _lines(*command: str) -> list[str]:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SourceError(f"`{' '.join(command)}` failed: {done.stderr.strip() or done.returncode}")
    return done.stdout.splitlines()


def main(arguments: list[str]) -> int:
    if arguments and not (len(arguments) == 1 and arguments[0].isdigit() and int(arguments[0]) > 0):
        print(__doc__, file=sys.stderr)
        return 2
    count: int = int(arguments[0]) if arguments else 1
    try:
        _lines("git", "fetch", "--quiet", "origin")
        files: list[str] = _lines("git", "ls-tree", "-r", "--name-only", "origin/main", "roadmap", "changes")
        branches: list[str] = _lines("git", "ls-remote", "--heads", "origin")
        titles: list[str] = []
        for pull in json.loads("\n".join(_lines("gh", "pr", "list", "--state", "open", "--json", "title,headRefName"))):
            titles.append(pull["title"])
            branches.append(pull["headRefName"])
    except (SourceError, OSError) as error:
        print(f"next_item: {error}; no number is given, as it could collide with one it did not see", file=sys.stderr)
        return 1
    found: dict[int, str] = taken(files, titles, branches)
    first: int = next_item(files, titles, branches)
    print(" ".join(str(number) for number in range(first, first + count)))
    if found:
        highest: int = max(found)
        print(f"({highest} is the highest taken, on {found[highest]})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
