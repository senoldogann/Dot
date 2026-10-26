"""Routes for what Dot keeps: rules for the policy, memories and schedules."""

import time
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import regex
from fastapi import APIRouter, Query

from dotagent.agent.learning_health import LearningHealth, quiet_limit, read_learning_health, stall_reason
from dotagent.agent.review import RuleDrafter, RuleWordsRequest, ToolBrief, rule_drafter_for
from dotagent.config import SchedulerConfig
from dotagent.core.errors import InvalidValueError, NotFoundError, RunConflict
from dotagent.events import SYSTEM_RUN_ID
from dotagent.memory.lifecycle import (
    Decided,
    ForgetProposal,
    Unmerged,
    delete_for_good,
    forget_proposals,
    forget_proposed,
    keep_proposed,
    pin_memory,
    rested_since,
    restore_note,
    trash_of,
    trash_total,
    undo_merge,
    unpin_memory,
)
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    OwnerSection,
    SavedMemory,
    forget_memory,
    owner_section,
    recall,
    recent_scores,
    resting,
    save_memory,
    set_owner_section,
    update_memory_note,
)
from dotagent.policy.drafting import DraftedRule
from dotagent.proactive.brief import read_state
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.plans import (
    ScheduleRequest,
    create_schedule,
    create_schedule_off,
    disable_schedule,
    edit_schedule,
    enable_schedule,
    remove_schedule,
    schedule_order,
    schedule_timing,
    schedule_zone,
)
from dotagent.scheduler.timing import format_local
from dotagent.server.context import ServerContext
from dotagent.server.rule_replay import candidate_rule, preview_rule, rules_usage
from dotagent.server.rule_shadow import rule_views
from dotagent.server.views import (
    FiringsQuery,
    ForgetProposalsView,
    ForgetProposalView,
    LearningHealthView,
    MemoriesQuery,
    MemoryPage,
    MemoryRequest,
    NoteIdsRequest,
    OwnerLinesRequest,
    PinRequest,
    ProposalOutcome,
    RuleDraftRequest,
    RuleEnabledRequest,
    RuleMoveRequest,
    RulePreview,
    RulePreviewRequest,
    RuleRequest,
    RulesUsage,
    RulesUsageQuery,
    RuleView,
    ScheduleAgent,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
    ScheduleView,
    SearchQuery,
    TrashedNote,
    TrashView,
    UnmergeView,
)
from dotagent.store.db import Store
from dotagent.store.models import (
    AgentRecord,
    MemoryRecord,
    MemoryScore,
    RuleRecord,
    RunRecord,
    ScheduleFiring,
    ScheduleOptions,
    ScheduleRecord,
)


def _checked_pattern(pattern: str) -> str:
    try:
        regex.compile(pattern)
    except regex.error as exc:
        raise InvalidValueError(
            "the pattern is not a valid regular expression", {"pattern": pattern, "error": str(exc)}
        ) from exc
    return pattern


async def _rule(store: Store, rule_id: str) -> RuleRecord:
    for rule in await store.list_rules():
        if rule.id == rule_id:
            return rule
    raise NotFoundError("rule not found", {"rule_id": rule_id})


# How a ``datetime-local`` input writes a moment.
_INPUT_MOMENT: str = "%Y-%m-%dT%H:%M"
# Tells the pages that a rule was added, switched or deleted (a live event of the lifecycle stream), so that every page
# that shows the rules reads them again.
RULES_CHANGED_EVENT: str = "rules_changed"


def _schedule_view(
    record: ScheduleRecord, zone: ZoneInfo, agents: dict[str, AgentRecord], brief_id: str | None
) -> ScheduleView:
    """``record`` as the UI shows it, with the agent of ``agents`` its runs run as (item 462); ``brief_id`` is the
    schedule that is the morning brief (item 570)."""
    agent: AgentRecord | None = None if record.options.agent_id is None else agents.get(record.options.agent_id)
    return ScheduleView(
        record=record,
        when=schedule_timing(record, zone),
        next_local=None if record.next_run_at is None else format_local(record.next_run_at, zone),
        last_local=None if record.last_run_at is None else format_local(record.last_run_at, zone),
        run_at_input=None
        if record.run_at is None
        else datetime.fromtimestamp(record.run_at, tz=zone).strftime(_INPUT_MOMENT),
        zone=zone.key,
        agent=None
        if agent is None
        else ScheduleAgent(
            id=agent.id, name=agent.name, look=agent.look, approvals=agent.approvals, paused_at=agent.paused_at
        ),
        brief=record.id == brief_id,
    )


