"""The checks REVIEW.md names are real tests, so a finding that comes back cannot lose its guard.

The review guide is prose, and a prose rule is only a wish. The section "Checks made from reviews" names the test that
keeps each repeated finding fixed; this test fails when one of those names stops existing, so deleting a guard is a
red gate rather than a quiet regression (item 798).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "REVIEW.md"
SECTION = "## Checks made from reviews"
ENTRY = re.compile(r"^- `(?P<path>tests/[^`]+?)::(?P<name>test_[A-Za-z0-9_]+)` — (?P<rule>.+)$")
MINIMUM = 3


def _section() -> list[str]:
    text = REVIEW.read_text(encoding="utf-8")
    start = text.find(SECTION)
    assert start >= 0, f"REVIEW.md has no {SECTION!r} section"
    body = text[start + len(SECTION) :]
    return body.split("\n## ", 1)[0].splitlines()


def _entries() -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for line in _section():
        match = ENTRY.match(line)
        if match is not None:
            entries.append((match["path"], match["name"], match["rule"].strip()))
        elif entries and line.startswith("  ") and line.strip():
            path, name, rule = entries[-1]
            entries[-1] = (path, name, f"{rule} {line.strip()}")
    return entries


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_review_check_names_a_test_that_exists() -> None:
    entries = _entries()
    assert len(entries) >= MINIMUM, f"name at least {MINIMUM} review checks in REVIEW.md"
    for path_text, name, rule in entries:
        path = ROOT / path_text
        assert path.is_file(), f"{path_text} does not exist"
        assert name in _functions(path), f"{path_text} has no test named {name}"
        assert len(rule) >= 20, f"the check for {name} does not say what finding it prevents"


def test_the_review_check_section_has_no_unparsed_bullet() -> None:
    bullets = [line for line in _section() if line.startswith("- ")]
    assert len(bullets) == len(_entries()), "every bullet in the review checks section must name a test"


def test_no_review_check_is_named_twice() -> None:
    keys = [(path, name) for path, name, _ in _entries()]
    assert len(keys) == len(set(keys)), "a review check is named more than once"
