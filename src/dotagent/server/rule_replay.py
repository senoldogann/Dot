"""Trying a rule on past steps before it is added (item 469): which of the steps of the last days it fits, which of
those an earlier rule decides first, which no rule decides (a yolo run's, or what the policy settles before it reads
any rule), and how what it would decide compares with what was decided then: the decision a step's record keeps since
item 468, or for a step the gate did not let through, what the policy event of its call said. And how each of the
owner's rules was used on the same steps, with today's rules (item 473). The steps are the ``tool_result`` events, read
newest first by pages, at most so many and for so long, and the rules are matched as the policy matches them
(:func:`dotagent.policy.engine.match_rule`), the policy itself untouched: nothing is written and nothing decided."""

import asyncio
import time
from collections import Counter
from collections.abc import Callable, Sequence
from typing import get_args

from dotagent.agent.authorization import StepDecision
from dotagent.agent.gate import TAINT_EVENTS
from dotagent.core.errors import ConfigError
from dotagent.core.text import plain_line, redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.policy.engine import match_rule
from dotagent.policy.types import TAINT_ESCALATION_FLAGS, ActionInfo, ActionKind, DecisionSource, Risk, Verdict
from dotagent.server.views import RuleExample, RulePreview, RuleShadow, RulesUsage, RuleUsage
from dotagent.store.db import Store
from dotagent.store.models import Behavior, RuleRecord, StepRow

DAY_S: float = 86_400.0
# The id of the rule being tried, which no stored rule has.
CANDIDATE_ID: str = "candidate"
# How a try-out reads: pages of steps, at most so many steps and so long, so the store stays free for the runs at work
# (it has one connection). The steps of a page are counted in a worker thread, so that the event loop, which serves the
# pages and the runs, takes its turn between two matches; one match it still waits out, as the regex module keeps the
# interpreter while it matches (for at most the daemon's limit for one, item 387).
_PAGE: int = 1000
_MAX_STEPS: int = 20_000
_BUDGET_S: float = 2.0
_EXAMPLES: int = 20
_EXAMPLE_CHARS: int = 200
_KINDS: frozenset[str] = frozenset(get_args(ActionKind))
_RISKS: frozenset[str] = frozenset(get_args(Risk))
# What a rule's behaviour comes to at the gate, as the policy reads it (policy/engine.py, _BEHAVIOR_VERDICT).
_VERDICT: dict[Behavior, Verdict] = {"allow": "allow", "preapproved": "allow", "ask": "ask", "handoff": "handoff"}
_STRICTNESS: dict[Verdict, int] = {"allow": 0, "ask": 1, "handoff": 2, "deny": 3}
# What settles a step before the policy reads any rule (policy/engine.py, decide_past_review; the gate's pace): a rule
# changes nothing there, but for one that hands the step off, which the guard of messages and posts, the guard of a
# login filled from the password manager (item 493) and a spend within its caps give way to.
_BEFORE_RULES: frozenset[DecisionSource] = frozenset(
    {"auto_review", "mode", "own_files", "hard_stop", "guard", "vault", "spend", "pace"}
)
_HANDOFF_FIRST: frozenset[DecisionSource] = frozenset({"guard", "vault", "spend"})


def candidate_rule(behavior: Behavior, tool_glob: str, match_regex: str, existing: Sequence[RuleRecord]) -> RuleRecord:
    """The rule tried, where a new rule goes: after every stored one."""
    return RuleRecord(
        id=CANDIDATE_ID,
        position=max((rule.position for rule in existing), default=0) + 1,
        enabled=True,
        behavior=behavior,
        tool_glob=tool_glob,
        match_regex=match_regex,
        description="",
        created_at=time.time(),
    )


def _action(step: StepRow) -> ActionInfo | None:
    """The step as the policy matched rules on it: its tool, summary and target; a step of a kind or risk this Dot does
    not know is passed over. In a chat where a login was filled, the policy matched the step as written and only its
    masked record was kept (item 621), so a rule tried here on such a step can count otherwise than the live decision
    did: the password is not stored to be matched again."""
    if step.kind not in _KINDS or step.risk not in _RISKS:
        return None
    return ActionInfo.model_validate(
        {
            "tool": step.tool,
            "kind": step.kind,
            "risk": step.risk,
            "summary": step.summary,
            "target": step.target,
            "flags": [],
        }
    )


def _was(step: StepRow) -> StepDecision | None:
    return None if step.decision is None else StepDecision.model_validate(step.decision)


