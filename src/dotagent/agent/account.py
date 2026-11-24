"""What a run did, from its record and not from its model (items 1745 and 1746).

A plan step marked done is judged by the tool results the run had while the step was open (:func:`step_proof`): shown
by one, left with an act that was not seen to hold and no look after it, or shown by none. The run's account
(:func:`run_account`) counts what its calls changed, ran, sent and checked, and names the plan steps that are open or
done without a tool result that shows them. Both read the ``tool_result`` events the executor stores and the run's
``plan`` events; every function here is pure.
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import JsonValue, ValidationError

from dotagent.agent.plan import CLOSED_STATES, PlanItem, PlanPayload
from dotagent.core.errors import StoreError
from dotagent.core.outcomes import StepTally, tally_unverified
from dotagent.core.types import FrozenModel
from dotagent.store.models import EventRecord

# How a done step stands against the run's record. ``shown``: a tool result of the run while it was open shows it.
# ``unverified``: the newest act of that time left a step that was not seen to hold, and nothing looked after it.
# ``none``: no tool result of that time shows anything. Empty: not judged (a step closed by a helper's report, or one
# stored before the judgement existed).
type ProofKind = Literal["", "shown", "unverified", "none"]

# The kinds of call that look at the state of things: a step whose act was not seen to hold is shown once one of them
# came after it. A command's output and a page opened are looks as well.
LOOK_KINDS: frozenset[str] = frozenset(
    {"read", "browser_read", "browser_navigate", "desktop_observe", "network", "mcp_read", "shell"}
)
# The kinds that changed something the owner keeps: a file a file tool wrote. A command's own writes are counted with
# the commands, which say nothing of what they did.
_CHANGE_KINDS: frozenset[str] = frozenset({"write"})
_COMMAND_KINDS: frozenset[str] = frozenset({"shell"})
# The flags of a call that sent something out for the owner or in public.
_SEND_FLAGS: frozenset[str] = frozenset({"message_send", "public_post", "external_send", "release"})
# How many call ids a step keeps as what shows it, and how many names the account lists of each kind.
_SHOWN_BY: int = 3
_LISTED: int = 10
_STEP_CHARS: int = 160


class ResultFact(FrozenModel):
    """What the account reads of one ``tool_result`` event: never its text."""

    event_id: int
    call_id: str
    tool: str
    kind: str
    ok: bool
    flags: tuple[str, ...]
    target: str
    summary: str
    tally: StepTally | None


class StepProof(FrozenModel):
    """How a done step stands (``kind``), the newest calls that show it (at most three), and, when it is not shown, why
    in words the model and the owner read."""

    kind: ProofKind
    calls: tuple[str, ...]
    why: str


class RunAccount(FrozenModel):
    """What a run did, by its record: its calls and how many failed; the files its file tools changed, the commands it
    ran and what it sent (counted, the first ten named); what its act steps came to (``acts``, summed); and its plan's
    steps done, those done without a tool result that shows them (``unshown``) and those still open."""

    calls: int
    failed_calls: int
    changed: tuple[str, ...]
    changed_count: int
    commands: int
    sent: tuple[str, ...]
    sent_count: int
    acts: StepTally
    plan_done: int
    unshown: tuple[str, ...]
    open_steps: tuple[str, ...]


def _text(data: Mapping[str, JsonValue], key: str) -> str:
    value: JsonValue = data.get(key, "")
    return value if isinstance(value, str) else ""


def result_fact(event: EventRecord) -> ResultFact:
    """The fact of a stored ``tool_result`` event. A tally that is there and malformed is a store error, never
    left out; an event stored before the tally existed has none."""
    raw: JsonValue = event.data.get("tally")
    try:
        tally: StepTally | None = None if raw is None else StepTally.model_validate(raw)
    except ValidationError as exc:
        raise StoreError(
            "a stored tool result holds a malformed tally",
            {"run_id": event.run_id, "event_id": event.id, "errors": exc.json(include_url=False)},
        ) from exc
    flags: JsonValue = event.data.get("flags")
    return ResultFact(
        event_id=event.id,
        call_id=_text(event.data, "call_id"),
        tool=_text(event.data, "tool"),
        kind=_text(event.data, "kind"),
        ok=event.data.get("ok") is True,
        flags=tuple(flag for flag in flags if isinstance(flag, str)) if isinstance(flags, list) else (),
        target=_text(event.data, "target"),
        summary=_text(event.data, "summary"),
        tally=tally,
    )


def _observed(facts: Sequence[ResultFact]) -> list[ResultFact]:
    """The calls that worked and are no bookkeeping of the run (its plan, its finish): what a step can be shown by."""
    return [fact for fact in facts if fact.ok and fact.kind and fact.kind != "meta"]


def step_proof(window: Sequence[ResultFact]) -> StepProof:
    """How a step marked done stands against the tool results of the time it was open (``window``, oldest first)."""
    observed: list[ResultFact] = _observed(window)
    if not observed:
        return StepProof(kind="none", calls=(), why="no tool result of the run while the step was open shows it")
    acts: list[int] = [index for index, fact in enumerate(observed) if fact.tally is not None]
    if acts:
        last: ResultFact = observed[acts[-1]]
        looked: bool = any(fact.kind in LOOK_KINDS for fact in observed[acts[-1] + 1 :])
        if last.tally is not None and tally_unverified(last.tally) and not looked:
            left: int = last.tally.contradicted + last.tally.uncertain + last.tally.not_checked
            return StepProof(
                kind="unverified",
                calls=(last.call_id,),
                why=(
                    f"its last {last.tool} call left {left} step(s) not seen to hold, and nothing looked at the "
                    "result after it"
                ),
            )
    return StepProof(kind="shown", calls=tuple(fact.call_id for fact in observed[-_SHOWN_BY:]), why="")


def _key(text: str) -> str:
    return text.strip().casefold()


def opened_after(plans: Sequence[tuple[int, PlanPayload]], text: str) -> int:
    """The id of the event after which the step named ``text`` has been open without a break, from the run's plans
    (``(event id, plan)``, oldest first): the earliest plan of the newest stretch in which it is open. Zero, the run's
    start, for a step no plan held open (one given done when it was first written)."""
    wanted: str = _key(text)
    since: int = 0
    for event_id, plan in reversed(plans):
        step: PlanItem | None = next((one for one in plan.steps if _key(one.text) == wanted), None)
        if step is None or step.status in CLOSED_STATES:
            if since:
                break
            continue
        since = event_id
    return since


def with_proof(step: PlanItem, proof: StepProof) -> PlanItem:
    return step.model_copy(update={"proof": proof.kind, "shown_by": proof.calls})


def proof_words(number: int, proof: StepProof) -> str:
    """What the result of a mark tells the model of a step done that no tool result shows; empty when one does."""
    if proof.kind in ("", "shown"):
        return ""
    return (
        f"Step {number} is recorded done, but {proof.why}: the run's account lists it as not verified. Look at what "
        "it changed (read the file, the page or the window), then mark the step done again with what you saw."
    )


def _step_name(number: int, step: PlanItem) -> str:
    text: str = " ".join(step.text.split())
    short: str = text if len(text) <= _STEP_CHARS else f"{text[: _STEP_CHARS - 1]}…"
    return f"{number}. {short}"


def _summed(tallies: Sequence[StepTally]) -> StepTally:
    return StepTally(
        verified=sum(one.verified for one in tallies),
        contradicted=sum(one.contradicted for one in tallies),
        uncertain=sum(one.uncertain for one in tallies),
        not_checked=sum(one.not_checked for one in tallies),
        failed=sum(one.failed for one in tallies),
    )


def _named(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def run_account(facts: Sequence[ResultFact], plan: PlanPayload) -> RunAccount:
    """The account of a run from the facts of its tool results, oldest first, and its newest plan."""
    worked: list[ResultFact] = [fact for fact in facts if fact.ok]
    changed: tuple[str, ...] = _named([fact.target for fact in worked if fact.kind in _CHANGE_KINDS])
    sent: list[str] = [fact.summary or fact.tool for fact in worked if _SEND_FLAGS.intersection(fact.flags)]
    return RunAccount(
        calls=len(facts),
        failed_calls=len(facts) - len(worked),
        changed=changed[:_LISTED],
        changed_count=len(changed),
        commands=sum(1 for fact in worked if fact.kind in _COMMAND_KINDS),
        sent=tuple(sent[:_LISTED]),
        sent_count=len(sent),
        acts=_summed([fact.tally for fact in facts if fact.tally is not None]),
        plan_done=sum(1 for step in plan.steps if step.status == "done"),
        unshown=tuple(
            _step_name(number, step)
            for number, step in enumerate(plan.steps, start=1)
            if step.status == "done" and step.proof in ("none", "unverified")
        ),
        open_steps=tuple(
            _step_name(number, step)
            for number, step in enumerate(plan.steps, start=1)
            if step.status not in CLOSED_STATES
        ),
    )
