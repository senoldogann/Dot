"""Fold the changelog entries of `changes/` into a release's section of CHANGELOG.md (item 459).

Every change records its changelog entry in a file of its own, `changes/<item>.md`, so that pull requests made side by
side do not all write the same lines at the top of CHANGELOG.md. A release gathers them: the entries under
**Unreleased** and those of `changes/` (the newest item first) become the section `## <version> (<date>)`, Unreleased
keeps its note and no entry, and the files of `changes/` go.

Usage: uv run python scripts/fold_changes.py <version> <date>
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
_UNRELEASED: str = "## Unreleased\n"
_ENTRY_FILE: re.Pattern[str] = re.compile(r"^(\d+)\.md$")
_VERSION: re.Pattern[str] = re.compile(r"^\d+\.\d+\.\d+$")
_DATE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FoldError(ValueError):
    """The changelog or an entry file is not in the shape the fold reads."""


@dataclass(frozen=True)
class Entry:
    """The changelog lines of one item: a list, each line of it starting `- ` or continuing the one before."""

    item: int
    text: str


def read_entries(folder: Path) -> list[Entry]:
    """The entry files of `folder`, the newest item first; a file not named `<item>.md` or not a list is refused."""
    entries: list[Entry] = []
    for path in sorted(folder.iterdir()):
        if path.name == "README.md":
            continue
        named: re.Match[str] | None = _ENTRY_FILE.match(path.name)
        if named is None:
            raise FoldError(f"{path} is not named <item>.md")
        text: str = path.read_text(encoding="utf-8").strip("\n")
        if not text.startswith("- ") or any(line and not line.startswith(("- ", "  ")) for line in text.splitlines()):
            raise FoldError(f"{path} is not a list of entries ('- ' lines, continued with two spaces)")
        entries.append(Entry(item=int(named.group(1)), text=text))
    return sorted(entries, key=lambda entry: entry.item, reverse=True)


def fold(changelog: str, entries: list[Entry], version: str, date: str) -> str:
    """`changelog` with the section of `version`: the entries of `entries`, then those under Unreleased, which keeps
    the paragraphs that are not entries (its note on `changes/`)."""
    if not _VERSION.match(version) or not _DATE.match(date):
        raise FoldError(f"a version is x.y.z and a date YYYY-MM-DD, not {version!r} and {date!r}")
    if f"## {version} " in changelog:
        raise FoldError(f"CHANGELOG.md has a section for {version} already")
    if changelog.count(_UNRELEASED) != 1:
        raise FoldError("CHANGELOG.md has no single '## Unreleased' section")
    before, _, rest = changelog.partition(_UNRELEASED)
    following: re.Match[str] | None = re.search(r"^## ", rest, re.M)
    unreleased, after = (rest, "") if following is None else (rest[: following.start()], rest[following.start() :])
    kept: list[str] = []
    listed: list[str] = []
    for block in re.split(r"\n(?=- )|\n\n", unreleased.strip("\n")):
        if block.strip() == "":
            continue
        (listed if block.startswith("- ") else kept).append(block.strip("\n"))
    released: list[str] = [entry.text for entry in entries] + listed
    if not released:
        raise FoldError("nothing to release: no entry under Unreleased nor in changes/")
    note: str = "\n\n".join(kept)
    head: str = f"{before}{_UNRELEASED}\n" + (f"{note}\n\n" if note else "")
    return f"{head}## {version} ({date})\n\n" + "\n".join(released) + "\n\n" + after


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    version, date = sys.argv[1], sys.argv[2]
    changelog: Path = ROOT / "CHANGELOG.md"
    folder: Path = ROOT / "changes"
    entries: list[Entry] = read_entries(folder)
    changelog.write_text(fold(changelog.read_text(encoding="utf-8"), entries, version, date), encoding="utf-8")
    for entry in entries:
        (folder / f"{entry.item}.md").unlink()
    print(f"CHANGELOG.md: {version} ({date}) with {len(entries)} entry file(s) of changes/ folded in")


if __name__ == "__main__":
    main()