def _example(step: StepRow) -> RuleExample:
    return RuleExample(
        event_id=step.event_id,
        run_id=step.run_id,
        at=step.at,
        tool=step.tool,
        summary=plain_line(redact_secrets(step.summary), _EXAMPLE_CHARS),
        target=plain_line(redact_secrets(step.target), _EXAMPLE_CHARS),
        ok=step.ok,
        was=_was(step),
        recorded=step.recorded,
    )


class _Said(FrozenModel):
    """What the policy said of a step then: where the answer came from, what it was, and whether the run was tainted
    when the gate decided (kept since item 474; ``None`` before)."""

    source: DecisionSource
    verdict: Verdict
    tainted: bool | None


def _said(step: StepRow) -> _Said | None:
    """What the policy said of the step then: the decision its record keeps, or for a step the gate did not let through
    (refused, declined, handed to the owner), the policy event of its call; ``None`` when neither says."""
    was: StepDecision | None = _was(step)
    if was is not None:
        return _Said(source=was.source, verdict=was.verdict, tainted=was.tainted)
    if step.stopped_by is not None:
        return _Said.model_validate(step.stopped_by)
    return None


def _settled_before_rules(said: _Said, behavior: Behavior) -> bool:
    """Whether what decided the step then comes before every rule, so that the rule tried would change nothing."""
    return said.source in _BEFORE_RULES and not (said.source in _HANDOFF_FIRST and behavior == "handoff")


def _verdict_now(step: StepRow, said: _Said, behavior: Behavior) -> Verdict:
    """What the policy would decide of the step with the rule tried deciding it: the rule's answer, but for an ``allow``
    on a step that reaches outside while the run was tainted, which the taint makes a question (``preapproved`` it does
    not; policy/engine.py, decide_past_review). The taint is the one the gate weighed, kept with the step's decision or
    policy event since item 474; for a step from before, the run's taint before the step's result, which for a call
    that ran beside read-only ones may hold one their results brought after the gate decided."""
    tainted: bool = step.tainted_before if said.tainted is None else said.tainted
    if behavior == "allow" and tainted and TAINT_ESCALATION_FLAGS.intersection(step.flags):
        return "ask"
    return _VERDICT[behavior]


class Tally:
    """What a try-out counted so far; one is filled page by page and read once at the end."""

    def __init__(self) -> None:
        self.scanned: int = 0
        self.oldest: float | None = None
        self.fits: int = 0
        self.shadowed_by: Counter[str] = Counter()
        self.in_yolo: int = 0
        self.before_rules: int = 0
        self.would_decide: int = 0
        self.stricter: int = 0
        self.looser: int = 0
        self.same: int = 0
        self.stopped: int = 0
        self.unknown: int = 0
        self.examples: list[RuleExample] = []
        self.problem: str | None = None


def _count_first(tally: Tally, step: StepRow, behavior: Behavior) -> None:
    """Count a step the rule tried decides (a hand-off wherever it stands, else as the first rule to fit, item 481): a
    yolo run's, which asks nothing whatever the rules say;
    one settled before any rule; or one it would decide, against what was decided then. Of those, a step that did not
    run and whose record does not say why is ``stopped``: another run held what it needed, the review model refused it,
    or the gate stopped an OpenCode agent's call, whose policy event comes before its ``tool_call``. One with no
    decision on record at all, from before item 468 or an OpenCode agent's from before item 474, is ``unknown``."""
    if step.yolo:
        tally.in_yolo += 1
        return
    said: _Said | None = _said(step)
    if said is not None and _settled_before_rules(said, behavior):
        tally.before_rules += 1
        return
    tally.would_decide += 1
    if said is None:
        if step.recorded:
            tally.stopped += 1
        else:
            tally.unknown += 1
        return
    now: int = _STRICTNESS[_verdict_now(step, said, behavior)]
    then: int = _STRICTNESS[said.verdict]
    if now > then:
        tally.stricter += 1
    elif now < then:
        tally.looser += 1
    else:
        tally.same += 1


def tally_steps(
    tally: Tally, existing: Sequence[RuleRecord], candidate: RuleRecord, steps: Sequence[StepRow], deadline: float
) -> int:
    """Count ``steps`` (newest first) into ``tally`` until the monotonic ``deadline`` passes, and say how many were
    counted (one at least): a pattern that takes too long on one stops the count there, with the problem said, as the
    daemon would refuse the step."""
    ruled: list[RuleRecord] = [*existing, candidate]
    counted: int = 0
    for step in steps:
        if counted > 0 and time.monotonic() > deadline:
            return counted
        counted += 1
        action: ActionInfo | None = _action(step)
        tally.scanned += 1
        tally.oldest = step.at
        if action is None:
            continue
        try:
            if match_rule(action, [candidate]) is None:
                continue
            ruling: RuleRecord | None = match_rule(action, ruled)
        except ConfigError as error:
            tally.problem = (
                f"This pattern takes longer than the daemon allows on a past step ({step.tool}: "
                f"{plain_line(redact_secrets(step.summary), _EXAMPLE_CHARS)}), or a stored rule cannot be read: "
                f"{error.message}. The daemon would refuse such a step."
            )
            return counted
        tally.fits += 1
        if len(tally.examples) < _EXAMPLES:
            tally.examples.append(_example(step))
        if ruling is not None and ruling.id != CANDIDATE_ID:
            tally.shadowed_by[ruling.id] += 1
        else:
            _count_first(tally, step, candidate.behavior)
    return counted


