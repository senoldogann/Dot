"""One step of the improvement loop (item 391, docs/harness.md): from the run of the owner's that went worst (item 720),
the proposer writes a candidate in each scope; the scoring set is played under the adopted texts and under each
candidate, within a budget the step stops before passing; the candidate that did better on a task and worse on none
survives, two that both did are merged by the composer and the merge is played in its turn; what each edit did is
written as experience; the probed version waits for the owner, or is adopted when ``[harness] adopt`` says so. The step
records what it did, and the probes run apart from the daemon."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence

from pydantic import JsonValue, TypeAdapter

from dotagent.agent.lessons import LessonCase
from dotagent.agent.reflection import wants_reflection
from dotagent.agent.review import (
    EXPERIENCE_TOOL,
    PROPOSE_TOOL,
    CandidateBrief,
    CompositionRequest,
    EditOutcome,
    ExperienceDraft,
    ExperienceRequest,
    HarnessComposition,
    ProposalScope,
    UnusableAnswer,
)
from dotagent.config import HarnessConfig
from dotagent.core.errors import DotError, InvalidValueError, ProviderProtocolError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn
from dotagent.harness.components import COMPONENTS, NO_HARNESS, GivenText, HarnessText
from dotagent.harness.contrast import (
    CONFIRM_FACTOR,
    Contrast,
    choose,
    confirm_contrast,
    contrast,
    contrast_detail,
    contrast_text,
)
from dotagent.harness.headroom import Attempt, ScoringPlan, plan_scoring, records_of
from dotagent.harness.own import own_tasks, task_taken, write_task
from dotagent.harness.probe import (
    BASE,
    CONFIRM_BASE,
    ProbeAsk,
    Prober,
    ProbeReport,
    Scorecard,
    confirm_label,
    nothing_played,
    over_budget,
)
from dotagent.harness.propose import bounds_of, case_of, propose_from_run, withheld_tasks
from dotagent.harness.roles import composer_of, experience_writer_of
from dotagent.harness.service import (
    adopt_replacing,
    adopted,
    check_adoptable,
    create_version,
    given_text,
    notes_of,
    notes_with,
    prune_records,
    step_facts,
)
from dotagent.llm.types import WHOLE_RUN_AGENTS, split_model_ref
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, HarnessStepState, HarnessVersion, RunRecord

log = get_logger(__name__)

# Who made a version the composer wrote.
COMPOSER: str = "composer"
# What the owner did with the survivor of a step while it ran, in the words of its decision (item 588).
_OWNER_WORDS: dict[str, str] = {"adopted": "in use", "retired": "set aside"}
# How far back the step looks for a run to learn from at most: from the last step that took one, or this far when none
# did since (item 719: nights that only try the pool take none, and the runs meanwhile wait for the next that proposes).
_LOOKBACK_S: float = 7 * 24 * 3600.0
_SESSION: str = "harness:step"
# What the roles answer under `dotx harness step --scripted` (:func:`scripted_role_turns`): the proposer proposes this
# habit in the first scope and keeps in the others, the experience writer saw no effect, and the composer is never
# asked (one candidate), so the step plays the whole probe path at no model's cost and ends with the base kept.
SCRIPTED_HABIT: str = "- Read a file before you change it (the scripted check of the loop)."
_MOMENTS: TypeAdapter[list[str]] = TypeAdapter(list[str])


class StepPlan(FrozenModel):
    """What a step plays: the scoring set ``repeats`` times against ``model`` (the references through the scripted
    model when ``scripted``), the scopes proposed, and the budget of minutes and tokens of the whole step."""

    tasks: tuple[str, ...]
    repeats: int
    scopes: tuple[ProposalScope, ...]
    minutes: float
    tokens: int
    model: str
    scripted: bool


def plan_of(config: HarnessConfig, model: str, scripted: bool) -> StepPlan:
    return StepPlan(
        tasks=tuple(config.scoring_tasks),
        repeats=config.step_repeats,
        scopes=tuple(config.step_candidates),
        minutes=config.step_minutes,
        tokens=config.step_tokens,
        model=SCRIPTED_MODEL if scripted else model,
        scripted=scripted,
    )


def learns_from(run: RunRecord) -> bool:
    """Whether a step may learn from ``run``: one the reflection reads (a task or research run of its own that ended)
    that Dot's own agent worked. A run on one of Zen's free models goes whole to OpenCode's own agent, which takes no
    habits, no tool guidance, no notes and no skills of Dot's (item 589), so what it did says nothing of them and its
    tools are not Dot's; the reflection still reads it, since its journal, what Dot knows about the owner and its
    lessons are of the owner's day. So it is of a run on the owner's Claude plan, which goes whole to Claude Code (item
    1013). A model reference no provider of Dot's answers to (a run of a time before a provider
    was renamed) is no OpenCode run: it is read as it was."""
    ref: tuple[str, str] | None = split_model_ref(run.model)
    return wants_reflection(run) and (ref is None or ref[0] not in WHOLE_RUN_AGENTS)


async def _why_not_adopted(store: Store, version_id: str, held: Sequence[str]) -> str:
    """Why the step's own adoption of ``version_id``, after its confirmation, did not happen, in the words of the
    step's decision: the owner adopted or set the version aside during the confirmation, the gate for learned texts
    refuses its text now, or the version in use is no longer the one the step compared it with."""
    version: HarnessVersion = await store.get_harness_version(version_id)
    if version.state in _OWNER_WORDS:
        return f"{version.id} is {_OWNER_WORDS[version.state]}, as you left it while the step ran"
    try:
        check_adoptable(version, held)
    except InvalidValueError:
        return "left to you: the gate for learned texts refuses its text now"
    return "left to you: what is in use changed while the step ran"


# What a step says when no run is left to write a task from (item 931).
NO_CLEAN_RUN: str = "no task: no run left that read only what is Dot's and the owner's and gave no task yet"
# How many of the newest runs a step may learn from are weighed for the one that went worst (item 720).
RUNS_WEIGHED: int = 30
# How badly a run ended, worst highest: one that failed, then one that was stopped or held, then the rest.
_ENDED_BADLY: dict[str, int] = {"failed": 2, "cancelled": 1, "blocked": 1}


async def pick_run(store: Store, since: float) -> RunRecord | None:
    """Of the newest :data:`RUNS_WEIGHED` runs of the owner's that ended after ``since`` and that a step may learn from
    (:func:`learns_from`), the one that went worst (item 720): one that failed before one that was stopped or held,
    before the rest, then the one with the most tool calls that failed, then the newest. A run that went well teaches
    little, and the proposer kept the harness or wrote a habit nothing tested. ``None`` when there is none."""
    runs: list[RunRecord] = sorted(
        (run for run in await store.runs_finished_since(since) if learns_from(run)),
        key=lambda run: run.finished_at or 0.0,
    )[-RUNS_WEIGHED:]
    errors: dict[str, int] = await store.tool_errors_of([run.id for run in runs])
    return max(
        runs,
        key=lambda run: (_ENDED_BADLY.get(run.status, 0), errors.get(run.id, 0), run.finished_at or 0.0),
        default=None,
    )


async def pick_clean_run(store: Store, since: float) -> RunRecord | None:
    """The run a task is written from (item 931): of the runs :func:`pick_run` weighs, the one that went worst among
    those that read no content third parties can author and that no task was written or tried from yet. The run the
    step learns from is the worst of all, and on the owner's Mac it had always read a page or a mail (80 of 97 runs of a
    week did), so a task was never written (item 904 writes none from such a run: what it read could shape the task a
    model then plays with a run's rights). ``None`` when there is none."""
    runs: list[RunRecord] = sorted(
        (run for run in await store.runs_finished_since(since) if learns_from(run)),
        key=lambda run: run.finished_at or 0.0,
    )[-RUNS_WEIGHED:]
    clean: list[RunRecord] = [
        run
        for run in runs
        if not await store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
        and not await task_taken(store, run.id)
    ]
    errors: dict[str, int] = await store.tool_errors_of([run.id for run in clean])
    return max(
        clean,
        key=lambda run: (_ENDED_BADLY.get(run.status, 0), errors.get(run.id, 0), run.finished_at or 0.0),
        default=None,
    )


def _cards(report: ProbeReport) -> list[tuple[object, ...]]:
    return [
        (
            card.task_id,
            card.attempt,
            card.passed,
            card.status,
            card.cut,
            card.steps,
            card.input_tokens,
            card.output_tokens,
            card.tool_errors,
            card.seconds,
            card.approvals_asked,
            card.checks_passed,
            card.checks_total,
        )
        for card in report.cards
    ]


def _changed(base: HarnessText, candidate: HarnessText) -> tuple[str, ...]:
    return tuple(name for name in COMPONENTS if base.component(name) != candidate.component(name))


class _Outcome(FrozenModel):
    """What a step came to: its state, its facts, the error of what failed (a probe that did not start, a role that
    could not be reached), which leaves the step ``failed`` with the survivor probed all the same, and the roles whose
    answer stayed unusable after their attempts (``unusable``, item 1015), which the step went on without. The
    experience writer's failure of another kind is a warning of the facts instead (item 931), which leaves the step as
    the probes decided it but adopts nothing on its own."""

    state: HarnessStepState
    facts: dict[str, JsonValue]
    error: str | None
    unusable: tuple[str, ...]


def state_of(outcome: _Outcome) -> HarnessStepState:
    """The state a step is recorded with (item 1015): ``failed`` when something failed; ``unusable`` when a role's
    answer stayed unusable and the step made no change (a night that kept the base or tried the pool without it); else
    the state the step came to, ``done`` among them, whose record still names the unusable answers. A weak model's
    answer that the checks refuse is the checks at work, not a failure of the night: on 2026-10-28 to 11-06 one such
    answer made 7 nights of 10 read as failed, and the rest of each night was lost with it."""
    if outcome.error is not None:
        return "failed"
    if outcome.unusable and outcome.state != "done":
        return "unusable"
    return outcome.state


def _unusable(answer: str, error: UnusableAnswer) -> str:
    """How the record says that ``answer`` (``the task writer's answer``) stayed unusable after its attempts (item
    1015), and why."""
    return f"{answer} was unusable: {error.message}"


def adopts_on_its_own(adopt: str, survivor: Contrast, repeats: int, tainted: bool, *, failed: bool = False) -> bool:
    """Whether the step adopts the survivor itself: ``[harness] adopt = "auto"``, a contrast better on a task and worse
    on none over at least two repeats, a run that had read no content third parties can author (``tainted``: a text
    written from such a run is left to the owner, whatever its score), and a step that did not fail (``failed``: a role
    that failed after the probes leaves the step recorded as failed, and a failed step adopts nothing, item 588)."""
    return (
        adopt == "auto" and survivor.worse == 0 and survivor.better > 0 and repeats >= 2 and not tainted and not failed
    )


async def _retire(store: Store, versions: Sequence[HarnessVersion], now: float, why: str) -> None:
    """Retire ``versions`` with ``why`` added to their notes as the store holds them now (a probe wrote to them since
    they were read). A version that is no longer a candidate or a probed one, because the owner adopted it or set it
    aside while the step ran, is left as they chose (item 588)."""
    for version in versions:
        if await store.retire_unadopted_harness(version.id, now):
            current: HarnessVersion = await store.get_harness_version(version.id)
            await store.set_harness_notes(version.id, notes_with(current.notes, {"retired": why}))


async def _carry_out(
    runtime: Runtime,
    step: HarnessStep,
    plan: StepPlan,
    base: HarnessVersion | None,
    scoring: ScoringPlan,
    prober: Prober,
    now: float,
    task_run: str | None = None,
) -> _Outcome:
    """The step ``step`` under ``base``, the version in use when it began, which ``scoring`` was planned for;
    ``task_run`` is the run a task is written from (items 904 and 931), the worst clean one, which it does not take."""
    store: Store = runtime.store
    config: HarnessConfig = runtime.config.harness
    # The values Dot holds, which a probe's own home cannot know: a stored text is played, or shown to a role, only as
    # :func:`given_text` leaves it (the gate with them), so that what is scored is what a run would take (item 587).
    # (The values filled into a page are not among them: a gate refuses by a value, item 621. A text is given with
    # them masked, :func:`given_text`.)
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
    base_text: GivenText = NO_HARNESS if base is None else given_text(base, held)
    errors: list[str] = []
    # What went wrong without changing the step's decision (item 931): shown with it, never as its failure.
    warnings: list[str] = []
    # The roles whose answer stayed unusable after their attempts (item 1015): the step goes on without it, says so in
    # its record and on the page, and adopts nothing on its own.
    unusable: list[str] = []
    # The tasks the loop wrote from the owner's runs (item 904), which a probe is given whole.
    own: dict[str, EvalTask] = await own_tasks(store)
    # Probe: the base first, then each candidate within what is left of the budget; a probe with nothing left plays
    # nothing, and its candidate comes out unclear.
    started: float = time.monotonic()
    spent: int = 0

    async def played(
        label: str, version: HarnessVersion | None, tasks: tuple[str, ...], repeats: int = plan.repeats
    ) -> ProbeReport:
        """``version`` (``None``: Dot's own text alone) played as ``label`` ``repeats`` times on each of ``tasks``,
        within what is left of the budget. What is played is its text as :func:`given_text` leaves it, never the text
        as stored: this is the only way a version reaches a probe in a step."""
        nonlocal spent
        ask: ProbeAsk = ProbeAsk(
            label=label,
            text=NO_HARNESS if version is None else given_text(version, held),
            tasks=tasks,
            repeats=repeats,
            model=plan.model,
            scripted=plan.scripted,
            max_minutes=max(0.0, plan.minutes - (time.monotonic() - started) / 60.0),
            max_tokens=max(0, plan.tokens - spent),
            own=tuple(own[task] for task in tasks if task in own),
        )
        report: ProbeReport = nothing_played(ask) if over_budget(ask, 0, 0.0, 0) is not None else await prober(ask)
        spent += report.tokens
        # An attempt that never started is the probe's failure, not the text's (item 717): the step says so and fails.
        lost: list[Scorecard] = [card for card in report.cards if not card.started]
        if lost:
            errors.append(f"{len(lost)} attempt(s) of {label} did not start: {lost[0].failure}")
        await store.add_harness_probes(
            step.id, None if version is None else version.id, label, _cards(report), time.time()
        )
        return report

    # Until enough tasks have headroom (item 719), the base alone tries a few tasks of the pool it knows least and
    # nothing is proposed.
    if not scoring.ready:
        calibrated: ScoringPlan = scoring
        task_note: str | None = None if plan.scripted else NO_CLEAN_RUN
        if task_run is not None:
            try:
                written, task_note = await write_task(runtime, task_run, prober)
            except UnusableAnswer as exc:
                log.warning("harness_task_unusable", step=step.id, error=exc.message)
                unusable.append(_unusable("the task writer's answer", exc))
                written, task_note = None, "no task: the writer's answer was unusable"
            except DotError as exc:
                log.warning("harness_task_failed", step=step.id, error=exc.message)
                errors.append(f"the task writer failed: {exc.message}")
                written, task_note = None, "no task: the writer failed"
            if written is not None:
                own[written.id] = written
                calibrated = scoring.model_copy(update={"calibrated": (written.id, *scoring.calibrated)})
        outcome: _Outcome = await _calibrate(calibrated, config, lambda tasks: played(BASE, base, tasks), errors)
        outcome = outcome.model_copy(
            update={"unusable": tuple(unusable), "facts": {**outcome.facts, "unusable": list(unusable)}}
        )
        if task_note is None:
            return outcome
        return outcome.model_copy(update={"facts": {**outcome.facts, "task_run": task_run, "task": task_note}})
    from_run: str | None = step.from_run
    if from_run is None:
        return _Outcome(
            state="skipped",
            facts={"reason": "no run of the owner's ended since the last step that took one, a week back at most"},
            error=None,
            unusable=(),
        )
    # A run as a task (item 904): a candidate is scored where an owner's run went wrong, which the tasks of the pool,
    # passed under any text, cannot show. The run is the worst that read only what is Dot's and the owner's (item
    # 931), not always the one the candidates answer. A scripted check writes none (its roles answer from a script that
    # holds no task); a writer that fails leaves the step to go on, failed in the end, as a proposer does.
    learned: EvalTask | None = None
    learned_note: str = "no task: a scripted check writes none"
    if not plan.scripted and task_run is None:
        learned_note = NO_CLEAN_RUN
    elif not plan.scripted and task_run is not None:
        try:
            learned, learned_note = await write_task(runtime, task_run, prober)
        except UnusableAnswer as exc:
            log.warning("harness_task_unusable", step=step.id, error=exc.message)
            unusable.append(_unusable("the task writer's answer", exc))
            learned_note = "no task: the writer's answer was unusable"
        except DotError as exc:
            log.warning("harness_task_failed", step=step.id, error=exc.message)
            errors.append(f"the task writer failed: {exc.message}")
            learned_note = "no task: the writer failed"
    if learned is not None:
        own[learned.id] = learned
    # Nothing tells a better text from luck (item 931): no task was written tonight, none the loop wrote has headroom,
    # and every task of the pool with headroom passes in more than three quarters of its attempts. The step proposes and
    # plays nothing then, and takes no run: it is marked ``no_signal``, which :func:`_since_last_step` passes over, so
    # that the run that went worst is still there for the next step that proposes. A task writer that failed tonight
    # still fails the step, so that the night never reads clean (the review); one whose answer was unusable makes it
    # ``unusable`` (item 1015).
    if not plan.scripted and learned is None and not scoring.hard and not any(task in own for task in scoring.headroom):
        left: str = "" if from_run is None else f"; {from_run} is left for a step that proposes"
        return _Outcome(
            state="keep",
            facts={
                "from_run": from_run,
                "no_signal": True,
                "task": learned_note,
                "decision": (
                    "nothing was proposed: no task tells a better text from luck (every task with headroom passes in "
                    f"more than three quarters of its attempts, and none was written from a run){left}"
                ),
                "unusable": list(unusable),
            },
            error="; ".join(errors) or None,
            unusable=tuple(unusable),
        )
    # Propose. A proposer whose answer is no proposal in one scope (the review module asked it three times already)
    # leaves the other scopes to be proposed and played (item 718): on 2026-10-24 one such answer lost the night. The
    # step records the answer as unusable (item 1015) and adopts nothing on its own.
    candidates: list[HarnessVersion] = []
    proposals_unusable: int = 0
    for scope in plan.scopes:
        try:
            candidate: HarnessVersion | None = await propose_from_run(runtime, from_run, scope)
        except UnusableAnswer as exc:
            log.warning("harness_proposal_unusable", step=step.id, scope=scope, error=exc.message)
            unusable.append(_unusable(f"the proposer's answer in the {scope} scope", exc))
            proposals_unusable += 1
            continue
        except ProviderProtocolError as exc:
            # A provider's broken reply (not the model's answer) is a failure, as before item 1015; the other scopes
            # are still proposed (item 718).
            log.warning("harness_proposal_failed", step=step.id, scope=scope, error=exc.message)
            errors.append(f"the proposer failed in the {scope} scope: {exc.message}")
            continue
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        reason: str = "the proposer's answer was unusable" if proposals_unusable else "the proposer kept the harness"
        return _Outcome(
            state="keep",
            facts={"from_run": from_run, "reason": reason, "task": learned_note, "unusable": list(unusable)},
            error="; ".join(errors) or None,
            unusable=tuple(unusable),
        )
    case: LessonCase = await case_of(runtime, from_run)

    async def write_experience(probed: HarnessVersion | None) -> None:
        try:
            await _write_experience(runtime, step, from_run, base_text, candidates, contrasts, probed, held)
        except UnusableAnswer as exc:
            # An answer that stayed unusable is recorded as such (item 1015), and no experience is kept from it.
            log.warning("harness_experience_unusable", step=step.id, error=exc.message)
            unusable.append(_unusable("the experience writer's answer", exc))
        except DotError as exc:
            # The record of the night, not its decision (item 931): a writer whose answer was malformed leaves the
            # step as the probes decided it, with a warning, rather than failed (on 2026-11-02 it made a night that
            # had kept the base read as failed).
            log.warning("harness_experience_failed", step=step.id, error=exc.message)
            warnings.append(f"the experience writer failed: {exc.message}")

    # The task written tonight is scored on first: it is where an owner's run went wrong.
    scored: tuple[str, ...] = scoring.scored if learned is None else (learned.id, *scoring.scored)
    base_report: ProbeReport = await played(BASE, base, scored)
    contrasts: list[Contrast] = []
    by_label: dict[str, HarnessVersion] = {}

    async def contrasted(candidate: HarnessVersion) -> Contrast:
        found: Contrast = contrast(base_report, await played(candidate.id, candidate, scored), config.step_margin)

        contrasts.append(found)
        by_label[candidate.id] = candidate
        await store.set_harness_notes(
            candidate.id, notes_with(candidate.notes, {"contrast": contrast_detail(found), "step": step.id})
        )
        return found

    for candidate in candidates:
        await contrasted(candidate)

    def facts_now() -> dict[str, JsonValue]:
        return {
            "from_run": from_run,
            "candidates": [candidate.id for candidate in candidates],
            "contrasts": {found.label: contrast_detail(found) for found in contrasts},
            "tokens": spent,
            "tainted": case.tainted,
            "errors": list(errors),
            "warnings": list(warnings),
            "unusable": list(unusable),
            "scored_on": list(scored),
            "task": learned_note,
        }

    # Select; when two candidates both did better, the composer merges them and the merge is probed in its turn.
    chosen: Contrast | None = choose(contrasts)
    if chosen is None:
        # A candidate whose attempts did not start was not judged, and is set aside saying so (item 717).
        unjudged: set[str] = {found.label for found in contrasts if found.not_started}
        await _retire(
            store,
            [candidate for candidate in candidates if candidate.id not in unjudged],
            time.time(),
            "no better than the base on the scoring set",
        )
        await _retire(
            store,
            [candidate for candidate in candidates if candidate.id in unjudged],
            time.time(),
            "not judged: attempts of the probe did not start",
        )
        await write_experience(None)
        # A candidate that could not be told from a lucky attempt, or whose probe was cut short, says why (item 589).
        unclear: str = "; ".join(f"{found.label} is unclear: {found.why}" for found in contrasts if found.why)
        return _Outcome(
            state="keep",
            facts={
                **facts_now(),
                "decision": f"the base is kept: no candidate did better{f' ({unclear})' if unclear else ''}",
            },
            error="; ".join(errors) or None,
            unusable=tuple(unusable),
        )
    winners: list[Contrast] = [found for found in contrasts if found.verdict == "better"]
    survivor: Contrast = chosen
    decision: str = f"{chosen.label} did better on {chosen.better} and worse on none"
    if len(winners) > 1:
        composed: HarnessVersion | None
        try:
            composed = await _compose(
                runtime, step, from_run, base_text, [by_label[found.label] for found in winners], winners, held
            )
        except UnusableAnswer as exc:
            log.warning("harness_composer_unusable", step=step.id, error=exc.message)
            unusable.append(_unusable("the composer's answer", exc))
            composed = None
            decision = f"{decision}; the composer's answer was unusable, so the best candidate stands"
        except DotError as exc:
            log.warning("harness_composer_failed", step=step.id, error=exc.message)
            errors.append(f"the composer failed: {exc.message}")
            composed = None
            decision = f"{decision}; the composer failed, so the best candidate stands"
        else:
            if composed is None:
                decision = f"{decision}; the composer kept the base"
        if composed is not None:
            candidates.append(composed)
            merged: Contrast = await contrasted(composed)
            if merged.verdict == "better":
                survivor = merged
                decision = (
                    f"{composed.id} composed of {', '.join(found.label for found in winners)} did better on "
                    f"{merged.better} and worse on none"
                )
            else:
                decision = f"{decision}; the composition {composed.id} came out {merged.verdict}"
    # The probes took long (up to `step_minutes`): the owner may have adopted or set aside a version since (item 588).
    # The survivor and the versions it beat change state only while they are candidates or probed ones, and an
    # adoption of the step's own replaces only the version it compared them with.
    probed: HarnessVersion = await store.set_harness_state(survivor.label, "probed")
    decided: bool = probed.state != "probed"
    if decided:
        decision = f"{decision}; {probed.id} is {_OWNER_WORDS[probed.state]}, as you left it while the step ran"
    await _retire(
        store,
        [candidate for candidate in candidates if candidate.id != probed.id],
        time.time(),
        f"beaten by {probed.id} on the scoring set",
    )
    await write_experience(probed)
    adopted_now: bool = False
    confirmation: str | None = None

    async def confirm() -> Contrast:
        """The base and the survivor played again on new attempts, :data:`CONFIRM_FACTOR` times as many as the step's
        own: luck gave a text that changes nothing a better score in a step in six to twenty-seven, and gives it twice
        in a row in one in fifty. Recorded in the survivor's notes (the owner reads the tasks there) and in the step."""
        again: int = plan.repeats * CONFIRM_FACTOR
        base_again: ProbeReport = await played(CONFIRM_BASE, base, scored, again)
        survivor_again: ProbeReport = await played(confirm_label(probed.id), probed, scored, again)
        found: Contrast = confirm_contrast(base_again, survivor_again, config.step_margin)
        current: HarnessVersion = await store.get_harness_version(probed.id)
        await store.set_harness_notes(probed.id, notes_with(current.notes, {"confirmation": contrast_detail(found)}))
        return found

    # The owner already decided about the survivor while the step ran: nothing is left to adopt or to leave to them.
    # A warning (the experience writer's failure) adopts nothing on its own either, as a failure does (item 588), nor
    # does an answer that stayed unusable (item 1015).
    if not decided and adopts_on_its_own(
        config.adopt, survivor, plan.repeats, case.tainted, failed=bool(errors) or bool(warnings) or bool(unusable)
    ):
        sure: Contrast | None = None
        try:
            sure = await confirm()
        except DotError as exc:
            log.warning("harness_confirmation_failed", step=step.id, error=exc.message)
            errors.append(f"the confirmation failed: {exc.message}")
            decision = f"{decision}; left to you: the confirmation on new attempts failed"
        if sure is not None:
            confirmation = contrast_detail(sure)
            if sure.verdict == "better":
                decision = f"{decision}; confirmed on new attempts ({contrast_text(sure)})"
                replaced: HarnessVersion | None = await adopt_replacing(
                    store, probed.id, None if base is None else base.id, time.time(), held
                )
                adopted_now = replaced is not None
                if not adopted_now:
                    decision = f"{decision}; {await _why_not_adopted(store, probed.id, held)}"
            else:
                decision = (
                    f"{decision}; left to you: it was not better again on new attempts, which a better text is "
                    f"({contrast_text(sure)})"
                )
    elif not decided and config.adopt == "auto":
        if case.tainted:
            decision = f"{decision}; left to you: the run had read content others can author"
        elif errors:
            decision = f"{decision}; left to you: the step failed after the probes"
        elif warnings:
            decision = f"{decision}; left to you: {warnings[0]}"
        elif unusable:
            decision = f"{decision}; left to you: {unusable[0]}"
    facts: dict[str, JsonValue] = {**facts_now(), "decision": decision, "probed": probed.id}
    if confirmation is not None:
        facts["confirmation"] = confirmation
    if adopted_now:
        facts["adopted"] = True
    return _Outcome(state="done", facts=facts, error="; ".join(errors) or None, unusable=tuple(unusable))


async def scoring_of(store: Store, config: HarnessConfig, base: HarnessVersion | None) -> ScoringPlan:
    """Where a step under ``base`` (``None``: Dot's own text alone) scores and what its base tries (item 719), from the
    attempts of that version that started."""
    attempts: list[Attempt] = [
        Attempt(task_id=probe.task_id, passed=probe.passed, at=probe.created_at)
        for probe in await store.started_attempts_of(None if base is None else base.id)
    ]
    return plan_scoring(config, records_of(attempts, config.headroom_window), tuple(await own_tasks(store)))


async def _calibrate(
    scoring: ScoringPlan,
    config: HarnessConfig,
    tries: Callable[[tuple[str, ...]], Awaitable[ProbeReport]],
    errors: list[str],
) -> _Outcome:
    """A step with too few tasks with headroom known for a candidate to be judged (item 719): no proposer is asked, and
    the base tries the tasks of the pool it knows least; or nothing at all when every task of the pool is known and
    too few have headroom, which the decision says."""
    known: str = (
        f"{len(scoring.headroom)} task(s) with headroom known, {config.headroom_least} needed to judge a candidate"
    )
    if not scoring.calibrated:
        return _Outcome(
            state="keep",
            facts={
                "headroom": list(scoring.headroom),
                "decision": f"nothing to learn on the pool: {known}, and every task of it is known",
            },
            error=None,
            unusable=(),
        )
    await tries(scoring.calibrated)
    return _Outcome(
        state="calibrating",
        facts={
            "calibrated": list(scoring.calibrated),
            "headroom": list(scoring.headroom),
            "decision": (
                f"trying the pool before proposing: {known}; {len(scoring.calibrated)} tried tonight, "
                f"{scoring.untried} not tried yet"
            ),
        },
        error="; ".join(errors) or None,
        unusable=(),
    )


async def _compose(
    runtime: Runtime,
    step: HarnessStep,
    from_run: str,
    base_text: GivenText,
    winners: Sequence[HarnessVersion],
    found: Sequence[Contrast],
    held: Sequence[str],
) -> HarnessVersion | None:
    """One version of the winning candidates by the composer, written as a candidate for the step to probe in its turn;
    ``None`` when the composer keeps the base, which leaves the best candidate as the survivor. A composer that fails
    raises its error, which the step records. The composer is shown the texts of the candidates as :func:`given_text`
    leaves them (``held`` are the values Dot holds), and the composed text is written with the same values asked."""
    run: RunRecord = await runtime.store.get_run(from_run)
    shown: list[GivenText] = [given_text(candidate, held) for candidate in winners]
    briefs: tuple[CandidateBrief, ...] = tuple(
        CandidateBrief(
            number=number,
            scope=_scope_of(candidate),
            summary=candidate.summary,
            habits=text.habits,
            tool_guidance=text.tool_guidance,
            moments=_moments_of(candidate),
            contrast=contrast_text(each),
        )
        for number, (candidate, text, each) in enumerate(zip(winners, shown, found, strict=True), start=1)
    )
    request: CompositionRequest = CompositionRequest(
        goal=run.goal,
        habits=base_text.habits,
        tool_guidance=base_text.tool_guidance,
        candidates=briefs,
        bounds=bounds_of(runtime.config.harness),
    )
    composition: HarnessComposition = await composer_of(runtime, _SESSION, from_run)(request)
    if composition.keep_base:
        return None
    composed: HarnessVersion = await create_version(
        runtime.store,
        runtime.config.harness,
        HarnessText(habits=composition.habits, tool_guidance=composition.tool_guidance),
        composition.summary,
        COMPOSER,
        json.dumps(
            {
                "from_run": from_run,
                "step": step.id,
                "composed_of": [candidate.id for candidate in winners],
                # Taint is the OR of the parts (item 588): a composition of text a page may have written is such text,
                # and what its parts read is what it says.
                "tainted": any(notes_of(candidate).get("tainted") is True for candidate in winners),
                "tainted_by": sorted(_read_by(winners)),
                "decisions": [decision.model_dump() for decision in composition.decisions],
            },
            ensure_ascii=False,
        ),
        held,
    )
    return composed


def _read_by(versions: Sequence[HarnessVersion]) -> set[str]:
    """What the runs the ``versions`` were written from read (``tainted_by`` of their notes), together."""
    found: set[str] = set()
    for version in versions:
        read: JsonValue = notes_of(version).get("tainted_by")
        if isinstance(read, list):
            found |= {item for item in read if isinstance(item, str)}
    return found


def _scope_of(version: HarnessVersion) -> ProposalScope:
    """The scope a candidate was proposed in; a version with none in its notes (the owner's) counts as invariant."""
    return "domain" if notes_of(version).get("scope") == "domain" else "invariant"


def _moments_of(version: HarnessVersion) -> tuple[str, ...]:
    """The moments of the run a proposal named, none for a version of the owner's."""
    return tuple(_MOMENTS.validate_python(notes_of(version).get("moments", [])))


def scripted_role_turns(scopes: int) -> tuple[ScriptedTurn, ...]:
    """The scripted model's replies for the roles of one step that proposes in ``scopes`` scopes: a proposal of
    :data:`SCRIPTED_HABIT`, keep for each further scope, and what the experience writer saw."""

    def call(tool: str, arguments: dict[str, JsonValue]) -> ScriptedTurn:
        return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))

    proposal: ScriptedTurn = call(
        PROPOSE_TOOL,
        {
            "keep": False,
            "habits": SCRIPTED_HABIT,
            "tool_guidance": None,
            "moments": ["the scripted check of the loop"],
            "summary": "Scripted check: read first",
        },
    )
    keep: ScriptedTurn = call(
        PROPOSE_TOOL, {"keep": True, "habits": None, "tool_guidance": None, "moments": [], "summary": "keep"}
    )
    experience: ScriptedTurn = call(
        EXPERIENCE_TOOL,
        {
            "effects": [{"edit": 1, "effect": "no_effect", "why": "the references play the same under both texts"}],
            "entry": None,
        },
    )
    return (proposal, *([keep] * max(0, scopes - 1)), experience)


