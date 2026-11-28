"""The suite of real runs (docs/development.md, "Evals from real runs"): what the owner asked Dot, graded on the way a
run went (the tools in their order, the skills it read) and on how it ended, with no model in the grading.

A suite mixes three kinds of case, by share of its size: about 5 % *golden* cases, tasks of the catalog that play on
files and sites of their own (the fixed cases whose score must not move), about 80 % *real* cases, the goals of the
owner's own runs read from Dot's record, and about 15 % *owner* cases, goals the owner wrote in ``<home>/evals/real/
owner.json``. A kind that has fewer cases than its share gives what it has: the suite is shorter, and says so.

The real cases put first the runs that went wrong: those that failed or were cancelled, then those the owner marked
wrong or asked again (a reaction), then the rest, newest first in each. A run that ended done and was not marked sets
the order of its acting tools as the case's trajectory; one that went wrong sets none, since its way was the wrong one,
and is graded on its ending and the skills its run read. Only the owner's own words are taken: the runs started from
the chat, the command line, a schedule or a resume of one, never a delegated run, a reply to someone else, a code
review (its goal is a diff of a repository the attempt does not have) or an evaluation's.
"""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from dotagent.core.errors import ConfigError, StoreError
from dotagent.core.types import FrozenModel
from dotagent.evals.results import TaskOutcome
from dotagent.evals.types import EvalTask, SkillRead, SkillSeed
from dotagent.skills.types import SKILL_TOOL
from dotagent.store.models import RunMode

type CaseOrigin = Literal["golden", "real", "owner"]
type CaseReason = Literal["failed", "cancelled", "corrected", "done", "golden", "owner"]

REAL_PREFIX: str = "real-"
OWNER_PREFIX: str = "owner-"
# The shares of a suite's size: golden, owner-written; the real runs take the rest.
GOLDEN_SHARE: float = 0.05
OWNER_SHARE: float = 0.15
# The catalog's tasks a suite takes as its golden cases: one of each kind of work that plays on fixtures of its own.
GOLDEN_TASKS: tuple[str, ...] = (
    "find-first-error",
    "fix-add-bug",
    "recover-edit-mismatch",
    "research-service-owner",
    "injection-in-notes",
    "memo-in-house-style",
    "changelog-skill-among-decoys",
    "newsletter-form-verified",
)
# The sources of runs whose goal is the owner's own words.
OWNER_SOURCES: tuple[str, ...] = ("ui", "cli", "schedule", "resume")
# The owner's reactions that say a run's answer was not what they wanted.
CORRECTING: tuple[str, ...] = ("wrong", "again")
# The tools that keep the run's own books, not its work: left out of a trajectory.
BOOKKEEPING: frozenset[str] = frozenset(
    {
        "finish",
        "mark_plan_step",
        "update_plan",
        "use_tools",
        "memory_search",
        "memory_save",
        "memory_forget",
        "memory_update",
        "write_notes",
        SKILL_TOOL,
    }
)
# The most tools a trajectory keeps, the first ones.
TRAJECTORY_MOST: int = 12
# The share of a case's trajectory an attempt must follow, in order, to pass.
TRAJECTORY_PASS: float = 0.5
# What an attempt at a real or owner case may spend.
REAL_TIME_LIMIT_S: float = 600.0
REAL_TOKEN_BUDGET: int = 600_000
# Above this pass rate a suite says little of a change (Uber's 70-90 % band).
SAYS_LITTLE_ABOVE: float = 0.9
_BUSY_TIMEOUT_S: float = 5.0
_RUNS_SQL: str = """
SELECT r.id, r.goal, r.mode, r.status, r.created_at,
       EXISTS (SELECT 1 FROM reactions AS x WHERE x.run_id = r.id AND x.removed_at IS NULL AND x.reaction IN ({react}))
FROM runs AS r
WHERE r.source IN ({sources}) AND r.parent_run_id IS NULL AND r.created_at >= ?
  AND r.status IN ('done', 'failed', 'cancelled') AND TRIM(r.goal) != ''
ORDER BY r.created_at DESC
"""
_CALLS_SQL: str = """
SELECT run_id, json_extract(data, '$.tool'), json_extract(data, '$.args.name') FROM events
WHERE kind = 'tool_call' AND run_id IN (SELECT value FROM json_each(?)) ORDER BY run_id, id
"""


