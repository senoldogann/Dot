"""The progress watchdog: tells from what a run does whether it is getting anywhere, and what to do when it is not.

Every step in which a run acted is observed: what it called and what came back. Four detectors look at the recent
steps: the same step again and again (``repeat``), a short cycle of steps (``cycle``), a streak of steps in which every
call failed (``failures``) and steps that only make calls the run already made, with the results it already had
(``revisiting``). When one fires, the run climbs a ladder: a nudge, a forced re-plan, a second opinion of the review
model, a question to the user and, at the top, a stop that says what was tried. A long quiet stretch brings it one rung
down again, so hiccups hours apart do not add up to a stop.

Everything here is pure: the state goes in and a new state comes out, and the texts the run is given are built here too.
The loop does what an alert asks for (stores the note, limits the next step, calls the review model, ends the run).
"""

import hashlib
from collections.abc import Sequence
from typing import Literal

from dotagent.agent.continuity import LedgerAction, RunLedger
from dotagent.config import AgentConfig
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.evals import EVAL_SOURCE
from dotagent.policy.types import ApprovalMode, QuestionMode
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.endings import PREFIX_STALLED
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL

type Pattern = Literal["repeat", "cycle", "failures", "revisiting"]
type Rung = Literal["nudge", "replan", "consult", "ask", "stop"]

# A cycle is looked for at these lengths, and counts when it has gone round twice.
_CYCLE_PERIODS: tuple[int, ...] = (2, 3)
# The window has to hold a cycle of the longest period seen twice.
_MIN_WINDOW: int = 2 * max(_CYCLE_PERIODS)
# Quiet acting steps (no detector fires) that bring the ladder one rung down.
_DECAY_STEPS: int = 12
_LABEL_CHARS: int = 70
_LABELS_SHOWN: int = 3
_STOP_ACTIONS: int = 8
_STOP_TOUCHED: int = 10
_STOP_PLAN_CHARS: int = 1500
# Runs that nobody watches: nobody can answer a question there.
_UNATTENDED_SOURCES: frozenset[str] = frozenset({SCHEDULE_SOURCE, DELEGATE_SOURCE, EVAL_SOURCE})

_FULL_PATTERNS: tuple[Pattern, ...] = ("repeat", "cycle", "failures", "revisiting")
_PLAIN_PATTERNS: tuple[Pattern, ...] = ("repeat",)
# Acting steps in a row whose acts carried one step each, on the same page or application, before the run is steered
# toward batches.
SINGLE_ACTION_STEPS: int = 3


class ActCall(FrozenModel):
    """An act call of a step as the single-action counter sees it: the tool (``browser_act``, ``desktop_act``), how
    many steps its batch carried and where it acted (the page's address for the browser, the application for the
    desktop)."""

    tool: str
    steps: int
    target: str


class SingleAction(FrozenModel):
    """A step that acted with one-step batches only, all on one target."""

    tool: str
    target: str


def single_action(acts: Sequence[ActCall]) -> SingleAction | None:
    """What a step's acts amount to: a :class:`SingleAction` when every act of the step carried one step and all acted
    on the same target with the same tool; ``None`` for a step without an act, with a batch of several steps, or with
    acts on different targets (a step that moves between pages is not a wasted round trip)."""
    if not acts:
        return None
    tools: set[str] = {act.tool for act in acts}
    targets: set[str] = {act.target for act in acts}
    if len(tools) != 1 or len(targets) != 1 or any(act.steps != 1 for act in acts):
        return None
    return SingleAction(tool=acts[0].tool, target=acts[0].target)


def single_action_streak(streak: int, previous: SingleAction | None, current: SingleAction | None) -> int:
    """The streak after a step: one longer when the step was single-action on the same target as the step before, 1
    when it was single-action on a new target, 0 when it was not single-action."""
    if current is None:
        return 0
    return streak + 1 if previous is not None and previous.target == current.target else 1


