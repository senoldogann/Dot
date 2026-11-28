"""The lint of a skill Dot writes (docs/harness.md, "The lint of a skill"): what makes a skill one a later run can
follow and check itself against, read from its SKILL.md with no model.

A skill passes when it has numbered steps, each step one objective with a check of its own (a word that says how the
step is seen to be done: "check", "confirm", "verify", "done when", "expect", "should show"), and a table of failures
and fixes: a Markdown table whose header names a failure (or a symptom, a problem, an error) and a fix. A fix that only
says to try again is no fix: it is counted as missing. A step that chains two objectives ("then" between two clauses) is
counted as more than one. The words are English and Turkish, the languages Dot writes skills in.

The lint never changes a skill: it says what is missing, and the caller records it.
"""

import re
from typing import Literal

from dotagent.core.types import FrozenModel

type LintRule = Literal["no_steps", "step_without_check", "step_with_two_objectives", "no_fix_table", "retry_is_no_fix"]

_STEP: re.Pattern[str] = re.compile(r"^\s{0,3}(\d{1,3})[.)]\s+(.+)$")
# Words that say how a step is seen to be done.
_CHECK: re.Pattern[str] = re.compile(
    r"\b(check|checks|checking|confirm|confirms|confirming|verify|verifies|verifying|done when|expect|expects|"
    r"should show|should read|must show|showing|until)\b|kontrol|doğrula|teyit|emin ol|beklenen",
    re.IGNORECASE,
)
# What chains a second objective onto a step: "then" between two clauses ("ardından", "sonra" in Turkish).
_CHAIN: re.Pattern[str] = re.compile(
    r"(,?\s+and then\s+|,\s+then\s+|\.\s+then\s+|,?\s+ardından\s+|,\s+sonra\s+|\.\s+sonra\s+)", re.IGNORECASE
)
_ROW: re.Pattern[str] = re.compile(r"^\s*\|(.+)\|\s*$")
_RULE_ROW: re.Pattern[str] = re.compile(r"^[\s|:-]+$")
_FAILURE_HEAD: re.Pattern[str] = re.compile(r"\b(failure|fails|symptom|problem|error|if)\b", re.IGNORECASE)
_FIX_HEAD: re.Pattern[str] = re.compile(r"\b(fix|remedy|do|action)\b", re.IGNORECASE)
# A fix that is only another attempt.
_RETRY_ONLY: re.Pattern[str] = re.compile(
    r"^\s*(try( it)? again|retry( it)?|run it again|do it again|repeat( the step)?|start over)\s*[.!]?\s*$",
    re.IGNORECASE,
)


class LintProblem(FrozenModel):
    """One thing a skill lacks: the ``rule`` it breaks, the ``step`` (1-based, 0 for the whole skill) and the words a
    person reads."""

    rule: LintRule
    step: int
    said: str


def _body(text: str) -> str:
    """``text`` with the frontmatter left out: its description is not a step."""
    if text.startswith("---\n"):
        end: int = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :]
    return text


def steps_of(text: str) -> tuple[str, ...]:
    """The numbered steps of a SKILL.md, in order; a step's indented lines under it belong to it."""
    steps: list[str] = []
    for line in _body(text).splitlines():
        found: re.Match[str] | None = _STEP.match(line)
        if found is not None:
            steps.append(found.group(2).strip())
        elif steps and line.startswith(("   ", "\t")) and line.strip():
            steps[-1] = f"{steps[-1]} {line.strip()}"
    return tuple(steps)


def _cells(line: str) -> tuple[str, ...]:
    found: re.Match[str] | None = _ROW.match(line)
    if found is None:
        return ()
    return tuple(cell.strip() for cell in found.group(1).split("|"))


def fix_rows(text: str) -> tuple[tuple[str, str], ...] | None:
    """The (failure, fix) rows of the first table whose header names a failure and a fix; ``None`` when there is no
    such table."""
    lines: list[str] = _body(text).splitlines()
    for index, line in enumerate(lines):
        head: tuple[str, ...] = _cells(line)
        if len(head) < 2:
            continue
        failure: int | None = next((n for n, cell in enumerate(head) if _FAILURE_HEAD.search(cell)), None)
        fix: int | None = next((n for n, cell in enumerate(head) if n != failure and _FIX_HEAD.search(cell)), None)
        if failure is None or fix is None:
            continue
        rows: list[tuple[str, str]] = []
        for later in lines[index + 1 :]:
            if _RULE_ROW.match(later) and "-" in later:
                continue
            cells: tuple[str, ...] = _cells(later)
            if not cells:
                break
            if len(cells) > max(failure, fix):
                rows.append((cells[failure], cells[fix]))
        return tuple(rows)
    return None


def lint_skill(text: str) -> tuple[LintProblem, ...]:
    """What the SKILL.md ``text`` lacks to be a skill a later run can follow and check; empty when it lacks nothing."""
    problems: list[LintProblem] = []
    steps: tuple[str, ...] = steps_of(text)
    if not steps:
        problems.append(LintProblem(rule="no_steps", step=0, said="it has no numbered steps"))
    for number, step in enumerate(steps, start=1):
        if _CHAIN.search(step):
            problems.append(
                LintProblem(
                    rule="step_with_two_objectives", step=number, said=f"step {number} does more than one thing"
                )
            )
        if not _CHECK.search(step):
            problems.append(
                LintProblem(rule="step_without_check", step=number, said=f"step {number} says no check of its own")
            )
    rows: tuple[tuple[str, str], ...] | None = fix_rows(text)
    if rows is None or not rows:
        problems.append(LintProblem(rule="no_fix_table", step=0, said="it has no table of failures and their fixes"))
    else:
        for failure, fix in rows:
            if not fix.strip() or _RETRY_ONLY.match(fix):
                problems.append(
                    LintProblem(
                        rule="retry_is_no_fix", step=0, said=f"the fix of '{failure[:60]}' is only to try again"
                    )
                )
    return tuple(problems)