async def _write_experience(
    runtime: Runtime,
    step: HarnessStep,
    from_run: str,
    base_text: GivenText,
    candidates: Sequence[HarnessVersion],
    contrasts: Sequence[Contrast],
    probed: HarnessVersion | None,
    held: Sequence[str],
) -> None:
    """What each candidate's edit did, by the experience writer, and the experience it kept, if any; a writer that
    fails raises its error, which the step records. The components a candidate changed are told by the texts as
    :func:`given_text` leaves them, as the base is."""
    by_label: dict[str, Contrast] = {found.label: found for found in contrasts}
    edits: tuple[EditOutcome, ...] = tuple(
        EditOutcome(
            component=", ".join(_changed(base_text, given_text(candidate, held))) or "nothing",
            change=candidate.summary,
            observed=contrast_text(by_label[candidate.id]) if candidate.id in by_label else "not probed",
        )
        for candidate in candidates
    )
    if not edits:
        return
    # The run's taint goes with the experience (item 392): one written from a run that read content third parties can
    # author is kept marked and never shown to the proposer, as a tainted note is never shown to the lesson writer.
    case: LessonCase = await case_of(runtime, from_run)
    request: ExperienceRequest = ExperienceRequest(
        goal=case.goal,
        edits=edits,
        adopted=probed is not None,
        summary=f"step {step.id}: {len(candidates)} candidate(s) probed against the base",
        withheld=withheld_tasks(runtime.config.harness),
    )
    draft: ExperienceDraft = await experience_writer_of(runtime, _SESSION, from_run)(request)
    if draft.entry is not None:
        await runtime.store.add_harness_experience(
            step.id,
            None if probed is None else probed.id,
            draft.entry.cue,
            draft.entry.lesson,
            draft.entry.evidence,
            draft.entry.confidence,
            case.tainted,
        )
    log.info("harness_experience_written", step=step.id, entry=draft.entry is not None)


