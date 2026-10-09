"""Routes for campaigns: goals that Dot keeps working on in sessions until their outcome is verified."""

import time

from fastapi import APIRouter

from dotagent.agent.campaign_control import mark_achieved, resume_campaign, stop_campaign
from dotagent.core.errors import RunConflict
from dotagent.scheduler.timing import format_local
from dotagent.server.context import ServerContext
from dotagent.server.views import CampaignDetail, CampaignSummary
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, CampaignRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL

# How many of the newest entries of its ledger a campaign's page shows.
_PAGE_ENTRIES: int = 200
# How many of the newest questions its sessions asked a campaign's page shows.
_PAGE_QUESTIONS: int = 50


def _working(sessions: list[RunRecord]) -> str | None:
    return next((session.id for session in reversed(sessions) if session.status in WORKING_STATUSES), None)


def campaigns_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    store: Store = ctx.runtime.store

    def next_local(campaign: CampaignRecord) -> str | None:
        planned: float | None = campaign.next_session_at
        return None if planned is None else format_local(planned, ctx.runtime.schedule_zone)

    async def summary(campaign: CampaignRecord) -> CampaignSummary:
        sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
        return CampaignSummary(
            record=campaign,
            sessions=len(sessions),
            working=_working(sessions),
            next_local=next_local(campaign),
            progress=await store.latest_campaign_entry(campaign.id, "progress"),
        )

    async def detail(campaign_id: str) -> CampaignDetail:
        campaign: CampaignRecord = await store.get_campaign(campaign_id)
        sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
        return CampaignDetail(
            record=campaign,
            sessions=sessions,
            working=_working(sessions),
            next_local=next_local(campaign),
            progress=await store.latest_campaign_entry(campaign.id, "progress"),
            entries=await store.campaign_entries(campaign.id, _PAGE_ENTRIES),
            questions=await store.campaign_questions(campaign.id, ASK_USER_TOOL, _PAGE_QUESTIONS),
            tokens=await store.campaign_tokens(campaign.id),
        )

    async def stop_session(campaign_id: str) -> None:
        """Stop the campaign's session that is working in the daemon, if one is."""
        working: str | None = _working(await store.campaign_sessions(campaign_id))
        if working is None or working not in ctx.manager.active_run_ids():
            return
        try:
            ctx.manager.cancel(working)
        except RunConflict:
            # It ended in between.
            return

    @router.get("/campaigns")
    async def list_campaigns() -> list[CampaignSummary]:
        return [await summary(campaign) for campaign in await store.list_campaigns()]

    @router.get("/campaigns/{campaign_id}")
    async def get_campaign(campaign_id: str) -> CampaignDetail:
        return await detail(campaign_id)

    @router.post("/campaigns/{campaign_id}/stop")
    async def stop(campaign_id: str) -> CampaignDetail:
        """Stop the campaign, and its session that is working in the daemon."""
        await stop_campaign(store, campaign_id)
        await stop_session(campaign_id)
        return await detail(campaign_id)

    @router.post("/campaigns/{campaign_id}/resume")
    async def resume(campaign_id: str) -> CampaignDetail:
        """Let a stopped or achieved campaign go on: its next session starts with the scheduler's next tick."""
        await resume_campaign(store, campaign_id, time.time())
        return await detail(campaign_id)

    @router.post("/campaigns/{campaign_id}/achieved")
    async def achieved(campaign_id: str) -> CampaignDetail:
        """Mark the campaign achieved on the owner's word, and stop its session that is working in the daemon."""
        await mark_achieved(store, campaign_id)
        await stop_session(campaign_id)
        return await detail(campaign_id)

    return router
