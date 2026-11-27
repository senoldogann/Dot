"""``GET /api/drafts`` (item 1825), on a served daemon and with a phone's own key, as the iPhone app's Activity reads
it: the drafts that wait for the owner (none sent or discarded yet, or whose last send failed), the newest first, and
nothing a send or a discard settled; ``limit`` is kept within its bounds. No network and no model service is used."""

import asyncio
from pathlib import Path

import httpx
import pytest
from phone_stand_ins import own_home_of, pair_phone, served

from dotagent.store.db import Store
from dotagent.store.models import DraftRecord, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


async def _draft(store: Store, run_id: str, subject: str) -> DraftRecord:
    made: DraftRecord = await store.add_draft(run_id, "mail", ["ayse@example.com"], [], subject, f"{subject}.", None)
    # Each a moment after the one before, so that the newest-first order is the test's own.
    await asyncio.sleep(0.01)
    return made


async def test_the_phone_reads_the_drafts_that_wait_for_the_owner_newest_first(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        _, phone = await pair_phone(client)
        store: Store = runtime.store
        chat: ThreadRecord = await store.create_thread("Reports", "ui")
        run: RunRecord = await store.create_run(
            chat.id,
            "Write the reports.",
            "task",
            "ollama:x",
            None,
            "ui",
            "high",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        first: DraftRecord = await _draft(store, run.id, "Weekly report")
        sent: DraftRecord = await _draft(store, run.id, "Sent one")
        refused: DraftRecord = await _draft(store, run.id, "Refused one")
        thrown: DraftRecord = await _draft(store, run.id, "Discarded one")
        newest: DraftRecord = await _draft(store, run.id, "Lunch")
        for taken, error in ((sent, None), (refused, "the account is offline")):
            await store.claim_draft(taken.id, list(taken.to), [], taken.subject, taken.body)
            await store.settle_draft(taken.id, error)
        assert (await client.post(f"/drafts/{thrown.id}/discard", headers=phone)).status_code == 200

        listed: httpx.Response = await client.get("/drafts", headers=phone)
        assert listed.status_code == 200, listed.text
        rows: list[dict[str, object]] = listed.json()
        assert [row["id"] for row in rows] == [newest.id, refused.id, first.id]
        assert [row["status"] for row in rows] == ["waiting", "failed", "waiting"]
        assert rows[1]["error"] == "the account is offline"
        assert rows[0]["run_id"] == run.id and rows[0]["to"] == ["ayse@example.com"]

        one: httpx.Response = await client.get("/drafts", params={"limit": 1}, headers=phone)
        assert [row["id"] for row in one.json()] == [newest.id]
        for wrong in (0, 201):
            assert (await client.get("/drafts", params={"limit": wrong}, headers=phone)).status_code == 422
