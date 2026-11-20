"""A commit writes its own item's records and no other's (item 1265).

An item's `changes/<n>.md` and `roadmap/<n>.md` are the words that item stands on: another item's commit that writes
one of them erases what that item did, and the roadmaps then disagree with the code. It happened twice while the
numbers of parallel work raced: a branch renumbered to the number main had just taken, and its records landed on the
item that took it. This reads each `Item N:` commit of a branch and fails when it writes `changes/<m>.md` or
`roadmap/<m>.md` for an m it does not name.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# A commit's subject: "Item 1265: ...", or "Items 1262, 1263 and 1264: ...".
_ITEM = re.compile(r"^Items?\b[^:]*")
_NUMBER = re.compile(r"\d+")
# An item's records: `changes/1265.md` and `roadmap/1265.md`.
_RECORD = re.compile(r"^(?:changes|roadmap)/(\d+)\.md$")
# What separates one commit's block from the next in `git log`'s output.
_SEPARATOR = "\x1e"


def item_numbers(subject: str) -> set[int]:
    """The items a commit subject names, empty for a commit that is no item's."""
    if not _ITEM.match(subject):
        return set()
    return {int(number) for number in _NUMBER.findall(subject.split(":", 1)[0])}


def offences(commits: list[tuple[str, list[str]]]) -> list[str]:
    """One line for each record a commit of one item wrote that belongs to another."""
    found: list[str] = []
    for subject, files in commits:
        items = item_numbers(subject)
        if not items:
            continue
        for path in files:
            record = _RECORD.match(path)
            if record is not None and int(record.group(1)) not in items:
                found.append(f"{subject}\n writes {path}, which is item {record.group(1)}'s")
    return found


def commits(base: str, head: str, cwd: str | None = None) -> list[tuple[str, list[str]]]:
    """The commits between `base` and `head` that touch a record, each with the records it touched."""
    out = subprocess.run(
        ["git", "log", f"--format={_SEPARATOR}%s", "--name-only", f"{base}..{head}", "--", "changes", "roadmap"],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    ).stdout
    found: list[tuple[str, list[str]]] = []
    for block in out.split(_SEPARATOR):
        lines = [line for line in block.splitlines() if line.strip() != ""]
        if lines:
            found.append((lines[0], lines[1:]))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="the branch the commit sits on")
    parser.add_argument("--head", default="HEAD", help="the commit to read back from")
    args = parser.parse_args(argv)
    found = offences(commits(args.base, args.head))
    for line in found:
        print(f"error: item records mixed: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
