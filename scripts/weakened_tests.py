"""What a branch does to the tests that check it, read from its diff alone (item 2100).

Main went red, more than once in a day, because one pull request changed a test's words while another changed the code
it checks, and fixes concluded "the test was wrong" with nothing but the author's word for it. A test a change weakens
is a check the change no longer has to pass, so the weakening has to be said and answered:

- ``check``: fails when the branch weakens a test and no ``Test-change:`` line of its commits names that test's file
  with a reason (three words at least besides the name). The gate runs it on every pull request, main's own copy of it
  when main has one, so that a branch cannot loosen the check that reads it; the pre-push hook runs it before the
  review.
- ``brief``: the section the pre-push review is given (REVIEW.md, "Test changes"): each test file the change edits,
  what it weakened there, and the ``Test-change:`` lines, for the reviewer to answer why each test changed and what its
  old assertion caught. Nothing is printed when the change edits no test that checks code it also changes, and weakens
  none.

A weakening is, in a test file (``tests/``, and the Swift files under an ``ios/`` folder of tests):

- an assertion line removed with no identical line added back anywhere in the tests (whitespace aside, so a moved one
  is not, nor one its hunk wraps over several lines), which takes in an assertion whose expected value was changed;
- a test (a function, a ``Test`` class, a Swift ``@Test``) removed the same way;
- a skip, an xfail, a fixme, a rerun or a ``pytest.mark.timeout`` added to a test that was there: one in a new file, or
  in a hunk that adds a test of its own, guards a new test (``skipif`` on another platform, a live test's
  ``XCTSkip``), and one the diff removes elsewhere moved;
- a timeout raised within one hunk, the same name's (``timeout_ms`` is not compared with ``timeout``).

Strings and comments are not read for marks, so a test that holds ``"pytest.mark.skip"`` as data adds no skip. It is a
cheap static read of the diff: an assertion spread over several lines is seen by its first line, a skip in
``conftest.py`` or a rerun in ``pyproject.toml`` is not seen, and a looser comparison on a line that is not an
assertion is the review's to see. A ``Test-change:`` line answers for its file over the whole branch.

Stdlib only. Usage: ``weakened_tests.py check --base <rev> --head <rev>``, ``weakened_tests.py brief --base <rev>
--since <rev> --head <rev>``: the branch is read from where it leaves ``--base`` (their merge base); the brief lists
the tests edited since ``--since`` (the commit the review reads from) and every ``Test-change:`` line of the branch.
Exit 0: nothing to answer (``check``) or the brief printed; 1: a weakening with no ``Test-change:`` line, listed; 2: git
failed.
"""

import argparse
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

