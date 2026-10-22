"""A pull request's title and body from the roadmap entry of its item (item 540).

The entry `roadmap/<item>.md` already says what was built, how it was checked and what was left; a body written
again by hand says it twice and drifts. The title is `Item <n>: <heading>` (the heading's first letter in lower case),
the body the entry without its heading and its `Log:`, then the attribution line.

Usage: uv run python scripts/pr_body.py <item> --title
       uv run python scripts/pr_body.py <item>
       gh pr create --title "$(uv run python scripts/pr_body.py 540 --title)" \\
         --body "$(uv run python scripts/pr_body.py 540)"
"""

import re
import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
_HEADING: re.Pattern[str] = re.compile(r"^### ([1-9]\d*)\. (.+)$")
_ATTRIBUTION: str = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


class EntryError(ValueError):
    """The roadmap entry is not in the shape the pull request is made from."""


def title(entry: str) -> str:
    """``Item <n>: <heading>``, the heading's first letter in lower case unless it begins an acronym (``UI of``)."""
    first: re.Match[str] | None = _HEADING.match(entry.splitlines()[0] if entry else "")
    if first is None:
        raise EntryError("the entry does not begin with `### <item>. <heading>`")
    heading: str = first.group(2)
    if not (len(heading) > 1 and heading[1].isupper()):
        heading = heading[0].lower() + heading[1:]
    return f"Item {first.group(1)}: {heading}"


def body(entry: str) -> str:
    """The entry without its heading and its log, and the attribution line under it."""
    title(entry)
    lines: list[str] = entry.splitlines()[1:]
    if "Log:" in lines:
        lines = lines[: lines.index("Log:")]
    return "\n".join(lines).strip() + f"\n\n{_ATTRIBUTION}\n"


def main(arguments: list[str]) -> int:
    if not arguments or not arguments[0].isdigit() or len(arguments) > 2 or arguments[1:] not in ([], ["--title"]):
        print(__doc__, file=sys.stderr)
        return 2
    path: Path = ROOT / "roadmap" / f"{arguments[0]}.md"
    if not path.is_file():
        print(f"pr_body: {path} does not exist: write the roadmap entry first", file=sys.stderr)
        return 1
    entry: str = path.read_text(encoding="utf-8")
    print(title(entry) if arguments[1:] else body(entry), end="" if not arguments[1:] else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
