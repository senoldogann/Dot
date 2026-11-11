"""The week of the harness (item 988, docs/harness.md): a version proposed from the patterns of a week of real runs,
tried on them half and half, and ended by what the trial shows. The night calls :func:`week` after its digest; a run
takes its text from :func:`text_for_run` as it starts."""

import asyncio
import json
from collections.abc import Sequence

from dotagent.agent.review import HarnessProposal, ProposalRequest, UnusableAnswer
from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.harness.components import HarnessText
from dotagent.harness.digest import SOURCES_OUT, Digest, FailureRow, digest_of, night_digest
from dotagent.harness.propose import PROPOSER, bounds_of
from dotagent.harness.roles import proposer_of
from dotagent.harness.seat import trial_of
from dotagent.harness.service import adopt_replacing, adopted_text, create_version, retire
from dotagent.harness.trial import (
    DAY_S,
    PROPOSAL_EVERY_S,
    PROPOSED_KEY,
    TRIAL_KEY,
    Half,
    Judgement,
    Trial,
    failing_runs,
    halves_of,
    judge,
    pattern_lines,
    patterns_of,
    shown_signature,
    steering_words,
)
from dotagent.notify.owner import tell_owner
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, HarnessVersion, RunTally, ToolOutcome, TrialRun

log = get_logger(__name__)

# Where the owner reads the trial.
PAGE: str = "#/settings/harness"
# What the proposer is told it reads: the patterns of a week, not one run.
_GOAL: str = (
    "The owner's real runs of the last week: the ways a tool failed again and again, across chats and days. Write the"
    " habits or tool guidance that would keep them from failing so."
)


async def week(runtime: Runtime, now: float) -> str:
    """The night's word on the trial under way, ended when it is decided; or, with none and a week since the last
    proposal, a proposal from the week's patterns and its trial begun. ``""`` when there is nothing to say."""
    trial: Trial | None = await trial_of(runtime.store)
    if trial is not None:
        return await _weigh(runtime, trial, now)
    kept: str | None = await runtime.store.kv_get(PROPOSED_KEY)
    if kept and now - float(kept) < PROPOSAL_EVERY_S:
        return ""
    since: float = now - PROPOSAL_EVERY_S
    outcomes: list[ToolOutcome] = await runtime.store.tool_outcomes(since, now, SOURCES_OUT)
    tally: RunTally = await runtime.store.run_tally(since, now, SOURCES_OUT)
    digest: Digest = digest_of(outcomes, tally, since, now, runtime.schedule_zone)
    patterns: tuple[FailureRow, ...] = patterns_of(digest)
    if not patterns:
        await runtime.store.kv_set(PROPOSED_KEY, repr(now))
        return "no way a tool failed recurred across chats and days this week: nothing proposed"
    return await _propose(runtime, patterns, now)


async def _propose(runtime: Runtime, patterns: Sequence[FailureRow], now: float) -> str:
    lines: tuple[str, ...] = pattern_lines(patterns)
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
    current, parent = await adopted_text(runtime.store, held)
    request: ProposalRequest = ProposalRequest(
        goal=_GOAL,
        digest="\n".join(lines),
        signals=lines,
        answer="",
        reached_goal=False,
        habits=current.habits,
        tool_guidance=current.tool_guidance,
        experience=(),
        scope="domain",
        bounds=bounds_of(runtime.config.harness),
    )
    proposal: HarnessProposal = await proposer_of(runtime, "harness:week", None)(request)
    # The week is spent once the proposer answered: one that failed (a network error), or whose answer stayed unusable
    # (item 1015, :func:`night`), is asked again the next night.
    await runtime.store.kv_set(PROPOSED_KEY, repr(now))
    if proposal.keep:
        return "the proposer kept the habits as they are for this week's failures"
    text: HarnessText = HarnessText(
        habits=current.habits if proposal.habits is None else proposal.habits,
        tool_guidance=current.tool_guidance if proposal.tool_guidance is None else proposal.tool_guidance,
    )
    notes: str = json.dumps(
        {"from_week": list(lines), "scope": "domain", "moments": list(proposal.moments), "parent": parent},
        ensure_ascii=False,
    )
    version: HarnessVersion = await create_version(
        runtime.store, runtime.config.harness, text, proposal.summary, PROPOSER, notes, held
    )
    steering: tuple[str, ...] = steering_words(f"{text.habits}\n{text.tool_guidance}")
    if steering:
        await retire(runtime.store, version.id, now)
        log.info("harness_week_steering", version=version.id, words=list(steering))
        return f"a proposal named {', '.join(steering)}, which a habit must not steer runs to: set aside unused"
    signatures: tuple[str, ...] = tuple(shown_signature(row) for row in patterns)
    trial: Trial = Trial(version_id=version.id, started_at=now, signatures=signatures, in_use=parent)
    await runtime.store.kv_set(TRIAL_KEY, trial.model_dump_json())
    said: str = f"trying “{version.summary}” on half of the runs, for {'; '.join(signatures)}"
    await tell_owner(runtime.bus, "Dot is trying new habits", said, PAGE)
    log.info("harness_trial_started", version=version.id, signatures=list(signatures))
    return said


