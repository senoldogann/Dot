"""Routes for the harness (item 389, docs/harness.md): the versions of the texts a run's prompt takes from it, written
by the owner, adopted and retired."""

import asyncio
import time
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query

from dotagent.harness.components import NO_HARNESS, HarnessText
from dotagent.harness.graph import HarnessGraph, Lineage, graph_of, lineage_of
from dotagent.harness.headroom import ScoringPlan, pool_of
from dotagent.harness.schedule import StepSchedule, StepTimeRequest, schedule_of, set_schedule
from dotagent.harness.service import (
    OWNER,
    adopt,
    adopted,
    create_version,
    raw_text_of,
    refusal_words,
    refusals,
    retire,
    taint_of,
)
from dotagent.harness.step import scoring_of
from dotagent.secrets import known_secrets
from dotagent.server.context import ServerContext
from dotagent.server.views import HarnessQuery, HarnessRequest, HarnessView
from dotagent.store.db import Store
from dotagent.store.models import HarnessExperience, HarnessStep, HarnessVersion

# How many of the newest steps the graph and the list show.
_STEPS_SHOWN: int = 50
_EXPERIENCES_SHOWN: int = 200


def _refused_of(versions: Sequence[HarnessVersion], held: Sequence[str]) -> dict[str, str]:
    """The versions of ``versions`` whose texts the gate for learned texts refuses now, by id, with why in words
    (:func:`refusal_words`). Seven questions over up to six readings of each text of each version: work for a thread,
    not for the daemon's loop."""
    return {version.id: refusal_words(found) for version in versions if (found := refusals(raw_text_of(version), held))}


def harness_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def view(query: HarnessQuery) -> HarnessView:
        limits = ctx.runtime.config.harness
        # The values Dot holds are asked for in a stored text as when it was written (item 587).
        # (The values filled into a page are not among them: the gate refuses by a value, item 621.)
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
        counts: dict[str, int] = await store.count_harness_versions()
        current: HarnessVersion | None = await adopted(store)
        page: list[HarnessVersion] = await store.list_harness_versions(query.limit, query.offset)
        shown: list[HarnessVersion] = [*([] if current is None else [current]), *page]
        from_outside: dict[str, tuple[str, ...]] = {}
        for version in shown:
            reasons: tuple[str, ...] = await taint_of(store, version)
            if reasons:
                from_outside[version.id] = reasons
        # The gate is asked of every version listed: CPU work that grows with the history, kept off the loop that the
        # daemon's runs, its page and its phone share (item 588, pre-push review round 4).
        refused: dict[str, str] = await asyncio.to_thread(_refused_of, shown, held)
        scoring: ScoringPlan = await scoring_of(store, limits, current)
        schedule: StepSchedule = await schedule_of(store, limits)
        return HarnessView(
            adopted=current,
            versions=tuple(page),
            from_outside=from_outside,
            refused=refused,
            versions_total=sum(counts.values()),
            version_counts=counts,
            habits_chars=limits.habits_chars,
            tool_guidance_chars=limits.tool_guidance_chars,
            component_lines=limits.component_lines,
            step_time=schedule.step_time,
            step_time_set_on=schedule.source,
            step_minutes=limits.step_minutes,
            step_tokens=limits.step_tokens,
            main_model=ctx.runtime.config.models.main,
            scoring_tasks=tuple(limits.scoring_tasks),
            final_tasks=tuple(limits.final_tasks),
            pool_size=len(pool_of(limits)),
            headroom=scoring.headroom,
            untried=scoring.untried,
            headroom_least=limits.headroom_least,
            step_repeats=limits.step_repeats,
            step_margin=limits.step_margin,
            adopt=limits.adopt,
            keep_days=limits.keep_days,
            steps_total=await store.count_harness_steps(),
            experiences_total=await store.count_harness_experiences(),
        )

    @router.get("/harness")
    async def get_harness(query: Annotated[HarnessQuery, Query()]) -> HarnessView:
        """The adopted version, a page of the versions (the newest first: ``limit``, ``offset``) with how many there
        are in all and in each state, and how the nightly step is set."""
        return await view(query)

    # The fixed places first: a version id never reads "graph", "steps" or "experiences".
    @router.get("/harness/graph")
    async def get_graph() -> HarnessGraph:
        """The provenance of the harness (item 392): versions, the newest steps, experiences and the runs learned
        from, with what links them."""
        return await graph_of(store, _STEPS_SHOWN)

    @router.get("/harness/steps")
    async def get_steps() -> list[HarnessStep]:
        """The newest steps of the improvement loop, the newest first."""
        return await store.list_harness_steps(_STEPS_SHOWN)

    @router.get("/harness/experiences")
    async def get_experiences() -> list[HarnessExperience]:
        """The experience library, the newest first."""
        return await store.list_harness_experiences(_EXPERIENCES_SHOWN)

    @router.get("/harness/{version_id}")
    async def get_version(version_id: str) -> HarnessVersion:
        return await store.get_harness_version(version_id)

    @router.get("/harness/{version_id}/lineage")
    async def get_lineage(version_id: str) -> Lineage:
        """A version with its ancestors, its probes, the steps that touched it and the experiences about it."""
        return await lineage_of(store, version_id)

    @router.post("/harness")
    async def write_version(request: HarnessRequest) -> HarnessVersion:
        """A version of the owner's: each component given replaces the adopted version's, one left out is kept; with
        ``adopt`` it is adopted at once."""
        current: HarnessVersion | None = await adopted(store)
        kept: HarnessText = NO_HARNESS if current is None else raw_text_of(current)
        text: HarnessText = HarnessText(
            habits=kept.habits if request.habits is None else request.habits,
            tool_guidance=kept.tool_guidance if request.tool_guidance is None else request.tool_guidance,
        )
        # The secrets Dot holds are read off the event loop: the .env file is read for them.
        # A gate: the values filled into a page are not among the secrets it refuses by (item 621).
        secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=False)
        version: HarnessVersion = await create_version(
            store, ctx.runtime.config.harness, text, request.summary, OWNER, "", secrets
        )
        return await adopt(store, version.id, time.time(), secrets) if request.adopt else version

    @router.post("/harness/{version_id}/adopt")
    async def adopt_version(version_id: str) -> HarnessVersion:
        """Adopt a version; one whose text the gate for learned texts refuses now is not adopted (item 587)."""
        # A gate: the values filled into a page are not among the secrets it refuses by (item 621).
        return await adopt(store, version_id, time.time(), await asyncio.to_thread(known_secrets, filled=False))

    @router.put("/harness/schedule")
    async def put_schedule(request: StepTimeRequest) -> HarnessView:
        """When the nightly step runs (item 721): a time of day ("04:10") or "off", kept in the store, which wins over
        ``[harness] step_time``; the view as it is now."""
        await set_schedule(store, ctx.runtime.config.harness, request.step_time)
        return await view(HarnessQuery())

    @router.post("/harness/{version_id}/retire")
    async def retire_version(version_id: str) -> HarnessVersion:
        return await retire(store, version_id, time.time())

    return router