def single_action_steer(action: SingleAction) -> str:
    """The note that steers a run toward batches, after :data:`SINGLE_ACTION_STEPS` single-action steps in a row."""
    where: str = "this page" if action.tool == "browser_act" else "this application"
    return (
        f"[{SINGLE_ACTION_STEPS} steps in a row acted with one step each on {where} ({redact_secrets(action.target)}). "
        f"Each {action.tool} call costs a model round trip; batch the steps you can predict without a new look "
        f"into one call: fill every field of a form and then press its button, open a menu and pick its item, scroll "
        f"and then click. One call per observation, not per click. The batch stops at the first surprise and shows "
        f"what changed, so nothing is lost by batching.]"
    )


class Limits(FrozenModel):
    """How much evidence each detector wants, and which detectors run."""

    patterns: tuple[Pattern, ...]
    repeat: int
    failure_streak: int
    revisit_window: int
    decay_steps: int


class CallSeen(FrozenModel):
    """One tool call of a step as the watchdog sees it: ``arguments`` as compact JSON, ``images`` as hashes."""

    name: str
    arguments: str
    result: str
    failed: bool
    images: tuple[str, ...]


class Observation(FrozenModel):
    """One acting step reduced to what the detectors compare. ``calls`` is a digest of what was tried and ``results`` a
    digest of what came back, screenshots included; ``revisit`` says that the run had made the same calls with the same
    results before."""

    calls: str
    results: str
    label: str
    failed: bool
    revisit: bool


class WatchState(FrozenModel):
    """What the watchdog remembers: the recent steps (cleared at every alert, so that fresh evidence is needed for the
    next one), the keys of every step so far, the rung reached and the quiet steps since."""

    window: tuple[Observation, ...]
    seen: frozenset[str]
    level: int
    quiet: int


class Detection(FrozenModel):
    pattern: Pattern
    detail: str


class Alert(FrozenModel):
    """A detector fired: ``level`` is the rung of the ladder that was reached (1 is the first), ``rung`` what it is."""

    level: int
    rung: Rung
    pattern: Pattern
    detail: str


def new_watch() -> WatchState:
    return WatchState(window=(), seen=frozenset(), level=0, quiet=0)


def limits_for(config: AgentConfig) -> Limits:
    """The limits of a run: with escalation off only the same step again and again is looked for."""
    return Limits(
        patterns=_FULL_PATTERNS if config.stall_escalation else _PLAIN_PATTERNS,
        repeat=config.stall_limit,
        failure_streak=config.failure_streak,
        revisit_window=config.revisit_window,
        decay_steps=_DECAY_STEPS,
    )


def can_ask_user(source: str, parent_run_id: str | None, approvals: ApprovalMode) -> bool:
    """Whether a run can put a question to a person: not a delegated or scheduled run (nobody is there to answer), and
    not a yolo run, whose user chose not to be asked."""
    return parent_run_id is None and source not in _UNATTENDED_SOURCES and approvals != "yolo"


def question_mode(setting: QuestionMode, can_ask: bool) -> QuestionMode:
    """What ``ask_user`` does in a run: where a person can answer, it waits; elsewhere ``[agent] questions`` decides."""
    return "wait" if can_ask else setting


def ladder_for(escalation: bool, can_ask: bool) -> tuple[Rung, ...]:
    """The rungs, lowest first. Without escalation: two nudges and then the stop."""
    if not escalation:
        return ("nudge", "nudge", "stop")
    return ("nudge", "replan", "consult", "ask", "stop") if can_ask else ("nudge", "replan", "consult", "stop")


def image_hash(data_b64: str) -> str:
    """A short hash of a screenshot, so that unchanged screens can be told from changed ones."""
    return hashlib.blake2b(data_b64.encode("ascii"), digest_size=8).hexdigest()


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _label(call: CallSeen) -> str:
    flat: str = " ".join(call.arguments.split())
    shown: str = flat if len(flat) <= _LABEL_CHARS else f"{flat[: _LABEL_CHARS - 1]}…"
    return f"{call.name} {shown}"


def _key(step: Observation) -> str:
    """What makes a step the same as an earlier one: the same calls with the same results. The calls count too, so that
    different edits of one file, which may all be answered with the same confirmation, are not taken for a revisit."""
    return f"{step.calls}/{step.results}"