def rules_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    async def tell_rules_changed() -> None:
        await ctx.runtime.bus.emit(SYSTEM_RUN_ID, RULES_CHANGED_EVENT, {}, False)

    @router.get("/rules")
    async def list_rules() -> list[RuleView]:
        return rule_views(await store.list_rules())

    @router.post("/rules", status_code=201)
    async def add_rule(request: RuleRequest) -> RuleRecord:
        rule: RuleRecord = await store.add_rule(
            request.behavior, request.tool_glob, _checked_pattern(request.match_regex), request.description
        )
        await tell_rules_changed()
        return rule

    @router.get("/rules/usage")
    async def usage(query: Annotated[RulesUsageQuery, Query()]) -> RulesUsage:
        """How each enabled rule was used on the steps of the last days, with today's rules (item 473): nothing is
        written."""
        return await rules_usage(store, await store.list_rules(), query.days, time.time())

    @router.post("/rules/preview")
    async def preview(request: RulePreviewRequest) -> RulePreview:
        """What a rule would have done on the steps of the last days (item 469), before it is added: nothing is written
        and nothing decided."""
        existing: list[RuleRecord] = await store.list_rules()
        candidate: RuleRecord = candidate_rule(
            request.behavior, request.tool_glob, _checked_pattern(request.match_regex), existing
        )
        return await preview_rule(store, candidate, existing, request.days, time.time())

    @router.post("/rules/draft")
    async def draft_rule(request: RuleDraftRequest) -> DraftedRule:
        """A rule drafted by the review model from the owner's words, checked against the tools Dot has, with the
        sentence it reads back as for its description; nothing is kept until the owner adds it. Words that make no
        rule are answered 422 with the model's reason."""
        drafter: RuleDrafter = rule_drafter_for(
            ctx.runtime.router,
            ctx.runtime.roles.review(),
            ctx.runtime.config.agent.model_call_timeout_s,
            f"rules:{int(time.time())}",
        )
        tools: tuple[ToolBrief, ...] = tuple(
            ToolBrief(name=name, description=entry.spec.description)
            for name, entry in sorted(ctx.runtime.registry.tools.items())
        )
        drafted: DraftedRule = await drafter(RuleWordsRequest(sentence=request.sentence, tools=tools))
        _checked_pattern(drafted.match_regex)
        return drafted

    @router.patch("/rules/{rule_id}")
    async def set_rule_enabled(rule_id: str, request: RuleEnabledRequest) -> RuleRecord:
        await _rule(store, rule_id)
        await store.set_rule_enabled(rule_id, request.enabled)
        await tell_rules_changed()
        return await _rule(store, rule_id)

    @router.post("/rules/{rule_id}/move")
    async def move_rule(rule_id: str, request: RuleMoveRequest) -> list[RuleView]:
        """Move a rule one place up or down the order the policy reads the rules in (item 449); 422 past either end."""
        await store.move_rule(rule_id, -1 if request.direction == "up" else 1)
        await tell_rules_changed()
        return rule_views(await store.list_rules())

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str) -> RuleRecord:
        rule: RuleRecord = await _rule(store, rule_id)
        await store.delete_rule(rule_id)
        await tell_rules_changed()
        return rule

    return router


