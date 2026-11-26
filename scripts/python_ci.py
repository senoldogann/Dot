"""Which files of a push to main run the gate's macOS jobs, `gate` and `gate (part 2)` (gate.yml).

Those jobs test the Python package on macOS: the unit tests that need macOS, `dotx eval check` and the smoke tests. They
run on the push to main that merges a pull request, never on the pull request (item 1886). A push whose every file
(those changed since main's last green gate) is one of a short list that nothing they run reads skips them, so that an
iPhone app's change or an item's records do not hold two of the account's five macOS runners. Everything else runs
them, a file this script does not know included: a new folder runs the gate until it is listed here. A release and a
run by hand run them always, and `checks` (Linux) runs for every pull request and push that is more than prose.

The list: the iPhone app (`ios/`, tested by ios.yml), the item records (`roadmap/`, `changes/`, which `checks` reads),
prose (`docs/`, the Markdown at the root, the issue and pull request templates) and the workflows of other checks
(ios.yml, desktop.yml). The desktop shell (`desktop/`) is not in it: smoke tests load its pages (`desktop/ui`); nor
is `docs/freedom.md`, which a test that runs on macOS alone reads.

The script reads the changed paths on stdin, one per line, and prints ``true`` or ``false``. It is stdlib only, so the
job that asks it needs no ``uv sync``.
"""

import sys

# Folders nothing of the macOS jobs reads. A unit test that reads one (test_ios_icons.py reads ios/, the item records'
# tests read roadmap/) runs on Linux, in `checks`, which every pull request that is more than prose runs.
_NOT_READ_UNDER: tuple[str, ...] = ("ios/", "roadmap/", "changes/", "docs/", ".github/ISSUE_TEMPLATE/")

_NOT_READ: frozenset[str] = frozenset(
    {
        ".github/workflows/ios.yml",
        ".github/workflows/desktop.yml",
        ".github/pull_request_template.md",
    }
)


# Read by a test that runs on macOS alone (tests/unit/test_freedom_contract.py, listed in tests/unit/conftest.py).
_READ_ON_MACOS: frozenset[str] = frozenset({"docs/freedom.md"})


def affects_python_gate(path: str) -> bool:
    """Whether a change to ``path`` can change what `gate` or `gate (part 2)` runs or what it sees."""
    if path in _READ_ON_MACOS:
        return True
    if path in _NOT_READ or path.startswith(_NOT_READ_UNDER):
        return False
    # The Markdown at the root (README.md, ROADMAP.md, CHANGELOG.md, ...); one in a folder may be a skill Dot reads.
    return not ("/" not in path and path.endswith(".md"))


def runs_python_gate(paths: list[str]) -> bool:
    """Whether a pull request changing ``paths`` runs the macOS jobs of the gate: when one of them can affect them, or
    when no file was read at all (an answer that cannot be read runs them)."""
    return not paths or any(affects_python_gate(path) for path in paths)


def main() -> None:
    paths: list[str] = [line for line in sys.stdin.read().splitlines() if line.strip()]
    run: bool = runs_python_gate(paths)
    print(f"{len(paths)} files, the gate's macOS jobs: {'yes' if run else 'no'}", file=sys.stderr)
    sys.stdout.write("true\n" if run else "false\n")


if __name__ == "__main__":
    main()
