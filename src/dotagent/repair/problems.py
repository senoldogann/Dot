"""The book of Dot's own problems (item 920): what Dot's record shows going wrong again and again, each problem once,
counted, with a few lines of evidence, for the repair night (:mod:`dotagent.repair.night`) to hand to Claude Code.

A problem is seen in four places, each read since the last look:

- a tool that failed with the same error in several runs (a ``tool_result`` that is not ``ok``), counted once a run;
- the progress watchdog's alerts (``stall``), by their pattern and detail;
- a step of the improvement loop that failed;
- an error of the daemon's own log (``dotx.log``, a line of level ``error`` or ``critical``), by its event.

A run that ended *failed* is not one: its result is the model's prose, which says why in words of its own each time,
and on the owner's Mac (2026-11-02) all four of a week were a step the owner had not approved. Nor is an error that is
the world's or the owner's say rather than a defect (:data:`_NOT_DEFECTS`): an approval refused or expired, a tool in
use by another run, a page that is not there. What is left can still be the world's; the brief lets the session say so,
and the problem is then let go.

The same problem is told by its fingerprint: its kind and its words with the ids, the numbers, the quoted words, the
paths and the addresses taken out (:func:`normalized`), so that two runs' failures of one defect are one problem. A
problem fixed that is seen again is opened again: the fix did not hold."""

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, HarnessStep

type ProblemKind = Literal["tool_error", "stall", "harness", "daemon"]
type ProblemStatus = Literal["open", "working", "fixed", "waiting", "declined"]

# The key of the kv table that holds the book, a :class:`Book` as JSON.
BOOK_KEY: str = "repair.book"
# The most problems the book keeps: the oldest of those no longer open are let go first.
BOOK_KEPT: int = 200
# The lines of evidence a problem keeps, the newest.
EVIDENCE_KEPT: int = 5
# The longest a problem's words and a line of its evidence are.
TITLE_CHARS: int = 160
EVIDENCE_CHARS: int = 400
# The events of a run read for its tool errors and its alerts, the newest.
EVENTS_READ: int = 2000
# The steps of the improvement loop looked at, the newest.
STEPS_READ: int = 20
# The start of an error that is the world's or the owner's say, not a defect of Dot's.
_NOT_DEFECTS: tuple[str, ...] = (
    "Not allowed:",
    "Nothing was done: ",
    "Nothing was written: ",
    "Stopped:",
    "Cancelled",
)
# Words of an error that says the run's own input failed or the world said no, which a tool is right to tell: a command
# or a script the model wrote that failed, a text that did not appear on a page, an edit whose text was not there.
_NOT_DEFECT_WORDS: tuple[str, ...] = (
    "got HTTP 4",
    "The page is not there",
    "The script threw an error",
    "The script gave no value",
    "The text did not appear",
    "The text has not appeared",
    "the text to replace was not found",
    "syntax error",
)
# The tools whose failure with an exit code or a time limit is the command's own, which the model wrote.
_COMMAND_TOOLS: frozenset[str] = frozenset({"shell", "terminal_open", "terminal_send"})
_COMMAND_ENDS: tuple[str, ...] = ("[exit ", "[timed out")
# What is taken out of an error's words before it is a problem's: the parts that differ from one sighting to the next.
_CUT_AT: tuple[str, ...] = (" | context=", "<web_content", "\n")
_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"(?:~|/)(?:[\w.@+-]+/)+[\w.@+-]*"), "<path>"),
    (re.compile(r"\b(?:run|thr|call|sch|ext|ord|hst|hrn|evt|apr|tsk|agt|obj)_[A-Za-z0-9_]+"), "<id>"),
    (re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"“[^”]*”|\"[^\"]*\"|'[^']*'"), "<quoted>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
    (re.compile(r"\s+"), " "),
)


class Sighting(FrozenModel):
    """A problem seen once: its kind, its words as they were, a line of evidence and when."""

    kind: ProblemKind
    words: str
    evidence: str
    at: float


