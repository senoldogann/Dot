"""One step of the improvement loop (item 391, docs/harness.md): from the newest run of the owner's that ended, the
proposer writes a candidate in each scope; the scoring set is played under the adopted texts and under each candidate,
within a budget the step stops before passing; the candidate that did better on a task and worse on none survives, two
that both did are merged by the composer and the merge is played in its turn; what each edit did is written as
experience; the probed version waits for the owner, or is adopted when ``[harness] adopt`` says so. The step records
what it did, and the probes run apart from the daemon."""

import asyncio
import json
import time
from collections.abc import Sequence

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
)
from dotagent.config import HarnessConfig
from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.harness.components import COMPONENTS, NO_HARNESS, HarnessText
from dotagent.harness.contrast import Contrast, choose, contrast, contrast_detail, contrast_text
from dotagent.harness.probe import BASE, ProbeAsk, Prober, ProbeReport, nothing_played, over_budget
from dotagent.harness.propose import bounds_of, case_of, propose_from_run, withheld_tasks
from dotagent.harness.roles import composer_of, experience_writer_of
from dotagent.harness.service import (
    adopt_replacing,
    adopted,
    create_version,
    notes_of,
    notes_with,
    step_facts,
    text_of,
)
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, HarnessStepState, HarnessVersion, RunRecord

log = get_logger(__name__)

# Who made a version the composer wrote.
COMPOSER: str = "composer"
# What the owner did with the survivor of a step while it ran, in the words of its decision (item 588).
_OWNER_WORDS: dict[str, str] = {"adopted": "in use", "retired": "set aside"}
# How far back the step looks for a run to learn from when no step came before.
_LOOKBACK_S: float = 24 * 3600.0
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


async def pick_run(store: Store, since: float) -> RunRecord | None:
    """The newest run of the owner's that ended after ``since`` and that a reflection would read (a task or research
    run of its own that ended), or ``None``."""
    runs: list[RunRecord] = [run for run in await store.runs_finished_since(since) if wants_reflection(run)]
    return max(runs, key=lambda run: run.finished_at or 0.0, default=None)


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
        )
        for card in report.cards
    ]


def _changed(base: HarnessText, candidate: HarnessText) -> tuple[str, ...]:
    return tuple(name for name in COMPONENTS if base.component(name) != candidate.component(name))


class _Outcome(FrozenModel):
    """What a step came to: its state, its facts, and the error of a role that failed after the probes (the composer,
    the experience writer), which leaves the step ``failed`` with the survivor probed all the same."""

    state: HarnessStepState
    facts: dict[str, JsonValue]
    error: str | None


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


async def _carry_out(runtime: Runtime, step: HarnessStep, plan: StepPlan, prober: Prober, now: float) -> _Outcome:
    store: Store = runtime.store
    config: HarnessConfig = runtime.config.harness
    base: HarnessVersion | None = await adopted(store)
    base_text: HarnessText = NO_HARNESS if base is None else text_of(base)
    from_run: str | None = step.from_run
    if from_run is None:
        return _Outcome(
            state="skipped", facts={"reason": "no run of the owner's ended since the last step"}, error=None
        )
    # Propose.
    candidates: list[HarnessVersion] = []
    for scope in plan.scopes:
        candidate: HarnessVersion | None = await propose_from_run(runtime, from_run, scope)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return _Outcome(
            state="keep", facts={"from_run": from_run, "reason": "the proposer kept the harness"}, error=None
        )
    case: LessonCase = await case_of(runtime, from_run)
    errors: list[str] = []

    async def write_experience(probed: HarnessVersion | None) -> None:
        try:
            await _write_experience(runtime, step, from_run, base_text, candidates, contrasts, probed)
        except DotError as exc:
            log.warning("harness_experience_failed", step=step.id, error=exc.message)
            errors.append(f"the experience writer failed: {exc.message}")

    # Probe: the base first, then each candidate within what is left of the budget; a probe with nothing left plays
    # nothing, and its candidate comes out unclear.
    started: float = time.monotonic()
    spent: int = 0

    async def played(label: str, text: HarnessText, version_id: str | None) -> ProbeReport:
        nonlocal spent
        ask: ProbeAsk = ProbeAsk(
            label=label,
            text=text,
            tasks=plan.tasks,
            repeats=plan.repeats,
            model=plan.model,
            scripted=plan.scripted,
            max_minutes=max(0.0, plan.minutes - (time.monotonic() - started) / 60.0),
            max_tokens=max(0, plan.tokens - spent),
        )
        report: ProbeReport = nothing_played(ask) if over_budget(ask, 0, 0.0, 0) is not None else await prober(ask)
        spent += report.tokens
        await store.add_harness_probes(step.id, version_id, label, _cards(report), time.time())
        return report

    base_report: ProbeReport = await played(BASE, base_text, None if base is None else base.id)
    contrasts: list[Contrast] = []
    by_label: dict[str, HarnessVersion] = {}

    async def contrasted(candidate: HarnessVersion) -> Contrast:
        found: Contrast = contrast(base_report, await played(candidate.id, text_of(candidate), candidate.id))
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
        }

    # Select; when two candidates both did better, the composer merges them and the merge is probed in its turn.
    chosen: Contrast | None = choose(contrasts)
    if chosen is None:
        await _retire(store, candidates, time.time(), "no better than the base on the scoring set")
        await write_experience(None)
        return _Outcome(
            state="keep",
            facts={**facts_now(), "decision": "the base is kept: no candidate did better"},
            error="; ".join(errors) or None,
        )
    winners: list[Contrast] = [found for found in contrasts if found.verdict == "better"]
    survivor: Contrast = chosen
    decision: str = f"{chosen.label} did better on {chosen.better} and worse on none"
    if len(winners) > 1:
        composed: HarnessVersion | None
        try:
            composed = await _compose(
                runtime, step, from_run, base_text, [by_label[found.label] for found in winners], winners
            )
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
    # The owner already decided about the survivor while the step ran: nothing is left to adopt or to leave to them.
    if not decided and adopts_on_its_own(config.adopt, survivor, plan.repeats, case.tainted, failed=bool(errors)):
        replaced: HarnessVersion | None = await adopt_replacing(
            store, probed.id, None if base is None else base.id, time.time(), await asyncio.to_thread(known_secrets)
        )
        adopted_now = replaced is not None
        if not adopted_now:
            decision = f"{decision}; left to you: what is in use changed while the step ran"
    elif not decided and config.adopt == "auto":
        if case.tainted:
            decision = f"{decision}; left to you: the run had read content others can author"
        elif errors:
            decision = f"{decision}; left to you: the step failed after the probes"
    facts: dict[str, JsonValue] = {**facts_now(), "decision": decision, "probed": probed.id}
    if adopted_now:
        facts["adopted"] = True
    return _Outcome(state="done", facts=facts, error="; ".join(errors) or None)