def memories_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    index: MemoryIndex = ctx.runtime.memory

    @router.get("/memories")
    async def list_memories(query: Annotated[MemoriesQuery, Query()]) -> MemoryPage:
        items: list[MemoryRecord] = await index.store.list_memories(query.limit, query.offset, query.order)
        now: float = time.time()
        ids: list[str] = [record.id for record in items]
        recent: dict[str, MemoryScore] = await recent_scores(index, ids, now)
        resting_ids: list[str] = [
            record.id
            for record in items
            if resting(record, now, index.config.rest_days, recent.get(record.id), index.config.score_min_uses)
        ]
        dated: dict[str, float] = {}
        for record in items:
            since: float | None = rested_since(record, now, index.config.rest_days)
            if record.id in resting_ids and since is not None:
                dated[record.id] = since
        return MemoryPage(
            total=await index.store.count_memories(),
            items=items,
            resting=resting_ids,
            rested=dated,
            scores=await index.store.memory_scores(ids, 0.0),
            agents={agent.id: agent.name for agent in await index.store.list_agents()},
        )

    @router.get("/memories/proposals")
    async def memory_proposals() -> ForgetProposalsView:
        """The notes that have rested for ``[memory] forget_after_days``, the longest first (item 584): nothing is
        forgotten until the owner says so (``POST /memories/proposals/forget``)."""
        found: list[ForgetProposal] = await forget_proposals(index, time.time())
        return ForgetProposalsView(
            items=[
                ForgetProposalView(record=proposal.record, rested_since=proposal.rested_since) for proposal in found
            ],
            forget_after_days=index.config.forget_after_days,
            trash_days=index.config.trash_days,
        )

    @router.post("/memories/proposals/forget")
    async def forget_proposed_notes(request: NoteIdsRequest) -> ProposalOutcome:
        """Forget the notes of ``ids`` that are still proposed: they go to the trash, where they wait ``[memory]
        trash_days`` and can be restored; the others are left alone and named in ``skipped``."""
        decided: Decided = await forget_proposed(index, request.ids, time.time())
        return ProposalOutcome(done=list(decided.done), skipped=list(decided.skipped))

    @router.post("/memories/proposals/keep")
    async def keep_proposed_notes(request: NoteIdsRequest) -> ProposalOutcome:
        """Keep the notes of ``ids``: they wake, written anew, and are proposed again only after as long a rest."""
        decided: Decided = await keep_proposed(index, request.ids, time.time())
        return ProposalOutcome(done=list(decided.done), skipped=list(decided.skipped))

    @router.get("/memories/trash")
    async def memory_trash() -> TrashView:
        """The notes the owner forgot, the newest first, with when each is deleted for good."""
        return await _trash_view()

    async def _trash_view() -> TrashView:
        days: int = index.config.trash_days
        return TrashView(
            items=[
                TrashedNote(
                    record=found.record, trashed_at=found.trashed_at, purged_at=found.trashed_at + days * 86400.0
                )
                for found in await trash_of(index)
            ],
            total=await trash_total(index),
            trash_days=days,
        )

    @router.post("/memories/trash/{memory_id}/restore")
    async def restore_memory_note(memory_id: str) -> MemoryRecord:
        """Bring a forgotten note back from the trash, awake (404 for one that is not there)."""
        return await restore_note(index, memory_id, time.time())

    @router.delete("/memories/trash/{memory_id}")
    async def delete_trashed_note(memory_id: str) -> TrashView:
        """Delete a forgotten note for good, at the owner's word (404 for one that is not in the trash); the trash after
        it."""
        if not await delete_for_good(index, memory_id):
            raise NotFoundError("the note is not in the trash", {"memory_id": memory_id})
        return await _trash_view()

    @router.get("/memories/owner")
    async def get_owner_section() -> OwnerSection:
        """What Dot knows about the owner: the reflection's lines and the owner's own."""
        return await owner_section(index.store)

    @router.put("/memories/owner")
    async def set_owner_lines(request: OwnerLinesRequest) -> OwnerSection:
        """Replace the owner's own lines; the reflection's lines stay, and the next reflection keeps the owner's."""
        before: OwnerSection = await owner_section(index.store)
        section: OwnerSection = OwnerSection(
            dot_lines=before.dot_lines, owner_lines=tuple(request.lines), updated_at=time.time()
        )
        await set_owner_section(index.store, section)
        return section

    @router.get("/memories/learning")
    async def learning_health() -> LearningHealthView:
        """How Dot's learning fares: when the reflection, the lessons and the skills last went well, their failures in
        a row with the last error, and why learning has stopped, if it has."""
        health: LearningHealth | None = await read_learning_health(ctx.runtime.store)
        quiet_s: float | None = quiet_limit(ctx.runtime.config.agent.reflection_time)
        stalled: str | None = None if health is None else stall_reason(health, time.time(), quiet_s)
        return LearningHealthView(
            health=health, stalled=stalled, harness_on=ctx.runtime.config.harness.step_time != "off"
        )

    @router.get("/memories/search")
    async def search_memories(query: Annotated[SearchQuery, Query()]) -> list[MemoryHit]:
        return await recall(index, query.q, query.limit)

    # After the routes above: "owner", "learning" and "search" are not ids.
    @router.get("/memories/{memory_id}")
    async def get_memory(memory_id: str) -> MemoryRecord:
        return await index.store.get_memory(memory_id)

    @router.post("/memories", status_code=201)
    async def add_memory(request: MemoryRequest) -> SavedMemory:
        # The owner's own note, from the Memory page: they vouch for its text.
        return await save_memory(index, request.content, request.tags, None, None, None, False)

    @router.patch("/memories/{memory_id}")
    async def edit_memory(memory_id: str, request: MemoryRequest) -> MemoryRecord:
        return await update_memory_note(index, memory_id, request.content, request.tags)

    @router.delete("/memories/{memory_id}")
    async def delete_memory(memory_id: str) -> MemoryRecord:
        """Forget a note: it goes to the trash, from which it can be restored (item 584)."""
        return await forget_memory(index, memory_id)

    @router.put("/memories/{memory_id}/pin")
    async def pin_memory_note(memory_id: str, request: PinRequest) -> MemoryRecord:
        """Pin a note, so that it never rests (a resting note wakes), or take the pin off (item 584)."""
        if request.pinned:
            return await pin_memory(index, memory_id, time.time())
        return await unpin_memory(index, memory_id)

    @router.post("/memories/{memory_id}/unmerge")
    async def unmerge_memory_note(memory_id: str) -> UnmergeView:
        """Take back the merge that made a note: its sources wake and it goes to the trash (item 584; 422 for a note no
        merge made)."""
        undone: Unmerged = await undo_merge(index, memory_id, time.time())
        return UnmergeView(merged=undone.merged, woken=list(undone.woken))

    return router