class Problem(FrozenModel):
    """A problem of the book: its fingerprint (``id``), kind and words, how often it was seen and its newest lines of
    evidence, its status, the sessions spent on it, the pull request of its fix and a note of how it stands."""

    id: str
    kind: ProblemKind
    title: str
    evidence: tuple[str, ...]
    count: int
    first_at: float
    last_at: float
    status: ProblemStatus
    tries: int
    pr_url: str
    note: str


class Book(FrozenModel):
    """The problems, and when the record was last read for them (``None``: never)."""

    problems: tuple[Problem, ...]
    looked_at: float | None


EMPTY_BOOK: Book = Book(problems=(), looked_at=None)


def normalized(words: str) -> str:
    """``words`` with what differs between two sightings of one problem taken out (ids, numbers, quoted words, paths,
    addresses), cut at its first line and to :data:`TITLE_CHARS`."""
    text: str = words
    for cut in _CUT_AT:
        text = text.split(cut, 1)[0]
    for pattern, put in _SCRUBS:
        text = pattern.sub(put, text)
    return text.strip()[:TITLE_CHARS]


def fingerprint(kind: ProblemKind, title: str) -> str:
    """The id of the problem of ``kind`` with ``title``: the same for every sighting of it."""
    return "prb_" + hashlib.sha256(f"{kind}\n{title}".encode()).hexdigest()[:12]


def is_defect(words: str) -> bool:
    """Whether an error's ``words`` may be a defect of Dot's, and not the world's or the owner's say."""
    head: str = words.lstrip()
    return not head.startswith(_NOT_DEFECTS) and not any(word in head for word in _NOT_DEFECT_WORDS)


def _evidence(sighting: Sighting) -> str:
    return " ".join(sighting.evidence.split())[:EVIDENCE_CHARS]


def with_sightings(book: Book, sightings: Sequence[Sighting], now: float) -> Book:
    """``book`` with ``sightings`` counted in, read at ``now``. A problem fixed or let go that is seen after it was is
    open again, with a note that says so; one being worked on or waiting on the owner keeps its status."""
    found: dict[str, Problem] = {problem.id: problem for problem in book.problems}
    for sighting in sorted(sightings, key=lambda seen: seen.at):
        title: str = normalized(sighting.words)
        if not title:
            continue
        key: str = fingerprint(sighting.kind, title)
        line: str = _evidence(sighting)
        before: Problem | None = found.get(key)
        if before is None:
            found[key] = Problem(
                id=key,
                kind=sighting.kind,
                title=title,
                evidence=(line,),
                count=1,
                first_at=sighting.at,
                last_at=sighting.at,
                status="open",
                tries=0,
                pr_url="",
                note="",
            )
            continue
        back: bool = before.status in ("fixed", "declined") and sighting.at > before.last_at
        found[key] = before.model_copy(
            update={
                "evidence": tuple(dict.fromkeys((*before.evidence, line)))[-EVIDENCE_KEPT:],
                "count": before.count + 1,
                "last_at": max(before.last_at, sighting.at),
                "status": "open" if back else before.status,
                "note": f"Seen again after it was {before.status}." if back else before.note,
            }
        )
    problems: list[Problem] = sorted(found.values(), key=lambda problem: problem.last_at, reverse=True)
    if len(problems) > BOOK_KEPT:
        live: list[Problem] = [problem for problem in problems if problem.status in ("open", "working", "waiting")]
        rest: list[Problem] = [problem for problem in problems if problem.status not in ("open", "working", "waiting")]
        problems = (live + rest)[:BOOK_KEPT]
    return Book(problems=tuple(problems), looked_at=now)


def next_problem(book: Book, least_seen: int, most_tries: int) -> Problem | None:
    """The problem to work on next: an open one seen at least ``least_seen`` times and tried fewer than ``most_tries``
    times, the most seen first and of those the newest."""
    ready: list[Problem] = [
        problem
        for problem in book.problems
        if problem.status == "open" and problem.count >= least_seen and problem.tries < most_tries
    ]
    return max(ready, key=lambda problem: (problem.count, problem.last_at), default=None)