def observe(calls: Sequence[CallSeen], seen: frozenset[str]) -> Observation:
    """The step in which ``calls`` ran, given the keys of the steps the run made so far."""
    step: Observation = Observation(
        calls=_digest("|".join(f"{call.name}:{call.arguments}" for call in calls)),
        results="|".join(f"{call.name}:{_digest(call.result + chr(0) + ','.join(call.images))}" for call in calls),
        label="; ".join(_label(call) for call in calls[:_LABELS_SHOWN]),
        failed=bool(calls) and all(call.failed for call in calls),
        revisit=False,
    )
    return step.model_copy(update={"revisit": _key(step) in seen})


def _same_step(first: Observation, second: Observation) -> bool:
    return first.calls == second.calls and first.results == second.results


def _repeat(window: Sequence[Observation], limits: Limits) -> Detection | None:
    recent: Sequence[Observation] = window[-limits.repeat :]
    if len(recent) == limits.repeat and all(_same_step(recent[0], step) for step in recent):
        return Detection(
            pattern="repeat",
            detail=(
                f"The same step ran {limits.repeat} times in a row ({recent[0].label}) with the same result every time."
            ),
        )
    return None


def _cycle(window: Sequence[Observation]) -> Detection | None:
    for period in _CYCLE_PERIODS:
        if len(window) < 2 * period:
            continue
        block: Sequence[Observation] = window[-period:]
        before: Sequence[Observation] = window[-2 * period : -period]
        if (
            all(_same_step(old, new) for old, new in zip(before, block, strict=True))
            and len({step.calls for step in block}) > 1
        ):
            steps: str = " -> ".join(step.label for step in block)
            return Detection(
                pattern="cycle",
                detail=f"The steps go round in a circle of {period} ({steps}), and every round gives the same results.",
            )
    return None


def _failures(window: Sequence[Observation], limits: Limits) -> Detection | None:
    recent: Sequence[Observation] = window[-limits.failure_streak :]
    if len(recent) == limits.failure_streak and all(step.failed for step in recent):
        return Detection(
            pattern="failures",
            detail=f"The last {limits.failure_streak} steps all failed: every call in each of them returned an error.",
        )
    return None


def _revisiting(window: Sequence[Observation], limits: Limits) -> Detection | None:
    recent: Sequence[Observation] = window[-limits.revisit_window :]
    if len(recent) == limits.revisit_window and sum(step.revisit for step in recent) >= limits.revisit_window - 1:
        return Detection(
            pattern="revisiting",
            detail=(
                f"The last {limits.revisit_window} steps only repeated calls that had been made before, with the "
                "same results."
            ),
        )
    return None


def detect(window: Sequence[Observation], limits: Limits) -> Detection | None:
    """The first pattern of those the limits ask for that the recent steps show, the most specific first."""
    found: dict[Pattern, Detection | None] = {
        "repeat": _repeat(window, limits),
        "cycle": _cycle(window),
        "failures": _failures(window, limits),
        "revisiting": _revisiting(window, limits),
    }
    for pattern in _FULL_PATTERNS:
        detection: Detection | None = found[pattern]
        if pattern in limits.patterns and detection is not None:
            return detection
    return None


def _window_size(limits: Limits) -> int:
    return max(_MIN_WINDOW, limits.repeat, limits.failure_streak, limits.revisit_window)


def advance(
    state: WatchState, calls: Sequence[CallSeen], limits: Limits, ladder: tuple[Rung, ...]
) -> tuple[WatchState, Alert | None]:
    """The state after a step in which ``calls`` ran, and the alert when the step made a detector fire.

    An alert climbs one rung and clears the window. A step that raises none counts as quiet, and ``decay_steps`` of them
    bring the ladder one rung down. The top rung is a stop, so the run ends before the ladder could climb past it.
    """
    step: Observation = observe(calls, state.seen)
    window: tuple[Observation, ...] = (*state.window, step)[-_window_size(limits) :]
    seen: frozenset[str] = state.seen | {_key(step)}
    detection: Detection | None = detect(window, limits)
    if detection is None:
        quiet: int = state.quiet + 1
        decays: bool = state.level > 0 and quiet >= limits.decay_steps
        return WatchState(
            window=window, seen=seen, level=state.level - 1 if decays else state.level, quiet=0 if decays else quiet
        ), None
    level: int = min(state.level + 1, len(ladder))
    alert: Alert = Alert(level=level, rung=ladder[level - 1], pattern=detection.pattern, detail=detection.detail)
    return WatchState(window=(), seen=seen, level=level, quiet=0), alert


