"""Checking the end of a run against what the harness observed, not against what the model says it did.

The loop keeps :class:`RunFacts` from the outcomes of the tool calls, never from a model: the files the file tools
wrote, the failed calls that no later success of the same tool and target followed, the failed calls that one did
follow, the newest commands that test, build or lint, and the paths and addresses that successful calls named. The goal
check reads them next to its digest; the lessons of a run (:mod:`dotagent.agent.lessons`) start from its failures.
:func:`answer_paths` and :func:`missing_paths` find the paths a final answer names that do not exist and that no
successful call named; with ``[agent] verify_answer_paths`` such an answer is sent back once. Every function here is
pure except :func:`missing_paths` and :func:`checked_facts`, which read the file system.
"""

import os
import re
from collections.abc import Collection, Sequence
from pathlib import Path

from pydantic import JsonValue

from dotagent.agent.continuity import identifiers
from dotagent.agent.executor import ToolOutcome
from dotagent.core.text import redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.policy.paths import expand_path
from dotagent.policy.types import ActionInfo

_FAILURES_KEPT: int = 10
_CHECKS_KEPT: int = 6
_WRITTEN_KEPT: int = 30
_NAMED_KEPT: int = 300
_TARGET_CHARS: int = 160
_RESULT_CHARS: int = 200
_NAMES_SHOWN: int = 12
# A path may go on past a space ("~/Library/Application Support"): this many more words are tried.
_SPACED_WORDS: int = 4
_PATH_STARTS: tuple[str, ...] = ("/", "~")
_ADDRESS_STARTS: tuple[str, ...] = ("http://", "https://")
_TRAILING_PUNCTUATION: str = ".,;:!?"

# Shell commands that test, build or lint: their outcome tells whether the work holds. The tool's name stands where a
# command does (after a space, a path or a separator), so "cat pytest.ini" is not one.
_CHECK_COMMAND: re.Pattern[str] = re.compile(
    r"(?:^|[\s;&|(/])"
    r"(?:pytest|unittest|tox|nox|ruff|mypy|pyright|flake8|pylint|tsc|eslint|biome|jest|vitest|mocha|rspec|phpunit"
    r"|xcodebuild|gradlew?|mvn|make|cmake|ninja"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|build|lint|check|typecheck)"
    r"|(?:cargo|go|swift|dotnet|deno)\s+(?:test|build|check|clippy|vet|lint))"
    r"(?=[\s;&|):]|$)"
)
_SPACED_WORD: re.Pattern[str] = re.compile(r" [\w.@+\-]+(?:/[\w.@+\-]+)*")


class CallFact(FrozenModel):
    """One tool call that ran: ``target`` is what it acted on (a file, a command, an address), ``result`` the start of
    what it returned."""

    tool: str
    target: str
    ok: bool
    result: str


class RunFacts(FrozenModel):
    """What the harness observed of a run's work, newest last. ``failures`` are the failed calls that no later success
    of the same tool and target followed, ``resolved`` the failed calls that one did follow (the run found how to make
    them work), ``checks`` the commands that test, build or lint (one that runs again replaces its entry), ``written``
    the files the file tools wrote or edited, and ``named`` the paths and addresses that successful calls named."""

    written: tuple[str, ...]
    failures: tuple[CallFact, ...]
    resolved: tuple[CallFact, ...]
    checks: tuple[CallFact, ...]
    named: tuple[str, ...]


class NamedPath(FrozenModel):
    """A path a final answer names. ``readings`` are the ways to read it, ``name`` first; a longer reading goes on past
    a space, as "~/Library/Application Support/Dot" does."""

    name: str
    readings: tuple[str, ...]


def new_facts() -> RunFacts:
    return RunFacts(written=(), failures=(), resolved=(), checks=(), named=())