def preview_of(tally: Tally, existing: Sequence[RuleRecord], partial: bool) -> RulePreview:
    names: dict[str, str] = {rule.id: rule.description or f"rule {rule.position}" for rule in existing}
    return RulePreview(
        scanned=tally.scanned,
        since=tally.oldest,
        partial=partial,
        fits=tally.fits,
        shadowed_by=[
            RuleShadow(rule_id=rule_id, description=names.get(rule_id, rule_id), steps=steps)
            for rule_id, steps in tally.shadowed_by.most_common()
        ],
        in_yolo=tally.in_yolo,
        before_rules=tally.before_rules,
        would_decide=tally.would_decide,
        stricter=tally.stricter,
        looser=tally.looser,
        same=tally.same,
        stopped=tally.stopped,
        unknown=tally.unknown,
        examples=tally.examples,
        problem=tally.problem,
    )


async def _read_steps(
    store: Store, days: int, now: float, count: Callable[[list[StepRow], float], int], stopped: Callable[[], bool]
) -> bool:
    """Read the steps of the last ``days`` before ``now``, newest first, by pages, and hand each page to ``count``,
    which counts it in a worker thread until the monotonic deadline it is given and says how many it counted; reading
    ends at the end of the steps, once ``stopped`` says a problem stopped the count, or at the cap of steps or of time.
    Whether steps were left unread."""
    floor: int = await store.first_event_id_since(now - days * DAY_S)
    before: int = 2**62
    scanned: int = 0
    ends: float = time.monotonic() + _BUDGET_S
    while True:
        wanted: int = min(_PAGE, _MAX_STEPS - scanned)
        page: list[StepRow] = await store.tool_steps(floor, before, wanted, TAINT_EVENTS)
        done: int = await asyncio.to_thread(count, page, ends)
        scanned += done
        if stopped():
            return False
        if done < len(page):
            return True
        if len(page) < wanted:
            return False
        before = page[-1].event_id
        if scanned >= _MAX_STEPS or time.monotonic() > ends:
            left: list[StepRow] = await store.tool_steps(floor, before, 1, TAINT_EVENTS)
            return left != []


async def preview_rule(
    store: Store, candidate: RuleRecord, existing: Sequence[RuleRecord], days: int, now: float
) -> RulePreview:
    """Try ``candidate`` on the steps of the last ``days`` before ``now``, newest first, by pages counted in a worker
    thread; a try-out that reaches its cap of steps or of time with steps left says it read the newest only."""
    tally: Tally = Tally()

    def count(page: list[StepRow], deadline: float) -> int:
        return tally_steps(tally, existing, candidate, page, deadline)

    partial: bool = await _read_steps(store, days, now, count, lambda: tally.problem is not None)
    return preview_of(tally, existing, partial)


class RuleTally:
    """What one enabled rule did on the steps read so far, with today's rules (item 473)."""

    def __init__(self) -> None:
        self.fits: int = 0
        self.last_fit_at: float | None = None
        self.decides: int = 0
        self.in_yolo: int = 0
        self.before_rules: int = 0
        self.shadowed_by: Counter[str] = Counter()


class UsageTally:
    """How the enabled rules were used on the steps read so far; one is filled page by page and read once at the end."""

    def __init__(self, rules: Sequence[RuleRecord]) -> None:
        self.scanned: int = 0
        self.oldest: float | None = None
        self.rules: dict[str, RuleTally] = {rule.id: RuleTally() for rule in rules if rule.enabled}
        self.problem: str | None = None


def _count_ruled(kept: RuleTally, step: StepRow, behavior: Behavior) -> None:
    """Count a step that a rule decides (a hand-off wherever it stands, else as the first rule to fit it, item 481): a
    yolo run's, which asks nothing whatever the rules say; one settled before any rule; or one it decides."""
    if step.yolo:
        kept.in_yolo += 1
        return
    said: _Said | None = _said(step)
    if said is not None and _settled_before_rules(said, behavior):
        kept.before_rules += 1
        return
    kept.decides += 1


