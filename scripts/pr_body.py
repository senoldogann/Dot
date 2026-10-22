"""A pull request's title and body from the roadmap entries of its items (items 540 and 541).

A pull request carries a batch: the items a session built, each with its own `roadmap/<item>.md`. The entry already
says what was built, how it was checked and what was left; a body written again by hand says it twice and drifts. For
one item the title is `Item <n>: <heading>` (the heading's first letter in lower case) and the body the entry without
its heading and its `Log:`; for several, `Items <n>, <m> and <k>: <heading>; <heading>; <heading>` and one section for
each, under `## Item <n>: <heading>`; the attribution line ends it.

Usage: uv run python scripts/pr_body.py <item>... --title
       uv run python scripts/pr_body.py <item>...
       gh pr create --title "$(uv run python scripts/pr_body.py 541 542 --title)" \\
         --body "$(uv run python scripts/pr_body.py 541 542)"
"""

import re
import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
_HEADING: re.Pattern[str] = re.compile(r"^### ([1-9]\d*)\. (.+)$")
_ATTRIBUTION: str = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


class EntryError(ValueError):
    """The roadmap entry is not in the shape the pull request is made from."""


def _heading(entry: str) -> tuple[str, str]:
    """An entry's item number and its heading, the heading's first letter in lower case unless it begins an acronym
    (``UI of``)."""
    first: re.Match[str] | None = _HEADING.match(entry.splitlines()[0] if entry else "")
    if first is None:
        raise EntryError("the entry does not begin with `### <item>. <heading>`")
    heading: str = first.group(2)
    if not (len(heading) > 1 and heading[1].isupper()):
        heading = heading[0].lower() + heading[1:]
    return first.group(1), heading


def title(entry: str) -> str:
    """``Item <n>: <heading>``."""
    number, heading = _heading(entry)
    return f"Item {number}: {heading}"


def body(entry: str) -> str:
    """The entry without its heading and its log, and the attribution line under it."""
    return _section(entry) + f"\n\n{_ATTRIBUTION}\n"


def _section(entry: str) -> str:
    title(entry)
    lines: list[str] = entry.splitlines()[1:]
    if "Log:" in lines:
        lines = lines[: lines.index("Log:")]
    return "\n".join(lines).strip()


def batch_title(entries: list[str]) -> str:
    """The title of a pull request that carries ``entries``: one item's own, or all the numbers and all the headings."""
    if len(entries) == 1:
        return title(entries[0])
    parts: list[tuple[str, str]] = [_heading(entry) for entry in entries]
    numbers: list[str] = [number for number, _ in parts]
    return f"Items {', '.join(numbers[:-1])} and {numbers[-1]}: " + "; ".join(heading for _, heading in parts)


def batch_body(entries: list[str]) -> str:
    """The body of a pull request that carries ``entries``: the one item's body, or a section for each item."""
    if len(entries) == 1:
        return body(entries[0])
    sections: list[str] = [f"## {title(entry)}\n\n{_section(entry)}" for entry in entries]
    return "\n\n".join(sections) + f"\n\n{_ATTRIBUTION}\n"


def main(arguments: list[str]) -> int:
    wants_title: bool = arguments[-1:] == ["--title"]
    items: list[str] = arguments[:-1] if wants_title else arguments
    if not items or not all(item.isdigit() for item in items) or len(set(items)) != len(items):
        print(__doc__, file=sys.stderr)
        return 2
    entries: list[str] = []
    for item in items:
        path: Path = ROOT / "roadmap" / f"{item}.md"
        if not path.is_file():
            print(f"pr_body: {path} does not exist: write the roadmap entry first", file=sys.stderr)
            return 1
        entries.append(path.read_text(encoding="utf-8"))
    print(batch_title(entries) if wants_title else batch_body(entries), end="\n" if wants_title else "")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
