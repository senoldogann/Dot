"""Which files of a pull request run the browser parts of the gate (gate.yml).

A push to main and a release run every browser part. A pull request runs them when a changed file can affect what
they do, and skips them otherwise: the account has five macOS runners, and a part that cannot see the change would
hold one for nothing. The daemon those tests start imports almost all of the package, so a change under
``src/dotagent`` runs them, except the command line and the modules the tests never import. A unit test, a smoke
test that is not one of theirs, the desktop shell and prose do not.

The script reads the changed paths on stdin, one per line, and prints ``true`` or ``false``. It is stdlib only, so
the job that asks it needs no ``uv sync``.
"""

import re
import sys

# The locks and the workflows that decide what the browser parts install and run. A change to the path check itself
# runs the parts too, so the run of that change still shows them.
_ALWAYS: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        ".github/workflows/gate.yml",
        ".github/workflows/browser.yml",
        ".github/workflows/release.yml",
        "scripts/browser_ci.py",
    }
)

# Modules the daemon and the browser tests do not import. tests/unit/test_browser_ci.py fails the day one of them
# joins that import path, and a new module is not listed here, so it runs the parts.
_NOT_IMPORTED: frozenset[str] = frozenset(
    {
        "src/dotagent/__main__.py",
        "src/dotagent/core/heap.py",
        "src/dotagent/daemon_failure.py",
        "src/dotagent/restart_when_idle.py",
        "src/dotagent/watchdog.py",
        "src/dotagent/evals/audit.py",
        "src/dotagent/evals/drafts.py",
        "src/dotagent/evals/reports.py",
        "src/dotagent/evals/scoring.py",
        "src/dotagent/evals/speed.py",
        "src/dotagent/evals/tool_cases.py",
        "src/dotagent/evals/toolcalls.py",
        "src/dotagent/evals/toolcalls_play.py",
    }
)

# The files the parts run besides the globs, and the one they import (test_browser.py) without running it whole.
_NAMED_TESTS: frozenset[str] = frozenset(
    {
        "tests/smoke/test_secret_fields.py",
        "tests/smoke/test_page_guards.py",
        "tests/smoke/test_face.py",
    }
)

_BROWSER_TEST: re.Pattern[str] = re.compile(r"tests/smoke/test_browser(_.*)?\.py\Z")
_PHONE_TEST: re.Pattern[str] = re.compile(r"tests/smoke/test_phone(_.*)?\.py\Z")


def _command_line(path: str) -> bool:
    """Whether ``path`` is the command line, which the pages do not import: the daemon serves them."""
    name: str = path.removeprefix("src/dotagent/")
    return name == "cli.py" or (name.startswith("cli_") and "/" not in name and name.endswith(".py"))


def affects_browser_tests(path: str) -> bool:
    """Whether a change to ``path`` can change what a browser part of the gate runs or what it sees."""
    if path in _ALWAYS or path.startswith(".github/actions/"):
        return True
    if path == "tests/conftest.py":
        return True
    if path.startswith("src/dotagent/"):
        return path not in _NOT_IMPORTED and not _command_line(path)
    if path.startswith("tests/smoke/"):
        name: str = path.rsplit("/", 1)[-1]
        # A helper beside the tests has no mark of its own; a new one would be missed by a list.
        if not name.startswith("test_"):
            return True
        return (
            path in _NAMED_TESTS or _BROWSER_TEST.fullmatch(path) is not None or _PHONE_TEST.fullmatch(path) is not None
        )
    return False


def runs_browser_tests(paths: list[str]) -> bool:
    """Whether a pull request changing ``paths`` runs the browser parts: when one of them can affect those tests."""
    return any(affects_browser_tests(path) for path in paths)


def main() -> None:
    paths: list[str] = [line for line in sys.stdin.read().splitlines() if line.strip()]
    run: bool = runs_browser_tests(paths)
    print(f"{len(paths)} files, browser parts: {'yes' if run else 'no'}", file=sys.stderr)
    sys.stdout.write("true\n" if run else "false\n")


if __name__ == "__main__":
    main()