def schedules_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store
    config: SchedulerConfig = ctx.runtime.config.scheduler
    zone: ZoneInfo = ctx.runtime.schedule_zone

    async def agents_by_id() -> dict[str, AgentRecord]:
        return {agent.id: agent for agent in await store.list_agents()}

    async def view_of(record: ScheduleRecord) -> ScheduleView:
        return _schedule_view(
            record,
            await schedule_zone(record.options, zone),
            await agents_by_id(),
            (await read_state(store)).schedule_id,
        )

    @router.get("/schedules")
    async def list_schedules() -> list[ScheduleView]:
        agents: dict[str, AgentRecord] = await agents_by_id()
        brief_id: str | None = (await read_state(store)).schedule_id
        return [
            _schedule_view(record, await schedule_zone(record.options, zone), agents, brief_id)
            for record in sorted(await store.list_schedules(), key=schedule_order)
        ]

    @router.get("/schedules/{schedule_id}")
    async def get_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await store.get_schedule(schedule_id))

    @router.post("/schedules", status_code=201)
    async def add_schedule(request: ScheduleCreateRequest) -> ScheduleView:
        plan: ScheduleRequest = ScheduleRequest(
            name=request.name,
            goal=request.goal,
            cron=request.cron,
            at=request.at,
            mode=request.mode,
            options=ScheduleOptions(
                model=request.model,
                effort=request.effort,
                speed=request.speed,
                approvals=request.approvals,
                goal_mode=request.goal_mode,
                timezone=request.timezone,
                history_runs=request.history_runs,
                same_chat=request.same_chat,
                stop_after_failures=request.stop_after_failures,
                notify=request.notify,
                agent_id=request.agent_id,
            ),
        )
        if not request.enabled:
            return await view_of(await create_schedule_off(store, config, zone, plan, time.time()))
        return await view_of(await create_schedule(store, config, zone, plan, time.time(), None))

    @router.patch("/schedules/{schedule_id}")
    async def change_schedule(schedule_id: str, request: ScheduleUpdateRequest) -> ScheduleView:
        kept: ScheduleOptions = (await store.get_schedule(schedule_id)).options
        # A change that does not name the agent keeps the schedule's (item 462): only an explicit null makes it Dot's.
        agent_id: str | None = request.agent_id if "agent_id" in request.model_fields_set else kept.agent_id
        plan: ScheduleRequest = ScheduleRequest(
            name=request.name,
            goal=request.goal,
            cron=request.cron,
            at=request.at,
            mode=request.mode,
            options=ScheduleOptions(
                model=request.model,
                effort=request.effort,
                speed=request.speed,
                approvals=request.approvals,
                goal_mode=request.goal_mode,
                timezone=request.timezone,
                history_runs=request.history_runs,
                same_chat=request.same_chat,
                stop_after_failures=request.stop_after_failures,
                notify=request.notify,
                agent_id=agent_id,
                # The form has no field for them: a taught task's skill stays with its schedule (item 666).
                skills=kept.skills,
            ),
        )
        edited: ScheduleRecord = await edit_schedule(
            store, config, zone, schedule_id, plan, request.enabled, time.time()
        )
        return await view_of(edited)

    @router.post("/schedules/{schedule_id}/enable")
    async def switch_on_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await enable_schedule(store, config, zone, schedule_id, time.time()))

    @router.post("/schedules/{schedule_id}/disable")
    async def switch_off_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await disable_schedule(store, schedule_id))

    @router.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str) -> ScheduleView:
        return await view_of(await remove_schedule(store, schedule_id))

    @router.post("/schedules/{schedule_id}/run", status_code=201)
    async def run_schedule_now(schedule_id: str) -> RunRecord:
        """Start a run of the schedule now, by hand (item 455): 409 while the emergency stop is engaged, while one of
        its runs is at work, and in a daemon that runs no scheduler; its next time and its switch stay as they are."""
        if ctx.scheduler is None:
            raise RunConflict(
                "Schedules are not running right now, so this one cannot start", {"schedule_id": schedule_id}
            )
        return await ctx.scheduler.fire_by_hand(schedule_id, time.time())

    @router.get("/schedules/{schedule_id}/firings")
    async def list_firings(schedule_id: str, query: Annotated[FiringsQuery, Query()]) -> list[ScheduleFiring]:
        """The schedule's last firings, at its time and by hand, newest first, one at work among them (item 455)."""
        await store.get_schedule(schedule_id)
        return await store.schedule_firings(schedule_id, SCHEDULE_SOURCE, query.limit)

    return router
