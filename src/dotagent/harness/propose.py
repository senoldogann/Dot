"""A candidate from what one run did (item 390, docs/harness.md): the proposer reads the run as the lesson writer does
(its digest, what went wrong on the way, its answer), the texts of the harness as they are and the experience of
earlier steps, and writes a candidate version, or keeps. The scoring set that judges a candidate is never among what
it reads."""

import json

from dotagent.agent.lessons import LessonCase, stored_signals
from dotagent.agent.reflection import stored_case
from dotagent.agent.review import HarnessBounds, HarnessProposal, ProposalRequest, ProposalScope
from dotagent.config import HarnessConfig
from dotagent.core.logs import get_logger
from dotagent.harness.components import HarnessText
from dotagent.harness.roles import proposer_of
from dotagent.harness.service import adopted_text, create_version
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, HarnessVersion, RunRecord

log = get_logger(__name__)

# Who made a version the proposer wrote.
PROPOSER: str = "proposer"
# The newest tool results of a run the proposer's digest is drawn from, as the reflection draws a lesson's.
RESULTS_READ: int = 60
# How many of the newest experiences the proposer is shown (item 392): the index of the library.
EXPERIENCES_SHOWN: int = 12


def bounds_of(config: HarnessConfig) -> HarnessBounds:
    return HarnessBounds(
        habits_chars=config.habits_chars,
        tool_guidance_chars=config.tool_guidance_chars,
        component_lines=config.component_lines,
    )


async def case_of(runtime: Runtime, run_id: str) -> LessonCase:
    """The run ``run_id`` as the proposer reads it: the lesson case the reflection would build of it."""
    run: RunRecord = await runtime.store.get_run(run_id)
    events: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", RESULTS_READ)
    tainted: bool = await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
    return stored_case(run, events, tainted)


def notes_of(case: LessonCase, scope: ProposalScope, proposal: HarnessProposal, parent: str | None) -> str:
    """What a candidate's notes keep of its proposal: the run and its scope, the moments behind the change, whether the
    run had read content third parties can author, and the version it was written from."""
    return json.dumps(
        {
            "from_run": case.run_id,
            "scope": scope,
            "moments": list(proposal.moments),
            "tainted": case.tainted,
            "parent": parent,
        },
        ensure_ascii=False,
    )


async def experience_lines(store: Store, limit: int) -> tuple[str, ...]:
    """The index of the experience library as the proposer reads it: the newest ``limit`` experiences, a line each of
    the cue, the lesson and the confidence, without the ones written from a run that had read content third parties
    can author (``tainted``, as a tainted note is kept from the lesson writer, item 384). The secrets Dot holds are
    masked with the rest of the question (:mod:`dotagent.agent.review`)."""
    return tuple(
        f"{experience.cue}: {experience.lesson} ({experience.confidence})"
        for experience in await store.list_harness_experiences(limit)
        if not experience.tainted
    )


async def propose_from_run(runtime: Runtime, run_id: str, scope: ProposalScope) -> HarnessVersion | None:
    """Ask the proposer for a candidate from the run ``run_id`` in ``scope``, and write it as a candidate version made
    from the adopted one; ``None`` when the proposer keeps the harness as it is. A proposer that fails raises its
    error."""
    case: LessonCase = await case_of(runtime, run_id)
    signals: tuple[str, ...] = await stored_signals(runtime.store, case)
    current, parent = await adopted_text(runtime.store)
    request: ProposalRequest = ProposalRequest(
        goal=case.goal,
        digest=case.digest,
        signals=signals,
        answer=case.answer,
        reached_goal=case.reached_goal,
        habits=current.habits,
        tool_guidance=current.tool_guidance,
        experience=await experience_lines(runtime.store, EXPERIENCES_SHOWN),
        scope=scope,
        bounds=bounds_of(runtime.config.harness),
    )
    proposal: HarnessProposal = await proposer_of(runtime, f"{case.thread_id}:harness", run_id)(request)
    if proposal.keep:
        log.info("harness_proposal_keep", run_id=run_id, scope=scope)
        return None
    text: HarnessText = HarnessText(
        habits=current.habits if proposal.habits is None else proposal.habits,
        tool_guidance=current.tool_guidance if proposal.tool_guidance is None else proposal.tool_guidance,
    )
    version: HarnessVersion = await create_version(
        runtime.store,
        runtime.config.harness,
        text,
        proposal.summary,
        PROPOSER,
        notes_of(case, scope, proposal, parent),
        known_secrets(),
    )
    log.info("harness_proposed", run_id=run_id, scope=scope, version=version.id, moments=len(proposal.moments))
    return version