# What makes a path a test file: the Python and JavaScript tests, and the Swift files of the iPhone app's test targets.
_TEST_PATH = re.compile(r"^tests/|^ios/.*Tests/.*\.swift$")
# Prose a change may edit next to its tests without changing what is checked.
_PROSE_PATH = re.compile(r"\.md$|^docs/|^roadmap/|^changes/")
# A line that asserts, in Python (pytest, unittest), JavaScript (node:test, Playwright) or Swift (XCTest, Testing).
_ASSERTION = re.compile(
    r"^(?:assert\b|(?:with\s+)?self\.assert\w*\(|(?:with\s+)?pytest\.raises\(|(?:await\s+)?expect\(|assert\.\w+\(|assert\(|"
    r"XCTAssert\w*\(|XCTFail\(|#expect\(|#require\(|try\s+#require\()"
)
# A test function: a removed one takes its assertions with it.
_TEST_FUNCTION = re.compile(
    r"^(?:async\s+)?def\s+(test_\w+)|^func\s+(test\w+)\s*\(|^(?:it|test)\(\s*[\"'`]([^\"'`]+)|^class\s+(Test\w*)|"
    r"^@Test\b.*?\bfunc\s+(\w+)\s*\("
)
_SKIP = re.compile(
    r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail|importorskip)\(|unittest\.skip|"
    r"\b(?:it|test|describe)\.(?:skip|todo|fixme)\(|\{\s*skip\s*:|\btodo\s*:\s*true|XCTSkip|\.disabled\(|"
    r"\.fixme\(|@unittest\.expectedFailure"
)
_RERUN = re.compile(r"pytest\.mark\.flaky\b|\breruns\s*=|--reruns\b|\bretries\s*:")
_TIMEOUT_MARK = re.compile(r"pytest\.mark\.timeout\(")
# A timeout's number: ``timeout=5``, ``timeout_s: 2.5``, ``timeout(10)``, ``TIMEOUT = 30``, ``timeout: 5_000``.
_TIMEOUT_VALUE = re.compile(r"(timeout\w*)\s*[=:(]\s*([0-9][0-9_]*(?:\.[0-9]+)?)", re.IGNORECASE)
# A string literal, and a comment to the end of the line: neither is a mark.
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`')
_COMMENT = re.compile(r"(?:^|\s)#(?!expect\b|require\b)(?:\s.*)?$|//.*$")
_HUNK = re.compile(r"^@@ ")
_EXPLAINED = re.compile(r"^Test-change:\s*(.*\S)\s*$", re.MULTILINE)
# The words a Test-change line needs besides the file's name: a reason, not a label.
_REASON_WORDS = 3
_RECORD_SEPARATOR = "\x1e"


@dataclass(frozen=True)
class Hunk:
    """The lines one hunk of a file's diff removes and adds, without their ``-``/``+``."""

    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclass(frozen=True)
class FileDiff:
    """One file's hunks; ``path`` is its new path, or its old one when the change deletes it; ``new`` when the change
    adds the file."""

    path: str
    new: bool
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True)
class Weakening:
    """One way a change weakens the test file ``path``: ``kind`` and the line that shows it."""

    path: str
    kind: str
    line: str


def is_test(path: str) -> bool:
    """Whether ``path`` is a test file."""
    return bool(_TEST_PATH.search(path))


def is_code(path: str) -> bool:
    """Whether ``path`` is code a test may check: neither a test nor prose."""
    return not is_test(path) and not _PROSE_PATH.search(path)


def parse_diff(text: str) -> tuple[FileDiff, ...]:
    """The files of a ``git diff -U0`` and the lines each hunk removes and adds."""
    files: list[FileDiff] = []
    path: str = ""
    old_path: str = ""
    hunks: list[Hunk] = []
    removed: list[str] = []
    added: list[str] = []
    in_hunk: bool = False

    def close_hunk() -> None:
        if in_hunk:
            hunks.append(Hunk(removed=tuple(removed), added=tuple(added)))
        removed.clear()
        added.clear()

    def close_file() -> None:
        name: str = path or old_path
        if name:
            files.append(FileDiff(path=name, new=bool(path) and not old_path, hunks=tuple(hunks)))
        hunks.clear()

    for line in text.splitlines():
        if line.startswith("diff --git "):
            close_hunk()
            in_hunk = False
            close_file()
            path, old_path = "", ""
        elif not in_hunk and line.startswith("--- "):
            old_path = line[4:].removeprefix("a/") if line[4:] != "/dev/null" else ""
        elif not in_hunk and line.startswith("+++ "):
            path = line[4:].removeprefix("b/") if line[4:] != "/dev/null" else ""
        elif _HUNK.match(line):
            close_hunk()
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            removed.append(line[1:])
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    close_hunk()
    close_file()
    return tuple(files)


def _squeezed(line: str) -> str:
    return "".join(line.split())


def _assertion(line: str) -> bool:
    return bool(_ASSERTION.match(line.strip()))


def _test_name(line: str) -> str | None:
    found: re.Match[str] | None = _TEST_FUNCTION.match(line.strip())
    if found is None:
        return None
    return next(group for group in found.groups() if group)


def _code(line: str) -> str:
    """``line`` without its string literals and its comment, squeezed: what a mark is read in."""
    return _squeezed(_COMMENT.sub("", _STRING.sub('""', line)))


def _mark(line: str) -> str | None:
    """The kind of weakening the code of ``line`` adds, if it adds one."""
    code: str = _COMMENT.sub("", _STRING.sub('""', line))
    if _SKIP.search(code):
        return "skip or xfail added"
    if _RERUN.search(code):
        return "rerun added"
    if _TIMEOUT_MARK.search(code):
        return "timeout added"
    return None


def _timeouts(lines: tuple[str, ...]) -> dict[str, float]:
    """The largest value of each timeout's name in ``lines`` (names compared without case)."""
    largest: dict[str, float] = {}
    for line in lines:
        for name, value in _TIMEOUT_VALUE.findall(line):
            key: str = name.lower()
            largest[key] = max(largest.get(key, float(value.replace("_", ""))), float(value.replace("_", "")))
    return largest


def _wrapped(line: str, hunk: Hunk) -> bool:
    """Whether the removed assertion ``line`` stands whole in what ``hunk`` adds, spread over its lines (a reflow)."""
    added: str = _squeezed("".join(hunk.added))
    return re.search(rf"{re.escape(_squeezed(line))}(?![\w.\[])", added) is not None


def weakenings(files: tuple[FileDiff, ...]) -> tuple[Weakening, ...]:
    """Every weakening the diff ``files`` makes to its test files, in the order of the diff."""
    tests: tuple[FileDiff, ...] = tuple(diff for diff in files if is_test(diff.path))
    added_assertions: Counter[str] = Counter(
        _squeezed(line) for diff in tests for hunk in diff.hunks for line in hunk.added if _assertion(line)
    )
    added_names: Counter[str] = Counter(
        found_name
        for diff in tests
        for hunk in diff.hunks
        for line in hunk.added
        if (found_name := _test_name(line)) is not None
    )
    removed_marks: Counter[str] = Counter(
        _code(line) for diff in tests for hunk in diff.hunks for line in hunk.removed if _mark(line) is not None
    )
    found: list[Weakening] = []
    for diff in tests:
        for hunk in diff.hunks:
            for line in hunk.removed:
                if _assertion(line):
                    key: str = _squeezed(line)
                    if added_assertions[key] > 0:
                        added_assertions[key] -= 1
                    elif not _wrapped(line, hunk):
                        found.append(Weakening(diff.path, "assertion removed or changed", line.strip()))
                name: str | None = _test_name(line)
                if name is not None:
                    if added_names[name] > 0:
                        added_names[name] -= 1
                    else:
                        found.append(Weakening(diff.path, "test removed", line.strip()))
            # A mark in a new file, or in a hunk that adds a test of its own, guards that new test.
            guards_a_new_test: bool = diff.new or any(_test_name(line) is not None for line in hunk.added)
            for line in hunk.added:
                kind: str | None = _mark(line)
                if kind is None or guards_a_new_test:
                    continue
                if removed_marks[_code(line)] > 0:
                    removed_marks[_code(line)] -= 1
                    continue
                found.append(Weakening(diff.path, kind, line.strip()))
            before: dict[str, float] = _timeouts(hunk.removed)
            for name, after in _timeouts(hunk.added).items():
                if name in before and after > before[name]:
                    raised: str = next(line for line in hunk.added if name in line.lower())
                    found.append(
                        Weakening(diff.path, f"timeout raised from {before[name]:g} to {after:g}", raised.strip())
                    )
    return tuple(found)


def explanations(messages: tuple[str, ...]) -> tuple[str, ...]:
    """The text of every ``Test-change:`` line of the commit ``messages``."""
    return tuple(found for message in messages for found in _EXPLAINED.findall(message))


def explains(line: str, path: str) -> bool:
    """Whether the ``Test-change:`` text ``line`` names ``path`` (its path or its file name) with a reason."""
    name: str = path.rsplit("/", 1)[-1]
    rest: str = ""
    for spelled in (path, name):
        named: re.Pattern[str] = re.compile(rf"(?<![\w/.-]){re.escape(spelled)}(?![\w-]|\.\w)")
        if named.search(line):
            rest = named.sub(" ", line)
            break
    else:
        return False
    return len(re.findall(r"[^\W\d_]{2,}", rest)) >= _REASON_WORDS


def unexplained(found: tuple[Weakening, ...], said: tuple[str, ...]) -> tuple[Weakening, ...]:
    """The weakenings whose file no ``Test-change:`` line of ``said`` explains."""
    return tuple(weak for weak in found if not any(explains(line, weak.path) for line in said))


def edited_tests(files: tuple[FileDiff, ...]) -> tuple[str, ...]:
    """The test files whose existing lines the diff removes or rewrites (a test only added is not edited)."""
    return tuple(diff.path for diff in files if is_test(diff.path) and any(hunk.removed for hunk in diff.hunks))


def brief(files: tuple[FileDiff, ...], said: tuple[str, ...]) -> str:
    """The pre-push review's section on the tests the change edits, or ``""`` when it has nothing to ask."""
    found: tuple[Weakening, ...] = weakenings(files)
    edited: tuple[str, ...] = edited_tests(files)
    code: bool = any(is_code(diff.path) for diff in files)
    if not found and not (code and edited):
        return ""
    paths: list[str] = list(dict.fromkeys([*edited, *(weak.path for weak in found)]))
    lines: list[str] = [
        '## Test changes (REVIEW.md, "Test changes")',
        "",
        "This change edits the tests below"
        + (" and changes code too" if code else "")
        + ". For each file, say why the test changed and what its old assertion caught, and whether the new test "
        "still catches it. A test weakened (an assertion removed or loosened, a skip, xfail or rerun added, a timeout "
        "raised) with no reason that holds against the code is an Important finding, as is a test changed to agree "
        "with the code when the code is what is wrong.",
        "",
    ]
    for path in paths:
        mine: list[Weakening] = [weak for weak in found if weak.path == path]
        lines.append(f"- `{path}`" + ("" if mine else ": edited, no weakening seen in its lines"))
        lines.extend(f"  - {weak.kind}: `{weak.line[:160]}`" for weak in mine)
    lines.append("")
    if said:
        lines.append("The commits' `Test-change:` lines:")
        lines.extend(f"- {line}" for line in said)
    else:
        lines.append("No commit of the change has a `Test-change:` line.")
    return "\n".join(lines) + "\n"


def _git(args: list[str]) -> str:
    done: subprocess.CompletedProcess[str] = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({done.returncode}): {done.stderr.strip()[:600]}")
    return done.stdout


def _diff(base: str, head: str) -> tuple[FileDiff, ...]:
    """The diff from where ``head`` leaves ``base`` to ``head``."""
    fork: str = _git(["merge-base", base, head]).strip()
    return parse_diff(_git(["diff", "--no-color", "--no-ext-diff", "--no-renames", "-U0", fork, head]))


def _reasons(base: str, head: str) -> tuple[str, ...]:
    """The ``Test-change:`` lines of the commits ``head`` has since it left ``base``."""
    fork: str = _git(["merge-base", base, head]).strip()
    log: str = _git(["log", "--format=%B%x1e", f"{fork}..{head}"])
    return explanations(tuple(block for block in log.split(_RECORD_SEPARATOR) if block.strip()))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("command", choices=("check", "brief"))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--since", help="brief: the commit the review reads from (the tests edited since it)")
    args = parser.parse_args(argv)
    if args.command == "brief" and args.since is None:
        parser.error("brief needs --since")
    try:
        files: tuple[FileDiff, ...] = _diff(args.since if args.command == "brief" else args.base, args.head)
        said: tuple[str, ...] = _reasons(args.base, args.head)
    except RuntimeError as failed:
        print(f"weakened_tests: {failed}", file=sys.stderr)
        return 2
    if args.command == "brief":
        print(brief(files, said), end="")
        return 0
    missing: tuple[Weakening, ...] = unexplained(weakenings(files), said)
    if not missing:
        print("weakened_tests: no test weakened without a Test-change line")
        return 0
    print(
        "weakened_tests: the change weakens tests and no `Test-change:` line of its commits says why:", file=sys.stderr
    )
    for weak in missing:
        print(f"  {weak.path}: {weak.kind}: {weak.line[:160]}", file=sys.stderr)
    print(
        "Add a line to the body of the commit that makes the change, one per file, naming the file and why: what the "
        "old assertion caught and why it no longer holds, e.g.\n"
        "  Test-change: tests/unit/test_x.py: the old count assumed two helpers; item 2018 made it three, see "
        "src/dotagent/x.py\n"
        "It may stand in the body of any commit of the branch (`git commit --amend` for the last one). A test the "
        "code broke is fixed in the code, not in the test.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