# How many of the newest steps that took a run are read for the one that counts (not a scripted check).
_RECENT_STEPS: int = 20


async def _since_last_step(store: Store, now: float) -> float:
    """When the newest step that learned from a run began, and never further back than :data:`_LOOKBACK_S` from
    ``now``: a scripted check (`--scripted`) is recorded like any step but does not move the window the next step
    learns from, nor does a step that took no run (item 719: one that only tried the pool, whatever it came to, or
    found none to learn from), nor one that proposed nothing for want of a task to tell a better text by (item 931,
    ``no_signal``), so that the run that went worst meanwhile is still there for the next step that
    proposes. One rule bounds it: a week, whether the last step that took a run was longer ago or there was none."""
    oldest: float = now - _LOOKBACK_S
    for earlier in await store.harness_steps_with_run(oldest, _RECENT_STEPS):
        facts: dict[str, JsonValue] = step_facts(earlier)
        if facts.get("scripted") is not True and facts.get("no_signal") is not True:
            return earlier.started_at
    return oldest


async def run_step(runtime: Runtime, now: float, from_run: str | None, prober: Prober, scripted: bool) -> HarnessStep:
    """One step at ``now``, learning from ``from_run`` or from the run of the owner's that went worst (item 720) since
    the last step, its probes played by ``prober`` (apart from the daemon, or in this process for a process that runs
    nothing else); ``scripted`` plays the references through the scripted model, which checks the step without a model's
    cost. The step is recorded whatever happens; a failure is raised after it is."""
    store: Store = runtime.store
    plan: StepPlan = plan_of(runtime.config.harness, runtime.config.models.main, scripted)
    base: HarnessVersion | None = await adopted(store)
    # Where the texts are scored (item 719): the guards, the tasks the version in use still fails sometimes and a few it
    # passes. A scripted check scores on the guards alone, since the references pass everything and no task would ever
    # have headroom. A store that cannot be read for it still leaves the step recorded, as failed.
    try:
        scoring: ScoringPlan = (
            ScoringPlan(scored=plan.tasks, calibrated=(), headroom=(), untried=0, ready=True, hard=plan.tasks)
            if plan.scripted
            else await scoring_of(store, runtime.config.harness, base)
        )
    except Exception as exc:
        unplanned: HarnessStep = await store.create_harness_step(now, None, None if base is None else base.id)
        why: str = exc.message if isinstance(exc, DotError) else f"{type(exc).__name__}: {exc}"
        await store.finish_harness_step(unplanned.id, time.time(), "failed", "{}", why)
        raise
    # A step that only tries the pool learns from no run and takes none, not even one named by hand, which its decision
    # says: the run that went worst is left to the first step that proposes (item 719, pre-push review rounds 1 to 3).
    left_for_later: str | None = None
    # The run a task is written from (items 904 and 931), on any step: the worst run that read nothing third parties
    # can author, which the step does not take. A pool every task of which is passed would leave the loop nothing to
    # propose on for good, and the tasks written from the owner's runs are what gives it some.
    task_run: str | None = None
    if not plan.scripted:
        clean: RunRecord | None = await pick_clean_run(store, await _since_last_step(store, now))
        task_run = None if clean is None else clean.id
    if not scoring.ready:
        left_for_later, from_run = from_run, None
    elif from_run is None:
        since: float = await _since_last_step(store, now)
        picked: RunRecord | None = await pick_run(store, since)
        from_run = None if picked is None else picked.id
    step: HarnessStep = await store.create_harness_step(now, from_run, None if base is None else base.id)
    log.info("harness_step_started", step=step.id, from_run=from_run, tasks=len(plan.tasks), repeats=plan.repeats)
    try:
        outcome: _Outcome = await _carry_out(runtime, step, plan, base, scoring, prober, now, task_run)
    except DotError as exc:
        await store.finish_harness_step(step.id, time.time(), "failed", "{}", exc.message)
        raise
    except Exception as exc:
        await store.finish_harness_step(step.id, time.time(), "failed", "{}", f"{type(exc).__name__}: {exc}")
        raise
    facts: dict[str, JsonValue] = {**outcome.facts, **({"scripted": True} if plan.scripted else {})}
    if left_for_later is not None:
        facts["decision"] = f"{facts.get('decision', '')}; {left_for_later} is left for a step that proposes"
        facts["left_for_later"] = left_for_later
    finished: HarnessStep = await store.finish_harness_step(
        step.id,
        time.time(),
        state_of(outcome),
        json.dumps(facts, ensure_ascii=False),
        outcome.error,
    )
    await prune_records(store, runtime.config.harness, now)
    return finished