class RealCase(FrozenModel):
    """One case of a suite: ``id`` (a catalog task's for a golden case), where it comes from, the ``goal`` and ``mode``
    it is played with, the run it was read from (``None`` for a golden or owner case), why it is in the suite, the
    ``tools`` an attempt should call in that order (empty: graded on its ending alone) and the ``skills`` it should
    read."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    origin: CaseOrigin
    goal: str
    mode: RunMode
    run_id: str | None
    reason: CaseReason
    tools: tuple[str, ...]
    skills: tuple[str, ...]


class OwnerCase(FrozenModel):
    """A case the owner wrote in ``owner.json``: a goal, its mode, and the tools and skills a good run uses."""

    goal: str = Field(min_length=1)
    mode: RunMode
    tools: tuple[str, ...]
    skills: tuple[str, ...]


class Suite(FrozenModel):
    """A suite as it was built at ``built_at``, and what was asked for: ``size`` cases."""

    built_at: float
    size: int
    cases: tuple[RealCase, ...]


class RealGrade(FrozenModel):
    """One attempt at one case: whether it ended as asked (done, and every skill read), the share of the case's
    trajectory it followed in order (1.0 for a case with none), the tools of it that it left out, and whether it
    passed: both."""

    case_id: str
    origin: CaseOrigin
    attempt: int
    ended_well: bool
    order: float
    missing: tuple[str, ...]
    passed: bool
    tokens: int


class _RunRow(FrozenModel):
    run_id: str
    goal: str
    mode: RunMode
    status: Literal["done", "failed", "cancelled"]
    created_at: float
    corrected: bool


_OWNER_CASES: TypeAdapter[tuple[OwnerCase, ...]] = TypeAdapter(tuple[OwnerCase, ...])


def trajectory_of(tools: Sequence[str]) -> tuple[str, ...]:
    """The acting tools of ``tools`` (called in that order), a tool called again at once counted once, at most
    :data:`TRAJECTORY_MOST`."""
    kept: list[str] = []
    for tool in tools:
        if tool in BOOKKEEPING or (kept and kept[-1] == tool):
            continue
        kept.append(tool)
    return tuple(kept[:TRAJECTORY_MOST])


def order_followed(expected: Sequence[str], actual: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    """The share of ``expected`` that ``actual`` calls in the same order (their longest common subsequence), and the
    tools of ``expected`` it never calls. A case with no trajectory is followed whole."""
    if not expected:
        return 1.0, ()
    table: list[list[int]] = [[0] * (len(actual) + 1) for _ in range(len(expected) + 1)]
    for i, want in enumerate(expected, start=1):
        for j, got in enumerate(actual, start=1):
            table[i][j] = table[i - 1][j - 1] + 1 if want == got else max(table[i - 1][j], table[i][j - 1])
    called: set[str] = set(actual)
    missing: tuple[str, ...] = tuple(dict.fromkeys(tool for tool in expected if tool not in called))
    return table[len(expected)][len(actual)] / len(expected), missing


def _reason(row: _RunRow) -> CaseReason:
    if row.status != "done":
        return row.status
    return "corrected" if row.corrected else "done"


_RANK: dict[CaseReason, int] = {"failed": 0, "cancelled": 0, "corrected": 1, "done": 2, "golden": 3, "owner": 3}


def real_case(row: _RunRow, tools: Sequence[str], skills: Sequence[str]) -> RealCase:
    """The case of one owner's run: its trajectory only when it ended done and was not marked wrong."""
    reason: CaseReason = _reason(row)
    return RealCase(
        id=f"{REAL_PREFIX}{row.run_id.removeprefix('run_').replace('_', '-').lower()}"[:40],
        origin="real",
        goal=row.goal,
        mode=row.mode,
        run_id=row.run_id,
        reason=reason,
        tools=trajectory_of(tools) if reason == "done" else (),
        skills=tuple(dict.fromkeys(skills)),
    )


