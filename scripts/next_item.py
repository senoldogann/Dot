"""The number the next roadmap item takes (items 540 and 541): one more than the highest that is taken on `origin/main`
(its `roadmap/` and `changes/` files), in an open pull request (its title, `Item <n>:` or `Items <n>, <m> and <k>:`, and
the entries it adds) or on a pushed branch (`item-<n>-...`, and the entries a branch ahead of main adds).

Several sessions take "the highest plus one" at once, and the second to push renumbers its files, its references and
its log; asking all of these places at the moment of the first push avoids that. A batch takes its numbers together:
`scripts/next_item.py 3` prints three consecutive ones.

Usage: uv run python scripts/next_item.py [count]
"""

import json
import re
import subprocess
import sys
from collections.abc import Iterable

_FILE: re.Pattern[str] = re.compile(r"(?:roadmap|changes)/([1-9]\d*)\.md")
_TITLE: re.Pattern[str] = re.compile(r"\bItems? ([1-9]\d*(?:(?:, | and )[1-9]\d*)*)")
_BRANCH: re.Pattern[str] = re.compile(r"\bitem-([1-9]\d*)\b")


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


class SourceError(RuntimeError):
    """A place that holds item numbers could not be read, so a number would be a guess."""


def _lines(*command: str) -> list[str]:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SourceError(f"`{' '.join(command)}` failed: {done.stderr.strip() or done.returncode}")
    return done.stdout.splitlines()


def _sources() -> list[tuple[str, list[str]]]:
    _lines("git", "fetch", "--quiet", "origin")
    on_main: list[str] = _lines("git", "ls-tree", "-r", "--name-only", "origin/main", "roadmap", "changes")
    heads: list[str] = _lines("git", "ls-remote", "--heads", "origin")
    pulls: list[str] = []
    branches: list[str] = list(heads)
    for pull in json.loads(
        "\n".join(_lines("gh", "pr", "list", "--state", "open", "--json", "title,headRefName,files"))
    ):
        pulls.extend([pull["title"], *(file["path"] for file in pull["files"])])
        branches.append(pull["headRefName"])
    # A branch pushed with items ahead of main (no pull request yet) holds numbers in its entries, not only in its name.
    highest: int = max(numbers_in("\n".join(on_main)), default=0)
    for head in heads:
        name: str = head.partition("refs/heads/")[2]
        if any(number > highest for number in numbers_in(name)):
            branches.extend(
                _lines("git", "diff", "--name-only", f"origin/main...origin/{name}", "--", "roadmap", "changes")
            )
    return [("main", on_main), ("pull request", pulls), ("branch", branches)]


def main(arguments: list[str]) -> int:
    if arguments and not (len(arguments) == 1 and arguments[0].isdigit() and int(arguments[0]) > 0):
        print(__doc__, file=sys.stderr)
        return 2
    count: int = int(arguments[0]) if arguments else 1
    try:
        sources: list[tuple[str, list[str]]] = _sources()
    except (SourceError, OSError, KeyError, ValueError) as error:
        print(f"next_item: {error}; no number is given, as it could collide with one it did not see", file=sys.stderr)
        return 1
    found: dict[int, str] = taken(*sources)
    first: int = next_item(*sources)
    print(" ".join(str(number) for number in range(first, first + count)))
    if found:
        highest: int = max(found)
        print(f"({highest} is the highest taken, in {found[highest]})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
