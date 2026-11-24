"""``GET /api/needs-you`` (item 1422), on a served daemon: every question a run left for the owner, every request for a
secret and every hand-off in one list, newest first, with a count for the badge; a phone's key reads it as the Mac's
token does and no other request does; what the owner decided is no longer in it, and the Activity's own rows for the
same asks are as they were. No network and no model service is used."""

from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.core.types import JsonObject

pytestmark = pytest.mark.asyncio


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


async def test_the_list_of_what_waits_on_the_owner_has_the_shape_the_app_reads_and_follows_the_answers(
    own_home: Path,
) -> None:
    async with served(own_home) as (client, _, runtime):
        store = runtime.store
        _, phone = await pair_phone(client)
        assert (await client.get("/needs-you")).status_code == 401
        empty: httpx.Response = await client.get("/needs-you", headers=phone)
        assert empty.status_code == 200 and empty.json() == {"count": 0, "items": []}

        thread = await store.create_thread("Plan the launch", "chat")
        run = await store.create_run(
            thread.id, "Plan", "task", "m:m", None, "chat", "medium", "standard", None, "ask", False, None, None, None
        )
        options: list[JsonObject] = [
            {"label": "Friday", "description": "Ship it", "recommended": True},
            {"label": "Monday", "description": None, "recommended": False},
        ]
        question = await store.create_left_approval(
            run.id, "ask_user", "Which day?", "question", {"question": "Which day?", "options": options}
        )
        handoff = await store.create_kept_approval(
            run.id, "browser_act", "Pay the invoice — only you", "handoff", {}, "digest", 9e9, thread.id, None
        )
        secret = await store.create_left_approval(
            run.id, "ask_secret", "Stripe key", "secret", {"name": "STRIPE", "label": "Stripe key", "why": "to bill"}
        )
        # A step kept for the owner whose time is over has lapsed: it is not listed.
        lapsed = await store.create_kept_approval(
            run.id, "browser_act", "Old", "handoff", {}, "old", 1.0, thread.id, None
        )

        for headers in (MAC, phone):
            answered: httpx.Response = await client.get("/needs-you", headers=headers)
            assert answered.status_code == 200, answered.text
            body: dict[str, object] = answered.json()
            assert body["count"] == 3
            items: list[dict[str, object]] = body["items"]  # type: ignore[assignment]  # JSON read back
            assert [item["id"] for item in items] == [secret.id, handoff.id, question.id]
            common: set[str] = {"id", "kind", "run_id", "thread_id", "agent", "title", "since"}
            by_kind: dict[object, dict[str, object]] = {item["kind"]: item for item in items}
            assert set(by_kind) == {"question", "handoff", "secret"}
            assert set(by_kind["question"]) == common | {"choices", "recommended"}
            assert by_kind["question"]["choices"] == ["Friday", "Monday"]
            assert by_kind["question"]["recommended"] == "Friday" and by_kind["question"]["title"] == "Which day?"
            assert by_kind["question"]["run_id"] == run.id and by_kind["question"]["thread_id"] == thread.id
            assert by_kind["question"]["agent"] == "Dot" and by_kind["question"]["since"] == question.created_at
            # A hand-off and a secret have neither choices nor a recommendation: the keys are not there.
            assert set(by_kind["handoff"]) == common and set(by_kind["secret"]) == common
            # The secret is its id, who asks and a fixed line: not the card's words, and no value.
            assert by_kind["secret"]["title"] == "Dot asks for a secret"
            assert "Stripe" not in str(by_kind["secret"]) and "STRIPE" not in str(by_kind["secret"])

        # The Activity lists the same asks as it did: a question left, a step kept for the owner, and the lapsed one.
        activity: dict[str, list[dict[str, object]]] = (await client.get("/activity", headers=MAC)).json()
        waiting: dict[object, object] = {row["id"]: row["kind"] for row in activity["waiting"]}
        assert waiting == {
            question.id: "question",
            handoff.id: "approval",
            secret.id: "question",
            lapsed.id: "approval",
        }

        # An answer takes the ask out of the list, and the count with it.
        await store.decide_approval(question.id, "approved", "ui", "Friday")
        after: dict[str, object] = (await client.get("/needs-you", headers=phone)).json()
        assert after["count"] == 2
        assert [item["id"] for item in after["items"]] == [secret.id, handoff.id]  # type: ignore[attr-defined]  # JSON