def ranked(cases: Sequence[RealCase]) -> tuple[RealCase, ...]:
    """``cases`` (newest first) with those that went wrong first: failed or cancelled, then corrected, then done."""
    return tuple(sorted(cases, key=lambda case: _RANK[case.reason]))


def owner_cases(written: Sequence[OwnerCase]) -> tuple[RealCase, ...]:
    return tuple(
        RealCase(
            id=f"{OWNER_PREFIX}{number}",
            origin="owner",
            goal=case.goal,
            mode=case.mode,
            run_id=None,
            reason="owner",
            tools=trajectory_of(case.tools),
            skills=case.skills,
        )
        for number, case in enumerate(written, start=1)
    )


def golden_cases(tasks: Sequence[EvalTask]) -> tuple[RealCase, ...]:
    return tuple(
        RealCase(
            id=task.id,
            origin="golden",
            goal=task.goal,
            mode=task.mode,
            run_id=None,
            reason="golden",
            tools=(),
            skills=(),
        )
        for task in tasks
    )


def mix_suite(
    golden: Sequence[RealCase], real: Sequence[RealCase], owner: Sequence[RealCase], size: int, built_at: float
) -> Suite:
    """A suite of at most ``size`` cases: :data:`GOLDEN_SHARE` golden (one at least), :data:`OWNER_SHARE` the owner's
    and the rest real, each kind in its order. A kind short of its share gives what it has; no other kind fills in."""
    golden_count: int = max(1, round(size * GOLDEN_SHARE)) if size > 0 else 0
    owner_count: int = round(size * OWNER_SHARE)
    real_count: int = max(0, size - golden_count - owner_count)
    return Suite(
        built_at=built_at,
        size=size,
        cases=(*golden[:golden_count], *real[:real_count], *owner[:owner_count]),
    )


def read_real_cases(database: Path, since: float) -> tuple[RealCase, ...]:
    """The cases of the owner's runs started since ``since``, read from ``database`` opened read-only, ranked
    (:func:`ranked`). Blocking."""
    sources: str = ", ".join("?" for _ in OWNER_SOURCES)
    react: str = ", ".join("?" for _ in CORRECTING)
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S)) as connection:
            runs: list[tuple[str, str, str, str, float, int]] = connection.execute(
                _RUNS_SQL.format(sources=sources, react=react), (*CORRECTING, *OWNER_SOURCES, since)
            ).fetchall()
            ids: str = json.dumps([run[0] for run in runs])
            calls: list[tuple[str, str | None, str | None]] = connection.execute(_CALLS_SQL, (ids,)).fetchall()
    except sqlite3.Error as error:
        raise StoreError(
            "the owner's runs could not be read", {"database": str(database), "error": str(error)}
        ) from error
    tools: dict[str, list[str]] = {}
    skills: dict[str, list[str]] = {}
    for run_id, tool, skill in calls:
        if tool is None:
            continue
        tools.setdefault(run_id, []).append(tool)
        if tool == SKILL_TOOL and skill:
            skills.setdefault(run_id, []).append(skill)
    rows: list[_RunRow] = [
        _RunRow(run_id=run_id, goal=goal, mode=mode, status=status, created_at=created, corrected=bool(corrected))
        for run_id, goal, mode, status, created, corrected in runs
    ]
    return ranked([real_case(row, tools.get(row.run_id, []), skills.get(row.run_id, [])) for row in rows])


def read_owner_cases(path: Path) -> tuple[OwnerCase, ...]:
    """The cases the owner wrote in ``path`` (a JSON list); none when the file is not there. Blocking."""
    if not path.exists():
        return ()
    try:
        return _OWNER_CASES.validate_json(path.read_bytes())
    except ValidationError as error:
        raise ConfigError(
            "the owner's eval cases are not a list of {goal, mode, tools, skills}",
            {"path": str(path), "errors": error.error_count(), "first": str(error.errors()[0]["msg"])},
        ) from error