async def _compose(
    runtime: Runtime,
    step: HarnessStep,
    from_run: str,
    base_text: HarnessText,
    winners: Sequence[HarnessVersion],
    found: Sequence[Contrast],
) -> HarnessVersion | None:
    """One version of the winning candidates by the composer, written as a candidate for the step to probe in its turn;
    ``None`` when the composer keeps the base, which leaves the best candidate as the survivor. A composer that fails
    raises its error, which the step records."""
    run: RunRecord = await runtime.store.get_run(from_run)
    briefs: tuple[CandidateBrief, ...] = tuple(
        CandidateBrief(
            number=number,
            scope=_scope_of(candidate),
            summary=candidate.summary,
            habits=candidate.habits,
            tool_guidance=candidate.tool_guidance,
            moments=_moments_of(candidate),
            contrast=contrast_text(each),
        )
        for number, (candidate, each) in enumerate(zip(winners, found, strict=True), start=1)
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
        known_secrets(),
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
    base_text: HarnessText,
    candidates: Sequence[HarnessVersion],
    contrasts: Sequence[Contrast],
    probed: HarnessVersion | None,
) -> None:
    """What each candidate's edit did, by the experience writer, and the experience it kept, if any; a writer that
    fails raises its error, which the step records."""
    by_label: dict[str, Contrast] = {found.label: found for found in contrasts}
    edits: tuple[EditOutcome, ...] = tuple(
        EditOutcome(
            component=", ".join(_changed(base_text, text_of(candidate))) or "nothing",
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


# How many of the newest steps are read for the one that counts (not a scripted check) when the window is set.
_RECENT_STEPS: int = 20


async def _since_last_step(store: Store, now: float) -> float:
    """When the newest step that counts began, or the lookback from ``now``: a scripted check (`--scripted`) is recorded
    like any step but does not move the window the next step learns from."""
    for earlier in await store.list_harness_steps(_RECENT_STEPS):
        if step_facts(earlier).get("scripted") is not True:
            return earlier.started_at
    return now - _LOOKBACK_S


async def run_step(runtime: Runtime, now: float, from_run: str | None, prober: Prober, scripted: bool) -> HarnessStep:
    """One step at ``now``, learning from ``from_run`` or from the newest run of the owner's that ended since the last
    step, its probes played by ``prober`` (apart from the daemon, or in this process for a process that runs nothing
    else); ``scripted`` plays the references through the scripted model, which checks the step without a model's
    cost. The step is recorded whatever happens; a failure is raised after it is."""
    store: Store = runtime.store
    plan: StepPlan = plan_of(runtime.config.harness, runtime.config.models.main, scripted)
    if from_run is None:
        since: float = await _since_last_step(store, now)
        picked: RunRecord | None = await pick_run(store, since)
        from_run = None if picked is None else picked.id
    base: HarnessVersion | None = await adopted(store)
    step: HarnessStep = await store.create_harness_step(now, from_run, None if base is None else base.id)
    log.info("harness_step_started", step=step.id, from_run=from_run, tasks=len(plan.tasks), repeats=plan.repeats)
    try:
        outcome: _Outcome = await _carry_out(runtime, step, plan, prober, now)
    except DotError as exc:
        await store.finish_harness_step(step.id, time.time(), "failed", "{}", exc.message)
        raise
    except Exception as exc:
        await store.finish_harness_step(step.id, time.time(), "failed", "{}", f"{type(exc).__name__}: {exc}")
        raise
    facts: dict[str, JsonValue] = {**outcome.facts, **({"scripted": True} if plan.scripted else {})}
    return await store.finish_harness_step(
        step.id,
        time.time(),
        "failed" if outcome.error is not None else outcome.state,
        json.dumps(facts, ensure_ascii=False),
        outcome.error,
    )
