"""What the owner does to a campaign: stop it, let it go on, or mark it achieved on their own word; for the web UI and
``dotx campaign``. Each writes a note in the campaign's ledger, so the sessions after it read what the owner did.

These change the campaign alone: a session that is working goes on until it ends, and nothing follows it once the
campaign is no longer active. The daemon's API also stops such a session when it runs in the daemon.
"""

from dotagent.core.errors import RunConflict
from dotagent.store.db import Store
from dotagent.store.models import CampaignRecord


async def stop_campaign(store: Store, campaign_id: str) -> CampaignRecord:
    """Stop an active campaign: no session starts after it until it goes on again."""
    campaign: CampaignRecord = await store.get_campaign(campaign_id)
    if campaign.status != "active":
        raise RunConflict("the campaign is not active", {"campaign_id": campaign.id, "status": campaign.status})
    await store.set_campaign_status(campaign.id, "stopped", None)
    await store.add_campaign_entry(campaign.id, None, "note", "The owner stopped the campaign.", {})
    return await store.get_campaign(campaign.id)


async def resume_campaign(store: Store, campaign_id: str, now: float) -> CampaignRecord:
    """Let a stopped or achieved campaign go on: it is active again and its next session is due at ``now``. One that
    stopped at ``[agent] campaign_max_sessions`` runs one more session before it stops again."""
    campaign: CampaignRecord = await store.get_campaign(campaign_id)
    if campaign.status == "active":
        raise RunConflict("the campaign is already active", {"campaign_id": campaign.id})
    await store.set_campaign_status(campaign.id, "active", now)
    await store.add_campaign_entry(campaign.id, None, "note", "The owner let the campaign go on.", {})
    return await store.get_campaign(campaign.id)


async def mark_achieved(store: Store, campaign_id: str) -> CampaignRecord:
    """Mark a campaign achieved on the owner's word, without the goal check: no session follows."""
    campaign: CampaignRecord = await store.get_campaign(campaign_id)
    if campaign.status == "achieved":
        raise RunConflict("the campaign is already achieved", {"campaign_id": campaign.id})
    await store.achieve_campaign(campaign.id, "The owner marked the campaign achieved.")
    await store.add_campaign_entry(campaign.id, None, "note", "The owner marked the campaign achieved.", {})
    return await store.get_campaign(campaign.id)