def task_of(case: RealCase, skills: Sequence[SkillSeed]) -> EvalTask:
    """The eval task an attempt at a real or owner case plays: its goal in its mode, in an empty workspace, the
    ``skills`` it should read seeded in the attempt's home and checked to be read, to end done."""
    return EvalTask(
        id=case.id,
        category="multi_step",
        goal=case.goal,
        mode=case.mode,
        files=(),
        checks=tuple(SkillRead(kind="skill_read", name=name) for name in case.skills),
        injection_checks=(),
        ending="done",
        reference=(),
        time_limit_s=REAL_TIME_LIMIT_S,
        token_budget=REAL_TOKEN_BUDGET,
        site=(),
        goal_mode=False,
        skills=tuple(skills),
        servers=(),
        easy_way=None,
        grouped=False,
    )


def grade(case: RealCase, outcome: TaskOutcome) -> RealGrade:
    """An attempt at ``case``: it ended well when its outcome passed (done, every check held), and passed when it also
    followed :data:`TRAJECTORY_PASS` of the case's trajectory in order."""
    actual: tuple[str, ...] = trajectory_of([step.name for step in outcome.trace if step.kind == "tool"])
    order, missing = order_followed(case.tools, actual)
    return RealGrade(
        case_id=case.id,
        origin=case.origin,
        attempt=outcome.attempt,
        ended_well=outcome.passed,
        order=order,
        missing=missing,
        passed=outcome.passed and order >= TRAJECTORY_PASS,
        tokens=outcome.metrics.input_tokens + outcome.metrics.output_tokens,
    )


def render_suite(suite: Suite) -> list[str]:
    """What a suite holds: its cases by kind and by why they are in it."""
    by_origin: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for case in suite.cases:
        by_origin[case.origin] = by_origin.get(case.origin, 0) + 1
        by_reason[case.reason] = by_reason.get(case.reason, 0) + 1
    total: int = len(suite.cases)
    lines: list[str] = [f"{total} cases of the {suite.size} asked for:"]
    for origin in ("golden", "real", "owner"):
        count: int = by_origin.get(origin, 0)
        lines.append(f"  {origin:<7} {count:>3}  ({count / total:.0%})" if total else f"  {origin:<7}   0")
    reasons: str = ", ".join(f"{reason} {count}" for reason, count in sorted(by_reason.items()))
    lines.append(f"  why: {reasons}" if reasons else "  why: -")
    if total < suite.size:
        lines.append("  Fewer cases than asked: a kind short of its share gives what it has.")
    return lines


def render_grades(grades: Sequence[RealGrade]) -> list[str]:
    """The pass rate of each kind and of the whole suite, how many ended well and how much of the order was followed,
    and a word when the rate is too high to show a change."""
    lines: list[str] = []
    for origin in ("golden", "real", "owner"):
        mine: list[RealGrade] = [grade for grade in grades if grade.origin == origin]
        if not mine:
            continue
        passed: int = sum(1 for grade in mine if grade.passed)
        ended: int = sum(1 for grade in mine if grade.ended_well)
        order: float = sum(grade.order for grade in mine) / len(mine)
        lines.append(
            f"  {origin:<7} passed {passed}/{len(mine)}  ended well {ended}/{len(mine)}  order followed {order:.0%}"
        )
    if not grades:
        return ["No attempt was graded."]
    rate: float = sum(1 for grade in grades if grade.passed) / len(grades)
    tokens: int = sum(grade.tokens for grade in grades)
    lines.append(f"Passed {rate:.0%} of {len(grades)} attempts; {tokens:,} tokens.")
    if rate > SAYS_LITTLE_ABOVE:
        lines.append(f"Above {SAYS_LITTLE_ABOVE:.0%} a suite says little of a change: add harder cases to owner.json.")
    return lines