def with_problem(book: Book, problem: Problem) -> Book:
    """``book`` with ``problem`` in the place of the one with its id."""
    return book.model_copy(
        update={"problems": tuple(problem if kept.id == problem.id else kept for kept in book.problems)}
    )


def read_book(raw: str | None) -> Book:
    """The book kept as ``raw`` (``None``: none yet)."""
    return EMPTY_BOOK if raw is None else Book.model_validate_json(raw)


def run_sightings(run_id: str, events: Iterable[EventRecord]) -> list[Sighting]:
    """What the ``events`` of the run ``run_id`` show: each tool error once a run (by its tool and its words) and each
    alert."""
    sightings: dict[str, Sighting] = {}
    for event in events:
        if event.kind == "tool_result" and event.data.get("ok") is False:
            tool: object = event.data.get("tool")
            text: object = event.data.get("text")
            if not isinstance(tool, str) or not isinstance(text, str) or not is_defect(text):
                continue
            if tool in _COMMAND_TOOLS and text.lstrip().startswith(_COMMAND_ENDS):
                continue
            words: str = f"{tool}: {text}"
            sightings.setdefault(
                fingerprint("tool_error", normalized(words)),
                Sighting(kind="tool_error", words=words, evidence=f"{run_id}: {words}", at=event.ts),
            )
        elif event.kind == "stall":
            reason: object = event.data.get("reason")
            detail: object = event.data.get("detail")
            said: str = f"{reason if isinstance(reason, str) else 'stall'}: {detail if isinstance(detail, str) else ''}"
            sightings.setdefault(
                fingerprint("stall", normalized(said)),
                Sighting(kind="stall", words=said, evidence=f"{run_id}: {said}", at=event.ts),
            )
    return list(sightings.values())


def step_sightings(steps: Iterable[HarnessStep], since: float) -> list[Sighting]:
    """The steps of the improvement loop that failed at or after ``since``."""
    return [
        Sighting(kind="harness", words=step.error, evidence=f"{step.id}: {step.error}", at=step.started_at)
        for step in steps
        if step.state == "failed" and step.error and step.started_at >= since
    ]


def log_sightings(lines: Iterable[str], since: float) -> list[Sighting]:
    """The errors of the daemon's log (JSON lines) dated at or after ``since``: by their event and their error."""
    sightings: list[Sighting] = []
    for line in lines:
        try:
            entry: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("level") not in ("error", "critical"):
            continue
        stamp: object = entry.get("timestamp")
        if not isinstance(stamp, str):
            continue
        try:
            at: float = datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            continue
        if at < since:
            continue
        event: object = entry.get("event")
        error: object = entry.get("error") or entry.get("exception") or ""
        words: str = f"{event}: {error}" if error else str(event)
        if is_defect(str(error)):
            sightings.append(Sighting(kind="daemon", words=words, evidence=words, at=at))
    return sightings


async def sightings_since(store: Store, since: float, log_file: Path) -> list[Sighting]:
    """Every sighting of Dot's record since ``since``: the runs that finished since, the improvement loop's steps and
    the daemon's log at ``log_file`` (none when there is no such file)."""
    seen: list[Sighting] = []
    for run in await store.runs_finished_since(since):
        seen.extend(run_sightings(run.id, await store.events_of_kinds(run.id, ("tool_result", "stall"), EVENTS_READ)))
    seen.extend(step_sightings(await store.list_harness_steps(STEPS_READ), since))
    seen.extend(await asyncio.to_thread(_log_file_sightings, log_file, since))
    return seen


def _log_file_sightings(log_file: Path, since: float) -> list[Sighting]:
    """The errors of the daemon's log at ``log_file`` and of the one rotated before it (``.1``), since ``since``."""
    seen: list[Sighting] = []
    for path in (log_file.with_name(f"{log_file.name}.1"), log_file):
        with contextlib.suppress(FileNotFoundError), path.open(encoding="utf-8", errors="replace") as lines:
            seen.extend(log_sightings(lines, since))
    return seen
