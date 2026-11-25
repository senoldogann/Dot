"""Proof checks (item 1853): commands that have to exit 0 before a task run or a campaign counts as done.

A check is a command line with a time limit, set by the owner (``proof`` of ``POST /api/runs``, ``dotx run --proof``)
or by a run (``add_proof``). A run's checks are its ``proof`` events, a campaign's its ``proofs`` column; both are only
added to, never changed or removed, so a run cannot soften what it is held to. Dot runs them as ``shell`` calls of the
run itself, through the same gate, sandbox and events as a call of the model's: the loop at a finish with success,
``campaign_achieved`` before it judges a claim. Everything here is pure except :func:`run_checks`, which reads the
store.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, ValidationError

from dotagent.core.errors import StoreError
from dotagent.core.text import truncate_tail
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.claude_code import CLAUDE_CODE_PROVIDER
from dotagent.llm.opencode import OPENCODE_PROVIDER
from dotagent.store.db import Store
from dotagent.store.endings import GOAL_CHECK_MARK
from dotagent.store.models import (
    DEFAULT_PROOF_TIMEOUT_S,
    MAX_PROOF_CHECKS,
    MAX_PROOF_TIMEOUT_S,
    EventRecord,
    ProofBy,
    ProofCheck,
    ProofCommand,
    RunMode,
)

# The event of a run that holds one check it carries.
PROOF_EVENT: str = "proof"
# The event of a run that tells what one look at its checks came to.
PROOF_CHECKED_EVENT: str = "proof_check"
# The tool a check runs as.
PROOF_TOOL: str = "shell"
# The end of a failed check's output that the note back to the run quotes; the whole result is in the conversation.
PROOF_TAIL_CHARS: int = 3000

# How a check ended: it exited 0, it exited otherwise or was killed at its time limit, or it never ran (the gate, the
# owner or a cap refused it, or the run has no shell).
type ProofEnd = Literal["passed", "failed", "not_run"]


class ProofSpec(FrozenModel):
    """A check as the owner or a run gives it: a command line and its time limit in seconds."""

    command: ProofCommand
    timeout_s: int = Field(default=DEFAULT_PROOF_TIMEOUT_S, ge=1, le=MAX_PROOF_TIMEOUT_S)


class ProofOutcome(FrozenModel):
    """What one check came to at one look: how it ended and the text its call returned (or why it did not run)."""

    check: ProofCheck
    end: ProofEnd
    text: str


def checks_of(specs: Sequence[ProofSpec], by: ProofBy) -> tuple[ProofCheck, ...]:
    """The checks ``specs`` give, set by ``by``."""
    return tuple(ProofCheck(command=spec.command, timeout_s=spec.timeout_s, by=by) for spec in specs)


def added(held: Sequence[ProofCheck], new: Sequence[ProofCheck]) -> tuple[ProofCheck, ...]:
    """``new`` without the checks whose command ``held`` or an earlier one of ``new`` already has. Raises
    ``ValueError`` when the checks would be more than :data:`MAX_PROOF_CHECKS`."""
    seen: set[str] = {check.command for check in held}
    fresh: list[ProofCheck] = []
    for check in new:
        if check.command not in seen:
            seen.add(check.command)
            fresh.append(check)
    if len(held) + len(fresh) > MAX_PROOF_CHECKS:
        raise ValueError(
            f"at most {MAX_PROOF_CHECKS} proof checks: {len(held)} are set and {len(fresh)} more were given"
        )
    return tuple(fresh)


def proof_event_data(check: ProofCheck) -> JsonObject:
    """The data of a ``proof`` event."""
    return check.model_dump(mode="json")


async def run_checks(store: Store, run_id: str) -> tuple[ProofCheck, ...]:
    """The checks the run ``run_id`` carries, oldest first. Reads the store."""
    events: list[EventRecord] = await store.events_of_kind(run_id, PROOF_EVENT, MAX_PROOF_CHECKS)
    try:
        return tuple(ProofCheck.model_validate(event.data) for event in events)
    except ValidationError as exc:
        raise StoreError("a stored proof check does not read", {"run_id": run_id, "error": str(exc)}) from exc


def call_arguments(check: ProofCheck) -> JsonObject:
    """The arguments of the ``shell`` call a check runs as."""
    return {"command": check.command, "timeout_s": check.timeout_s}


def proof_end(is_error: bool, refused: bool, reached_tool: bool) -> ProofEnd:
    """How a check's call ended: it never ran when the gate refused it or it never reached its tool; else it passed
    when its result is no error (exit 0) and failed otherwise (an exit other than 0, or killed at its time limit)."""
    if refused or not reached_tool:
        return "not_run"
    return "failed" if is_error else "passed"


def over_limit(checks: Sequence[ProofCheck], max_timeout_s: int) -> tuple[ProofCheck, ...]:
    """The checks whose time limit is past ``max_timeout_s`` (``[shell] max_timeout_s``), which the shell would cut
    to it: they cannot run as they were set."""
    return tuple(check for check in checks if check.timeout_s > max_timeout_s)


def over_limit_text(max_timeout_s: int) -> str:
    """Why a check past the shell's limit does not run."""
    return (
        f"its limit is above [shell] max_timeout_s ({max_timeout_s} s), which a shell call is cut to; raise that "
        "setting or set a shorter limit"
    )