def _slow_rule(number: int, step: StepRow, error: ConfigError, reached: bool) -> str:
    """What stops a count of the rules' use: rule ``number``'s pattern took too long on ``step``, or could not be read.
    The daemon refuses a step that reaches that rule; ``reached`` says whether this one would
    (:func:`_read_by_the_daemon`: no earlier rule fitting it, or, for a rule that hands off, no earlier one that hands
    off; a check before every rule may still have settled it)."""
    said: str = (
        f"The pattern of rule #{number} takes longer than the daemon allows on a past step ({step.tool}: "
        f"{plain_line(redact_secrets(step.summary), _EXAMPLE_CHARS)}), or cannot be read: {error.message}."
    )
    if reached:
        return f"{said} The daemon refuses a step that reaches rule #{number}."
    return f"{said} An earlier rule fits this step first, but the daemon refuses a step that reaches rule #{number}."


def _read_by_the_daemon(rule: RuleRecord, earlier: Sequence[RuleRecord]) -> bool:
    """Whether the policy reads ``rule`` for a step the rules ``earlier`` fit (item 481): when none does, and, for a
    rule that hands off, when none of them hands off, since a hand-off decides wherever it stands."""
    if not earlier:
        return True
    return rule.behavior == "handoff" and not any(other.behavior == "handoff" for other in earlier)


def tally_usage(tally: UsageTally, rules: Sequence[RuleRecord], steps: Sequence[StepRow], deadline: float) -> int:
    """Count ``steps`` (newest first) for each enabled rule of ``rules`` until the monotonic ``deadline`` passes, and
    say how many were counted (one at least): which rules a step fits, and the one of them that decides it, as the
    policy picks it (:func:`dotagent.policy.engine.ruling_rule`: the first that hands off, else the first in their
    order, item 481). A pattern that takes too long on one stops the count there, with the problem said, as the daemon
    refuses a step whose rules it has to read that far."""
    ordered: list[RuleRecord] = sorted(rules, key=lambda rule: rule.position)
    # A rule's number as the page shows it: its place among all the rules, those switched off too.
    numbers: dict[str, int] = {rule.id: index + 1 for index, rule in enumerate(ordered)}
    enabled: list[RuleRecord] = [rule for rule in ordered if rule.enabled]
    counted: int = 0
    for step in steps:
        if counted > 0 and time.monotonic() > deadline:
            return counted
        counted += 1
        tally.scanned += 1
        tally.oldest = step.at
        action: ActionInfo | None = _action(step)
        if action is None:
            continue
        fitting: list[RuleRecord] = []
        for rule in enabled:
            try:
                fits: bool = match_rule(action, [rule]) is not None
            except ConfigError as error:
                tally.problem = _slow_rule(numbers[rule.id], step, error, _read_by_the_daemon(rule, fitting))
                return counted
            if fits:
                fitting.append(rule)
        if not fitting:
            continue
        decider: RuleRecord = next((rule for rule in fitting if rule.behavior == "handoff"), fitting[0])
        for rule in fitting:
            kept: RuleTally = tally.rules[rule.id]
            kept.fits += 1
            if kept.last_fit_at is None:
                kept.last_fit_at = step.at
            if rule.id != decider.id:
                kept.shadowed_by[decider.id] += 1
        _count_ruled(tally.rules[decider.id], step, decider.behavior)
    return counted


def usage_of(tally: UsageTally, rules: Sequence[RuleRecord], partial: bool) -> RulesUsage:
    names: dict[str, str] = {rule.id: rule.description or f"rule {rule.position}" for rule in rules}
    return RulesUsage(
        scanned=tally.scanned,
        since=tally.oldest,
        partial=partial,
        rules=[
            RuleUsage(
                rule_id=rule_id,
                fits=kept.fits,
                last_fit_at=kept.last_fit_at,
                decides=kept.decides,
                in_yolo=kept.in_yolo,
                before_rules=kept.before_rules,
                shadowed_by=[
                    RuleShadow(rule_id=first, description=names.get(first, first), steps=steps)
                    for first, steps in kept.shadowed_by.most_common()
                ],
            )
            for rule_id, kept in tally.rules.items()
        ],
        problem=tally.problem,
    )


async def rules_usage(store: Store, rules: Sequence[RuleRecord], days: int, now: float) -> RulesUsage:
    """How each enabled rule of ``rules`` was used on the steps of the last ``days`` before ``now``, with today's rules
    (item 473), read as a try-out reads them."""
    tally: UsageTally = UsageTally(rules)

    def count(page: list[StepRow], deadline: float) -> int:
        return tally_usage(tally, rules, page, deadline)

    partial: bool = await _read_steps(store, days, now, count, lambda: tally.problem is not None)
    return usage_of(tally, rules, partial)