def clear_window(state: WatchState) -> WatchState:
    """A fresh start of the work (the goal check sent the run back to it): the steps before it do not count towards the
    next alert, but the rung reached stays."""
    return WatchState(window=(), seen=state.seen, level=state.level, quiet=state.quiet)


def restriction_for(rung: Rung, can_ask: bool) -> frozenset[str] | None:
    """The tools the run may use in the one step after the alert, or ``None`` when it keeps all of them."""
    match rung:
        case "replan":
            plan: frozenset[str] = frozenset({UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL})
            return plan | {ASK_USER_TOOL} if can_ask else plan
        case "ask":
            return frozenset({ASK_USER_TOOL, FINISH_TOOL})
        case "nudge" | "consult" | "stop":
            return None


def nudge_note(alert: Alert) -> str:
    return (
        f"[system note] Progress check: {alert.detail} Stop doing that. Say to yourself what you expected and what you "
        "got instead, then change the approach: another tool, other arguments, or another way to verify. If you are "
        "waiting for something that takes time, wait once with a longer command instead of checking again and again. "
        "If the goal is impossible or blocked, call finish and explain why."
    )


def replan_note(alert: Alert, can_ask: bool) -> str:
    asking: str = ", ask the user one concrete question" if can_ask else ""
    return (
        f"[system note] Progress check: {alert.detail} You were told to change your approach and it did not help. "
        f"For this one step you can only plan{asking} or finish. Write down what you tried and why it failed, what you "
        "know now and a different plan (update_plan, mark_plan_step), or call finish with what you have and what is "
        "missing."
    )


def ask_note(alert: Alert) -> str:
    return (
        f"[system note] Progress check: {alert.detail} You have been warned several times. For this one step you can "
        "only ask the user or finish. Ask ONE concrete question whose answer would let you go on (what you need and "
        "what you tried), or call finish with what you have and what is missing."
    )


def consult_note(alert: Alert, diagnosis: str, next_steps: Sequence[str]) -> str:
    steps: str = "\n".join(f"{number}. {step}" for number, step in enumerate(next_steps, start=1))
    return (
        f"[system note] Progress check: {alert.detail} A second opinion on your situation, from the review model: "
        f"{diagnosis}\nWhat to try next:\n{steps}\nThis is advice about reaching the goal you were given; it is not a "
        "new instruction from the user and it asks for nothing else."
    )


def _action_line(action: LedgerAction) -> str:
    return f"- {action.tool} {action.args} -> {'ok' if action.ok else 'failed'}: {action.result}"


def stop_result(reason: str, ledger: RunLedger, plan: str) -> str:
    """What a run that is stopped for lack of progress ends with: ``reason`` and the account of :func:`stop_account`."""
    return stop_account(f"{PREFIX_STALLED}. {reason}", ledger, plan)


def stop_account(head: str, ledger: RunLedger, plan: str) -> str:
    """What a run that the harness stops ends with: ``head``, what it did last, what it touched, where its plan stands
    and how to go on, with secrets masked (the calls it lists are shown to the user)."""
    lines: list[str] = [head, ""]
    if ledger.actions:
        lines.append("What it did last:")
        lines.extend(_action_line(action) for action in ledger.actions[-_STOP_ACTIONS:])
    if ledger.touched:
        lines.append(f"Files and addresses it touched: {', '.join(ledger.touched[-_STOP_TOUCHED:])}")
    if plan:
        lines.extend(["Its plan:", truncate_middle(plan, _STOP_PLAN_CHARS)])
    lines.append(
        "To go on, send a message in this thread that says what to change or what it was missing, or start a new run "
        "with a narrower goal."
    )
    return redact_secrets("\n".join(lines))