def not_run(checks: Sequence[ProofCheck], why: str) -> tuple[ProofOutcome, ...]:
    """The outcomes of checks that could not be run at all, for ``why``."""
    return tuple(ProofOutcome(check=check, end="not_run", text=why) for check in checks)


def checked_event_data(outcomes: Sequence[ProofOutcome]) -> JsonObject:
    """The data of a ``proof_check`` event: each check's command, who set it and how it ended."""
    return {
        "checks": [
            {"command": outcome.check.command, "by": outcome.check.by, "end": outcome.end} for outcome in outcomes
        ]
    }


def output_tail(text: str, max_chars: int) -> str:
    """The end of ``text``, at most ``max_chars`` characters of it, marked when the start was dropped: what a failing
    test run says last is what tells why."""
    if len(text) <= max_chars:
        return text
    return f"[… {len(text) - max_chars} characters before]\n{text[-max_chars:]}"


def _line(outcome: ProofOutcome) -> str:
    tail: str = output_tail(outcome.text.strip(), PROOF_TAIL_CHARS)
    return f"- `{outcome.check.command}` (limit {outcome.check.timeout_s} s):\n{tail}"


def unproven(outcomes: Sequence[ProofOutcome]) -> tuple[ProofOutcome, ...]:
    """The outcomes that did not pass."""
    return tuple(outcome for outcome in outcomes if outcome.end != "passed")


def never_ran(outcomes: Sequence[ProofOutcome]) -> tuple[ProofOutcome, ...]:
    """The outcomes of checks that could not run."""
    return tuple(outcome for outcome in outcomes if outcome.end == "not_run")


def failed_note(outcomes: Sequence[ProofOutcome]) -> str:
    """The note that sends a run back when a check failed: each failing command with the end of what it printed."""
    lines: str = "\n".join(_line(outcome) for outcome in unproven(outcomes))
    return (
        "[system note] The proof does not pass yet, so the run is not done: these checks must exit 0, and did not "
        f"(their whole output is in the call above):\n{lines}\nFix what they show, then call finish again; Dot runs "
        "every check again then. A check cannot be changed or removed."
    )


def failed_text(outcomes: Sequence[ProofOutcome], rounds: int) -> str:
    """Why the run ends failed with checks that still fail after ``rounds`` rounds of further work (0 at its limit)."""
    commands: str = ", ".join(f"`{outcome.check.command}`" for outcome in unproven(outcomes))
    after: str = "the run's limit is reached" if rounds == 0 else f"after {rounds} rounds of further work"
    return f"the proof did not pass ({commands} still fail) and {after}."


def not_run_text(outcomes: Sequence[ProofOutcome]) -> str:
    """Why the run ends failed with checks that could not run: each command and what kept it from running."""
    reasons: str = "; ".join(
        f"`{outcome.check.command}`: {truncate_tail(outcome.text.strip(), 300)}" for outcome in never_ran(outcomes)
    )
    return f"the proof could not run, so the answer is not proven: {reasons}"


def with_proof_note(answer: str, text: str) -> str:
    """The answer a run ended with, followed by what its proof came to, under the goal check's mark."""
    return f"{answer}{GOAL_CHECK_MARK}{text}"


def claim_refusal(outcomes: Sequence[ProofOutcome]) -> str:
    """What ``campaign_achieved`` answers when the campaign's checks do not pass: those that failed with their output,
    or, when one could not run, to write a setback and ask the owner."""
    if never_ran(outcomes):
        return (
            f"Not accepted: {not_run_text(outcomes)} Write it in the ledger (setback), ask the owner with ask_user how "
            "the check can run, and finish saying so in needs_from_user: the next session waits for their answer."
        )
    lines: str = "\n".join(_line(outcome) for outcome in unproven(outcomes))
    return (
        "Not accepted: the campaign's proof does not pass. These checks must exit 0, and did not:\n"
        f"{lines}\nThe campaign goes on: fix what they show, then claim again."
    )


# The programs whose turns end without Dot's loop (the Claude plan's ``claude``, OpenCode): a run on one of them is not
# settled where the checks run, so it is refused checks rather than let through unchecked.
_NOT_SETTLED_BY_DOT: frozenset[str] = frozenset({CLAUDE_CODE_PROVIDER, OPENCODE_PROVIDER})


def limit_refused(checks: Sequence[ProofCheck], max_timeout_s: int) -> str | None:
    """Why ``checks`` cannot be taken as they stand (one's limit is past the shell's), ``None`` when they can."""
    over: tuple[ProofCheck, ...] = over_limit(checks, max_timeout_s)
    if not over:
        return None
    return f"`{over[0].command}` (limit {over[0].timeout_s} s): {over_limit_text(max_timeout_s)}"


def proof_refused(mode: RunMode, provider: str) -> str | None:
    """Why a run of ``mode`` on a model of ``provider`` cannot carry proof checks, ``None`` when it can: only a task has
    the ``shell`` a check runs as and a ``finish`` it holds, and only Dot's own loop runs them."""
    if mode != "task":
        return f"proof checks hold a task's finish; a {mode} run has none to hold"
    if provider in _NOT_SETTLED_BY_DOT:
        return f"proof checks run in Dot's own loop, and a run on {provider} ends its turns in its own program"
    return None