async def _weigh(runtime: Runtime, trial: Trial, now: float) -> str:
    store: Store = runtime.store
    try:
        version: HarnessVersion = await store.get_harness_version(trial.version_id)
    except NotFoundError:
        # Its version was deleted: the trial is over, or every night after would fail on it.
        await store.kv_delete(TRIAL_KEY)
        return "the trial ended: its version is gone"
    if version.state not in ("candidate", "probed"):
        await store.kv_delete(TRIAL_KEY)
        return f"the trial of “{version.summary}” ended: the version was {version.state}"
    runs: list[TrialRun] = await store.trial_runs(trial.version_id)
    ids: frozenset[str] = frozenset(run.run_id for run in runs)
    outcomes: list[ToolOutcome] = [
        outcome for outcome in await store.tool_outcomes(trial.started_at, now, SOURCES_OUT) if outcome.run_id in ids
    ]
    candidate, in_use = halves_of(runs, failing_runs(outcomes, trial.signatures))
    judgement: Judgement = judge(candidate, in_use, (now - trial.started_at) / DAY_S)
    said: str = f"the trial of “{version.summary}”: {judgement.why}"
    if judgement.verdict == "going":
        return said
    await store.kv_delete(TRIAL_KEY)
    if judgement.verdict == "better":
        await _keep_better(runtime, trial, candidate, in_use, now)
    else:
        await retire(store, trial.version_id, now)
    await tell_owner(runtime.bus, "Dot's trial of new habits ended", said, PAGE)
    log.info("harness_trial_ended", version=trial.version_id, verdict=judgement.verdict)
    return said


async def _keep_better(runtime: Runtime, trial: Trial, candidate: Half, in_use: Half, now: float) -> None:
    """A better candidate: adopted with ``adopt = "auto"`` (only over the version in use when the trial began), else
    kept tried for the owner to adopt."""
    store: Store = runtime.store
    if runtime.config.harness.adopt == "auto":
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
        if await adopt_replacing(store, trial.version_id, trial.in_use, now, held) is not None:
            return
    await store.set_harness_state(trial.version_id, "probed")
    log.info(
        "harness_trial_better", version=trial.version_id, candidate=candidate.model_dump(), in_use=in_use.model_dump()
    )


async def night(runtime: Runtime, now: float) -> HarnessStep:
    """The night of the harness: the digest of the day's real runs (:func:`night_digest`), then the week's word
    (:func:`week`), kept in the step's record (``week``). A week whose proposal failed fails the step with its error.
    One whose proposal stayed unusable after the proposer's attempts (item 1015: on 2026-11-06 Kimi K3 wrote a tool
    guidance of 1749 characters against 1500, three times) keeps the digest and records the step ``unusable``, with
    ``the proposal was unusable: <why>`` as the week's word and in ``unusable``; the week is not spent, so the next
    night asks again, as after a network error: a weak model's answer says nothing of the week's patterns, and asking
    costs three questions."""
    step: HarnessStep = await night_digest(runtime.store, runtime.config.harness, now, runtime.schedule_zone)
    try:
        said: str = await week(runtime, now)
    except UnusableAnswer as error:
        log.warning("harness_week_unusable", step=step.id, error=error.message)
        unusable: str = f"the proposal was unusable: {error.message}"
        kept: JsonObject = json.loads(step.outcome)
        kept["week"] = unusable
        kept["unusable"] = [unusable]
        return await runtime.store.amend_harness_step(step.id, now, "unusable", json.dumps(kept), None)
    except DotError as error:
        await runtime.store.amend_harness_step(step.id, now, "failed", step.outcome, error.message)
        raise
    if not said:
        return step
    record: JsonObject = json.loads(step.outcome)
    record["week"] = said
    return await runtime.store.amend_harness_step(step.id, now, step.state, json.dumps(record), None)
