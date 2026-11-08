"""A campaign's status moves only from the state each change expects (item 1046): a stop that comes after the
campaign was achieved leaves its outcome and evidence, and a late plan puts no session on an ended campaign."""

from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import CampaignRecord, ThreadRecord


async def _campaign(store: Store) -> CampaignRecord:
    thread: ThreadRecord = await store.create_thread("Leads", "test")
    return await store.create_campaign(
        "Leads", "Find fifty leads.", thread.id, "ollama:a", "medium", "standard", None, "ask", (), "own"
    )


async def test_a_stop_after_the_campaign_was_achieved_keeps_its_outcome(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        campaign: CampaignRecord = await _campaign(store)
        assert await store.achieve_campaign(campaign.id, "Fifty leads.", only_active=True)
        assert not await store.stop_active_campaign(campaign.id)
        assert not await store.set_next_session(campaign.id, 123.0)
        assert not await store.plan_campaign_sessions(campaign.id, "0 9 * * *", 123.0)
        kept: CampaignRecord = await store.get_campaign(campaign.id)
        assert (kept.status, kept.evidence, kept.next_session_at, kept.cron) == ("achieved", "Fifty leads.", None, None)
    finally:
        await store.close()


async def test_a_campaign_stops_and_goes_on_once_each(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        campaign: CampaignRecord = await _campaign(store)
        assert await store.set_next_session(campaign.id, 100.0)
        assert await store.stop_active_campaign(campaign.id)
        assert not await store.stop_active_campaign(campaign.id)
        assert (await store.get_campaign(campaign.id)).next_session_at is None
        assert await store.reactivate_campaign(campaign.id, 200.0)
        assert not await store.reactivate_campaign(campaign.id, 300.0)
        assert (await store.get_campaign(campaign.id)).next_session_at == 200.0
    finally:
        await store.close()
