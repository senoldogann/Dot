"""The lint of a skill Dot writes: numbered steps, one objective and a check each, a table of failures and fixes, and
a fix that is only another attempt counted as missing."""

import pytest

from dotagent.cli_skills_lint import find_skill, installed_skills
from dotagent.core.errors import SkillError
from dotagent.skills.catalog import builtin_skill_names
from dotagent.skills.lint import LintProblem, lint_skill

GOOD: str = """---
name: convert-markdown
description: Convert a Markdown file to PDF.
---

# convert-markdown

## Steps

1. Render the file to HTML with the converter; check that the HTML file exists.
2. Print the HTML to PDF with headless Chrome and confirm `file` says PDF.

## When it fails

| Failure | Fix |
| --- | --- |
| Chrome is not found | Use the path under /Applications |
| The PDF is empty | Wait for the page to load before printing |
"""


def _rules(text: str) -> list[tuple[str, int]]:
    problems: tuple[LintProblem, ...] = lint_skill(text)
    return [(problem.rule, problem.step) for problem in problems]


def test_a_skill_with_checked_steps_and_a_fix_table_lacks_nothing() -> None:
    assert lint_skill(GOOD) == ()


def test_each_thing_a_skill_lacks_is_named_with_its_step() -> None:
    bare: str = "---\nname: x\ndescription: y\n---\nOpen the page and read it.\n"
    assert _rules(bare) == [("no_steps", 0), ("no_fix_table", 0)]
    chained: str = GOOD.replace(
        "1. Render the file to HTML with the converter; check that the HTML file exists.",
        "1. Render the file to HTML, then open it in Safari.",
    )
    assert _rules(chained) == [("step_with_two_objectives", 1), ("step_without_check", 1)]
    turkish: str = GOOD.replace(
        "2. Print the HTML to PDF with headless Chrome and confirm `file` says PDF.",
        "2. HTML'i PDF'e yazdır ve dosyanın PDF olduğunu doğrula.",
    )
    assert lint_skill(turkish) == ()


def test_a_fix_that_only_says_to_try_again_is_no_fix() -> None:
    retry: str = GOOD.replace("| Wait for the page to load before printing |", "| Try again. |")
    assert _rules(retry) == [("retry_is_no_fix", 0)]
    no_table: str = GOOD.split("## When it fails")[0]
    assert _rules(no_table) == [("no_fix_table", 0)]


def test_a_skill_that_comes_with_dot_is_found_as_a_run_finds_it_and_a_gone_one_is_named() -> None:
    """`dotx eval real` stopped at a case whose run read a skill outside the home folder (item 2091's review)."""
    catalog = installed_skills()
    name: str = sorted(builtin_skill_names())[0]
    assert find_skill(catalog, name).name == name
    with pytest.raises(SkillError):
        find_skill(catalog, "no-such-skill")