def _flat(text: str, limit: int) -> str:
    """``text`` on one line and at most ``limit`` characters, secrets masked before the cut, which could leave a part
    of a secret too short to be recognised."""
    flat: str = " ".join(redact_secrets(text).split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _first_line(text: str) -> str:
    lines: list[str] = [line for line in text.splitlines() if line.strip()]
    return _flat(lines[0], _RESULT_CHARS) if lines else ""


def _ends(text: str) -> str:
    """The first and the last line of a result: for a shell command its exit status and, for most checkers, the tally."""
    lines: list[str] = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return _first_line(text)
    return _flat(f"{lines[0]} … {lines[-1]}", _RESULT_CHARS)


def _strings(value: JsonValue) -> list[str]:
    """Every string in a tool call's arguments."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    return []


def _named_by(outcome: ToolOutcome, action: ActionInfo) -> list[str]:
    """The paths and addresses a call named: those in its arguments, and the file a file tool acted on, whose name
    may hold spaces."""
    found: list[str] = sorted(identifiers("\n".join(_strings(outcome.call.arguments))))
    return [*found, action.target] if action.kind in ("read", "write") else found


def _with_newest(kept: tuple[str, ...], new: Sequence[str]) -> tuple[str, ...]:
    """``kept`` followed by ``new``, each value once, at its newest place."""
    fresh: set[str] = set(new)
    return tuple(dict.fromkeys([*(value for value in kept if value not in fresh), *new]))


def _with_outcome(facts: RunFacts, outcome: ToolOutcome) -> RunFacts:
    action: ActionInfo | None = outcome.action
    if action is None or action.kind == "meta":
        return facts
    succeeded: bool = not outcome.result.is_error
    tool: str = outcome.call.name
    target: str = _flat(action.target, _TARGET_CHARS)
    failures: tuple[CallFact, ...] = tuple(
        fact for fact in facts.failures if (fact.tool, fact.target) != (tool, target)
    )
    earlier: CallFact | None = next(
        (fact for fact in facts.failures if (fact.tool, fact.target) == (tool, target)), None
    )
    resolved: tuple[CallFact, ...] = facts.resolved
    if not succeeded:
        failures = (*failures, CallFact(tool=tool, target=target, ok=False, result=_first_line(outcome.result.text)))
    elif earlier is not None:
        resolved = (*(fact for fact in resolved if (fact.tool, fact.target) != (tool, target)), earlier)
    checks: tuple[CallFact, ...] = facts.checks
    if action.kind == "shell" and _CHECK_COMMAND.search(action.target) is not None:
        checks = (
            *(fact for fact in checks if fact.target != target),
            CallFact(tool=tool, target=target, ok=succeeded, result=_ends(outcome.result.text)),
        )
    written: tuple[str, ...] = facts.written
    named: tuple[str, ...] = facts.named
    if succeeded:
        if action.kind == "write":
            written = _with_newest(written, [action.target])
        named = _with_newest(named, _named_by(outcome, action))
    return RunFacts(
        written=written[-_WRITTEN_KEPT:],
        failures=failures[-_FAILURES_KEPT:],
        resolved=resolved[-_FAILURES_KEPT:],
        checks=checks[-_CHECKS_KEPT:],
        named=named[-_NAMED_KEPT:],
    )


def with_outcomes(facts: RunFacts, outcomes: Sequence[ToolOutcome]) -> RunFacts:
    """The facts after the tool calls of one step ran. A call that never ran (an unknown tool, arguments that did not
    fit it) and a bookkeeping call (the plan, finish, turning on a tool group) add nothing."""
    current: RunFacts = facts
    for outcome in outcomes:
        current = _with_outcome(current, outcome)
    return current


def _readings(answer: str, name: str) -> tuple[str, ...]:
    readings: dict[str, None] = {name: None}
    start: int = answer.find(name)
    while start >= 0:
        end: int = start + len(name)
        longer: str = name
        for _ in range(_SPACED_WORDS):
            word: re.Match[str] | None = _SPACED_WORD.match(answer, end)
            if word is None:
                break
            longer += word.group()
            end = word.end()
            readings[longer.rstrip(_TRAILING_PUNCTUATION)] = None
        start = answer.find(name, start + 1)
    return tuple(readings)


def answer_paths(answer: str) -> tuple[NamedPath, ...]:
    """The paths ``answer`` names, sorted by name: those :func:`dotagent.agent.continuity.identifiers` finds, ``~``
    paths and absolute paths under the roots it knows (``/Users``, ``/tmp``, ``/private``, ``/var``, ``/opt``,
    ``/Applications`` and ``/Library``)."""
    names: list[str] = sorted(name for name in identifiers(answer) if name.startswith(_PATH_STARTS))
    return tuple(NamedPath(name=name, readings=_readings(answer, name)) for name in names)


def missing_paths(paths: Sequence[NamedPath], named: Collection[str], workspace: Path) -> tuple[str, ...]:
    """The names of ``paths`` of which no reading exists and that no successful call of the run named: a file the run
    removed or moved is not missing. A relative ``named`` value is resolved against ``workspace``. Reads the file
    system."""
    touched: set[Path] = {expand_path(value, workspace) for value in named if not value.startswith(_ADDRESS_STARTS)}
    missing: list[str] = []
    for path in paths:
        readings: list[Path] = [expand_path(reading, workspace) for reading in path.readings]
        if not any(reading in touched or os.path.exists(reading) for reading in readings):
            missing.append(path.name)
    return tuple(missing)


def unopened_addresses(answer: str, named: Collection[str]) -> tuple[str, ...]:
    """The addresses ``answer`` names that no successful call of the run named, sorted."""
    opened: set[str] = {value.rstrip("/") for value in named if value.startswith(_ADDRESS_STARTS)}
    return tuple(
        sorted(
            address
            for address in identifiers(answer)
            if address.startswith(_ADDRESS_STARTS) and address.rstrip("/") not in opened
        )
    )


def _listed(values: Sequence[str]) -> list[str]:
    shown: list[str] = [f"- {value}" for value in values[:_NAMES_SHOWN]]
    more: int = len(values) - _NAMES_SHOWN
    return [*shown, f"- and {more} more"] if more > 0 else shown


def render_facts(facts: RunFacts, missing: Sequence[str], unopened: Sequence[str]) -> str:
    """The facts as the goal check reads them: every section, with "none" when it is empty. ``missing`` are the paths
    the final answer names that do not exist and that no successful call named, ``unopened`` the addresses it names
    that no call opened."""
    sections: tuple[tuple[str, list[str]], ...] = (
        ("Files the file tools wrote or edited", _listed(facts.written)),
        (
            "Failed calls that no later success of the same tool and target followed",
            [f"- {fact.tool} {fact.target}: {fact.result}" for fact in facts.failures],
        ),
        (
            "Commands that test, build or lint, with their results",
            [f"- {fact.target}: {'succeeded' if fact.ok else 'failed'}, {fact.result}" for fact in facts.checks],
        ),
        ("Paths the final answer names that do not exist and that no successful call named", _listed(missing)),
        ("Addresses the final answer names that no call of the run opened", _listed(unopened)),
    )
    return "\n\n".join(f"{title}:\n" + ("\n".join(lines) if lines else "none") for title, lines in sections)


def checked_facts(facts: RunFacts, answer: str, workspace: Path) -> str:
    """The facts as the goal check reads them next to ``answer`` (:func:`render_facts`), secrets masked. Reads the file
    system."""
    missing: tuple[str, ...] = missing_paths(answer_paths(answer), facts.named, workspace)
    return redact_secrets(render_facts(facts, missing, unopened_addresses(answer, facts.named)))


def paths_note(missing: Sequence[str]) -> str:
    """The note that sends a run back because its final answer names paths that do not exist."""
    listed: str = "\n".join(_listed(missing))
    return (
        "[system note] Your final answer names paths that do not exist, and no successful call of this run named "
        f"them:\n{listed}\nWhere the answer says that something is at one of these paths, that is not true yet: create "
        "it, or correct the answer to where it really is. If the answer names them on purpose (they were removed, or "
        "it reports them as missing), call finish again with the same answer."
    )
