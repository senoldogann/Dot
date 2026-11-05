"""The night's digest of real runs (item 987, docs/harness.md): what the owner's runs of the last day did with their
tools, read from Dot's record with SQL alone and no model, kept as the night's step of the harness.

It replaces the nightly step that played synthetic tasks (item 391): Dot passed almost all of them, so no text could do
better on them, while the runs that went wrong were left out as soon as they had read a page. The digest counts, for
each way a tool failed (its *signature*: the tool and the first sentence of what it said, with ids, numbers, quoted
words, paths and addresses taken out, :func:`dotagent.repair.problems.first_sentence`), how many runs it failed in, how
many runs used the tool, in how many chats and on how many days, from which sources and on which sites; and how the runs
that were handed a lesson ended beside those that were not. A run that read what others can author is counted like any
other: the signature keeps Dot's own words, not the page's."""

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, tzinfo

from dotagent.core.types import FrozenModel
from dotagent.repair.problems import first_sentence
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, RunTally, ToolOutcome

DAY_S: float = 86_400.0
# The runs a digest leaves out: the evaluation's, which play tasks, not the owner's work.
SOURCES_OUT: tuple[str, ...] = ("eval",)
# How many signatures a digest keeps, the most widespread first, and how many sites and sources of each.
ROWS_KEPT: int = 20
SITES_KEPT: int = 5
# How many signatures the night's line names.
ROWS_SAID: int = 3


class FailureRow(FrozenModel):
    """One way a tool failed in the span: ``failures`` results in ``runs_failing`` runs of the ``runs_using`` that
    called the tool, in ``chats`` chats on ``days`` days (of the schedule's time zone), from ``sources``, on ``sites``
    (at most :data:`SITES_KEPT`), ``tainted_runs`` of the runs having read what others can author."""

    signature: str
    tool: str
    failures: int
    runs_failing: int
    runs_using: int
    chats: int
    days: int
    sources: tuple[str, ...]
    sites: tuple[str, ...]
    tainted_runs: int


class Digest(FrozenModel):
    """A digest of the runs of ``since`` up to ``until``: how they ended (``tally``), how many called a tool, and the
    ways their tools failed, the most widespread first (at most :data:`ROWS_KEPT`)."""

    since: float
    until: float
    tally: RunTally
    runs_with_tools: int
    rows: tuple[FailureRow, ...]


def signature_of(outcome: ToolOutcome) -> str:
    """The way ``outcome``'s tool failed: the tool and the first sentence of what it said."""
    said: str = first_sentence(outcome.error)
    return f"{outcome.tool}: {said}" if said else f"{outcome.tool}: failed without a word"


def _row(signature: str, failed: Sequence[ToolOutcome], using: int, zone: tzinfo) -> FailureRow:
    sites: list[str] = []
    for outcome in failed:
        if outcome.site and outcome.site not in sites:
            sites.append(outcome.site)
    return FailureRow(
        signature=signature,
        tool=failed[0].tool,
        failures=len(failed),
        runs_failing=len({outcome.run_id for outcome in failed}),
        runs_using=using,
        chats=len({outcome.thread_id or outcome.run_id for outcome in failed}),
        days=len({datetime.fromtimestamp(outcome.at, zone).date() for outcome in failed}),
        sources=tuple(sorted({outcome.source for outcome in failed})),
        sites=tuple(sites[:SITES_KEPT]),
        tainted_runs=len({outcome.run_id for outcome in failed if outcome.tainted}),
    )


def digest_of(outcomes: Sequence[ToolOutcome], tally: RunTally, since: float, until: float, zone: tzinfo) -> Digest:
    """The digest of ``outcomes`` (the tools' results of the span, oldest first) and ``tally`` (how its runs ended)."""
    users: defaultdict[str, set[str]] = defaultdict(set)
    failed: defaultdict[str, list[ToolOutcome]] = defaultdict(list)
    for outcome in outcomes:
        users[outcome.tool].add(outcome.run_id)
        if not outcome.ok:
            failed[signature_of(outcome)].append(outcome)
    rows: list[FailureRow] = [
        _row(signature, results, len(users[results[0].tool]), zone) for signature, results in failed.items()
    ]
    rows.sort(key=lambda row: (-row.runs_failing, -row.chats, -row.failures, row.signature))
    return Digest(
        since=since,
        until=until,
        tally=tally,
        runs_with_tools=len({outcome.run_id for outcome in outcomes}),
        rows=tuple(rows[:ROWS_KEPT]),
    )


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"


def _chats(count: int) -> str:
    return f"{count} chat" if count == 1 else f"{count} chats"


def digest_words(digest: Digest) -> str:
    """The night's line: how the runs ended, how those handed a lesson ended beside the others, and the ways a tool
    failed in the most runs."""
    tally: RunTally = digest.tally
    parts: list[str] = [f"{_runs(tally.ended)} ended, {tally.done} done and {tally.failed} failed"]
    if tally.with_lesson > 0:
        others: int = tally.ended - tally.with_lesson
        others_done: int = tally.done - tally.with_lesson_done
        parts.append(
            f"{tally.with_lesson_done} of {_runs(tally.with_lesson)} handed a lesson ended done,"
            f" {others_done} of {others} without one"
        )
    if digest.rows:
        named: str = "; ".join(
            f"{row.signature} ({_runs(row.runs_failing)} of {row.runs_using}, {_chats(row.chats)})"
            for row in digest.rows[:ROWS_SAID]
        )
        parts.append(f"failed most: {named}")
    elif digest.runs_with_tools > 0:
        parts.append("no tool failed")
    return "; ".join(parts) + "."


async def night_digest(store: Store, now: float, zone: tzinfo) -> HarnessStep:
    """Read the runs of the day before ``now`` and keep their digest as the night's step: ``keep``, since it changes
    no text, with the line (``decision``) and the digest in its record."""
    # The version in use while the runs worked, by its id alone: the digest reads no text of it.
    base: str | None = await store.adopted_harness_id()
    step: HarnessStep = await store.create_harness_step(now, None, base)
    since: float = now - DAY_S
    outcomes: list[ToolOutcome] = await store.tool_outcomes(since, now, SOURCES_OUT)
    tally: RunTally = await store.run_tally(since, now, SOURCES_OUT)
    digest: Digest = digest_of(outcomes, tally, since, now, zone)
    record: str = json.dumps({"decision": digest_words(digest), "digest": digest.model_dump(mode="json")})
    return await store.finish_harness_step(step.id, now, "keep", record, None)
