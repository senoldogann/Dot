"""An index of the roadmap items from 459 on (item 1317): `roadmap/INDEX.md`, one row for each `roadmap/<n>.md`.

ROADMAP.md holds the items up to 458 and stops there; the items after it are files of their own, with nothing to read
them by. This reads each file's `### <n>. <title>` line and its `**Status: ...**` line (the status, as far as its first
clause, and the date in it) and writes a table, newest item first. It reads the files that exist and nothing else, so a
branch that adds `roadmap/<n>.md` changes the table by one row, and the table does not depend on git.

Usage:
    uv run python scripts/roadmap_index.py           write roadmap/INDEX.md
    uv run python scripts/roadmap_index.py --check   exit 1 when roadmap/INDEX.md is not what the files make

`scripts/check_item_records.py` runs the check, so the gate fails on an index that was not written again.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT: Path = Path(__file__).resolve().parent.parent
ROADMAP: Path = ROOT / "roadmap"
INDEX: Path = ROADMAP / "INDEX.md"

_FILE: re.Pattern[str] = re.compile(r"^(\d+)\.md$")
_TITLE: re.Pattern[str] = re.compile(r"^### (\d+)\. (.+?)\s*$", re.MULTILINE)
# The status runs to the closing `**`, which may stand on a later line of a long status.
_STATUS: re.Pattern[str] = re.compile(r"^\*\*Status:\s*(.+?)\*\*", re.MULTILINE | re.DOTALL)
_DATE: re.Pattern[str] = re.compile(r"\d{4}-\d{2}-\d{2}")
# Where the first clause of a status ends: "done (2026-11-21)", "done, in part", "done; needs the owner's look".
_CLAUSE_END: re.Pattern[str] = re.compile(r"\s*[(;,.]")

HEADER: str = """# Roadmap index

Every item that has a file here (456, and 459 on), newest first, made by `uv run python scripts/roadmap_index.py` from
the `roadmap/<item>.md` files: run it again when an item is added or its title or status changes (`--check` fails on
a stale index, and the gate runs it). Items up to 458 are in [ROADMAP.md](../ROADMAP.md).

| Item | Status | Date | Title |
| ---- | ------ | ---- | ----- |
"""


class Entry(NamedTuple):
    number: int
    title: str
    status: str
    date: str


def entry(number: int, text: str) -> Entry:
    """The row of one item's file. A file with no `### <n>. <title>` line of its own number is an error to fix, not a
    row to guess at."""
    titles: list[tuple[str, str]] = _TITLE.findall(text)
    own: list[str] = [title for found, title in titles if int(found) == number]
    if not own:
        raise ValueError(f"roadmap/{number}.md has no '### {number}. <title>' line")
    status_found = _STATUS.search(text)
    status: str = " ".join(status_found.group(1).split()) if status_found is not None else ""
    date_found = _DATE.search(status)
    return Entry(
        number=number,
        title=own[0],
        status=_CLAUSE_END.split(status, maxsplit=1)[0].strip() if status != "" else "no status",
        date=date_found.group(0) if date_found is not None else "",
    )


def entries(folder: Path = ROADMAP) -> list[Entry]:
    """The items the files of `folder` hold, newest (highest number) first."""
    found: list[Entry] = []
    for path in folder.iterdir():
        match = _FILE.match(path.name)
        if match is not None:
            found.append(entry(int(match.group(1)), path.read_text(encoding="utf-8")))
    return sorted(found, key=lambda item: item.number, reverse=True)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render(items: list[Entry]) -> str:
    """The text of INDEX.md for the items."""
    rows: list[str] = [
        f"| [{item.number}]({item.number}.md) | {_cell(item.status)} | {item.date} | {_cell(item.title)} |"
        for item in items
    ]
    return HEADER + "\n".join(rows) + "\n"


def is_current(folder: Path = ROADMAP) -> bool:
    """Whether INDEX.md in `folder` is what its item files make."""
    index: Path = folder / "INDEX.md"
    return index.is_file() and index.read_text(encoding="utf-8") == render(entries(folder))


STALE: str = (
    "roadmap/INDEX.md is not what roadmap/*.md make: run `uv run python scripts/roadmap_index.py` and commit it"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when roadmap/INDEX.md is stale, write nothing")
    args = parser.parse_args(argv)
    if args.check:
        if is_current():
            return 0
        print(f"error: {STALE}", file=sys.stderr)
        return 1
    INDEX.write_text(render(entries()), encoding="utf-8")
    print(f"wrote {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
