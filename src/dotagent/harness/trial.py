"""A version tried on real runs half and half (item 988, docs/harness.md): which half a run is in, which ways a tool
failed are a pattern worth a proposal, what the proposer is shown of them, which candidate is never tried, and how a
trial ends. Pure functions; the store and the model are reached from :mod:`dotagent.harness.weekly`."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.harness.digest import Digest, FailureRow, signature_of
from dotagent.store.models import ToolOutcome, TrialArm, TrialRun

# The kv key of the trial under way.
TRIAL_KEY: str = "harness:trial"
# The kv key of when the last proposal was asked for.
PROPOSED_KEY: str = "harness:proposed_at"
# A proposal at most once a week.
PROPOSAL_EVERY_S: float = 7 * 86_400.0
# What makes a way a tool failed a pattern: so many runs, chats and days of the week read.
PATTERN_RUNS: int = 3
PATTERN_CHATS: int = 2
PATTERN_DAYS: int = 3
# How many patterns one proposal answers.
PATTERNS_SHOWN: int = 5
# The trial's rules: the runs each half needs for a decision, for the guard, the guard's share, the test's level and
# the days it may last.
DECIDE_RUNS: int = 40
GUARD_RUNS: int = 20
GUARD_SHARE: float = 0.75
LEVEL: float = 0.1
TRIAL_DAYS: int = 21
DAY_S: float = 86_400.0

# The closed list a signature seen only in runs that read what others can author is told by: its tool and one of these.
type FailureClass = Literal["timed out", "not found", "refused", "invalid arguments", "page did not load", "other"]
_CLASSES: tuple[tuple[FailureClass, re.Pattern[str]], ...] = (
    ("timed out", re.compile(r"timed? ?out|did not (?:end|answer|say) in time|deadline", re.IGNORECASE)),
    ("invalid arguments", re.compile(r"invalid arguments|not a valid|field required|extra inputs", re.IGNORECASE)),
    ("not found", re.compile(r"not found|no such|does not exist|missing", re.IGNORECASE)),
    ("refused", re.compile(r"refused|denied|not allowed|forbidden|blocked", re.IGNORECASE)),
    (
        "page did not load",
        re.compile(r"snapshot|navigation|opening a tab|settle|page (?:did not|failed)", re.IGNORECASE),
    ),
)
# What a candidate's text must not name: a habit that steers runs toward a tool with fewer guards or around a question.
# A word is matched at its start and with whatever follows it, so a tool's name (``terminal_send``, ``shell``) is
# caught as the word is.
_STEERING: re.Pattern[str] = re.compile(
    r"(?<![A-Za-z0-9])(?:shell|terminal|bash|zsh|osascript|apple[ _]?script|approv|permission|yolo|ask|confirm)\w*",
    re.IGNORECASE,
)

type Arm = TrialArm
type Verdict = Literal["going", "better", "worse", "unclear"]


class Trial(FrozenModel):
    """The trial under way: the candidate, when it began, the signatures it was proposed for, and the version in use
    when it began (``None``: Dot's own text alone), which a better candidate replaces only while it is still in use."""

    version_id: str
    started_at: float
    signatures: tuple[str, ...]
    in_use: str | None


class Half(FrozenModel):
    """How the runs of one half ended: ``runs`` ended, ``done`` of them done, ``failing`` with one of the trial's
    signatures failed."""

    runs: int
    done: int
    failing: int


class Judgement(FrozenModel):
    """How a trial stands, and why, in words."""

    verdict: Verdict
    why: str


def arm_of(run_id: str) -> Arm:
    """The half a run is in: its id's hash, even or odd, so that a run's half is the same wherever it is read."""
    return "candidate" if hashlib.sha256(run_id.encode()).digest()[0] % 2 == 1 else "in_use"


def class_of(signature: str) -> FailureClass:
    """The class from the closed list of a signature's words."""
    for name, pattern in _CLASSES:
        if pattern.search(signature):
            return name
    return "other"


def shown_signature(row: FailureRow) -> str:
    """A pattern as the proposer is shown it: its signature when a run that read nothing others can author failed so,
    else its tool and its class alone."""
    if row.tainted_runs < row.runs_failing:
        return row.signature
    return f"{row.tool}: {class_of(row.signature)}"


def patterns_of(digest: Digest) -> tuple[FailureRow, ...]:
    """The ways a tool failed that are a pattern (:data:`PATTERN_RUNS` runs, :data:`PATTERN_CHATS` chats,
    :data:`PATTERN_DAYS` days), the most widespread first, at most :data:`PATTERNS_SHOWN`."""
    found: list[FailureRow] = [
        row
        for row in digest.rows
        if row.runs_failing >= PATTERN_RUNS and row.chats >= PATTERN_CHATS and row.days >= PATTERN_DAYS
    ]
    return tuple(found[:PATTERNS_SHOWN])


def pattern_lines(rows: Sequence[FailureRow]) -> tuple[str, ...]:
    """The lines the proposer is shown: each pattern and its counts."""
    return tuple(
        f"{shown_signature(row)} — failed in {row.runs_failing} of {row.runs_using} runs that used the tool, in"
        f" {row.chats} chats on {row.days} days"
        for row in rows
    )


def steering_words(text: str) -> tuple[str, ...]:
    """The words of ``text`` that steer runs toward the shell or around a question, which keep a candidate out of a
    trial."""
    return tuple(sorted({match.group(0).lower() for match in _STEERING.finditer(text)}))


def _p_lower(first: Half, second: Half) -> float:
    """The one-sided p of the share of failing runs of ``first`` being lower than ``second``'s (two proportions,
    pooled)."""
    pooled: float = (first.failing + second.failing) / (first.runs + second.runs)
    spread: float = math.sqrt(pooled * (1 - pooled) * (1 / first.runs + 1 / second.runs))
    if spread == 0.0:
        return 1.0
    z: float = (second.failing / second.runs - first.failing / first.runs) / spread
    return 0.5 * math.erfc(z / math.sqrt(2))


def _done_share(half: Half) -> float:
    return half.done / half.runs


def judge(candidate: Half, in_use: Half, days: float) -> Judgement:
    """How the trial stands after ``days``: worse at once when, with :data:`GUARD_RUNS` runs a half, the candidate's
    runs end done less than :data:`GUARD_SHARE` as often; with :data:`DECIDE_RUNS` runs a half, better when its share of
    runs a signature failed in is lower with p below :data:`LEVEL`, and its runs end done not less than
    :data:`GUARD_SHARE` as often; unclear after :data:`TRIAL_DAYS` days; else going."""
    least: int = min(candidate.runs, in_use.runs)
    counts: str = (
        f"with it {candidate.failing} of {candidate.runs} runs failed so and {candidate.done} ended done; without it"
        f" {in_use.failing} of {in_use.runs} and {in_use.done}"
    )
    if least >= GUARD_RUNS and _done_share(candidate) < GUARD_SHARE * _done_share(in_use):
        return Judgement(verdict="worse", why=f"worse: its runs ended done less often ({counts})")
    if least >= DECIDE_RUNS:
        p: float = _p_lower(candidate, in_use)
        if p < LEVEL:
            return Judgement(verdict="better", why=f"better (p = {p:.3f}; {counts})")
        if days >= TRIAL_DAYS:
            return Judgement(verdict="unclear", why=f"unclear after {TRIAL_DAYS} days (p = {p:.3f}; {counts})")
        return Judgement(verdict="going", why=f"no difference yet (p = {p:.3f}; {counts})")
    if days >= TRIAL_DAYS:
        return Judgement(verdict="unclear", why=f"unclear: too few runs in {TRIAL_DAYS} days ({counts})")
    return Judgement(verdict="going", why=f"{least} of the {DECIDE_RUNS} runs a half needs ({counts})")


def _half(runs: Sequence[TrialRun], arm: Arm, failing: frozenset[str]) -> Half:
    mine: list[TrialRun] = [run for run in runs if run.arm == arm]
    return Half(
        runs=len(mine),
        done=sum(1 for run in mine if run.status == "done"),
        failing=sum(1 for run in mine if run.run_id in failing),
    )


def halves_of(runs: Sequence[TrialRun], failing: frozenset[str]) -> tuple[Half, Half]:
    """The candidate's half and the version in use's of the trial's ended ``runs``, ``failing`` the runs in which one
    of its signatures failed."""
    return _half(runs, "candidate", failing), _half(runs, "in_use", failing)


def failing_runs(outcomes: Sequence[ToolOutcome], signatures: Sequence[str]) -> frozenset[str]:
    """The runs of ``outcomes`` in which a tool failed in one of ``signatures`` (as shown to the proposer: a
    signature, or a tool and its class)."""
    wanted: frozenset[str] = frozenset(signatures)
    found: set[str] = set()
    for outcome in outcomes:
        if outcome.ok:
            continue
        signature: str = signature_of(outcome)
        if signature in wanted or f"{outcome.tool}: {class_of(signature)}" in wanted:
            found.add(outcome.run_id)
    return frozenset(found)
