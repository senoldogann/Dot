"""The Dot Next app's calendars, reminders and location (item 2051), on a served daemon with a fake phone: the phone
holds ``GET /api/phone/asks/next`` open and answers at ``POST /api/phone/asks/{id}``, and the tools read what it
answered. Only a paired phone's own key takes asks; with no phone open the tool says so at once; a run has the tools
only while a phone is paired. No network and no model service is used."""

import asyncio
import time
from pathlib import Path

import httpx
import pytest
from phone_stand_ins import MAC, own_home_of, pair_phone, served

from dotagent.agent.verify import new_facts
from dotagent.core.errors import ToolExecutionError
from dotagent.core.paths import phones_path, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.phone.asks import PhoneAnswerUnreadable
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime
from dotagent.server.phones import any_paired
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return own_home_of(tmp_path, monkeypatch)


class _Gate:
    """Stands in for the run gate (which the loop asks before a call runs): keeps each call's action as the tool
    classified it."""

    def __init__(self) -> None:
        self.shown: list[ActionInfo] = []

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


async def _call(runtime: Runtime, gate: _Gate, name: str, arguments: JsonObject) -> ToolResult:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="phone-asks-test",
        agent_id=None,
        thread_id="phone-asks-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=gate,
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    gate.shown.append(prepared.action)
    return await prepared.run(ctx)


async def _phone_answers(client: httpx.AsyncClient, key: dict[str, str], answer: JsonObject) -> JsonObject:
    """The fake phone: waits for one ask, answers it with ``answer`` and gives the ask back."""
    while True:
        polled: httpx.Response = await client.get("/phone/asks/next", headers=key)
        assert polled.status_code in (200, 204), polled.text
        if polled.status_code == 200:
            break
    ask: JsonObject = polled.json()
    answered: httpx.Response = await client.post(f"/phone/asks/{ask['id']}", json={"answer": answer}, headers=key)
    assert answered.status_code == 204, answered.text
    return ask


async def _open(runtime: Runtime, phone: asyncio.Future[JsonObject]) -> None:
    """Wait until the fake phone's first request reached the daemon, as Dot Next's has once it is open."""
    async with asyncio.timeout(5.0):
        while runtime.phone_link.present() is None:
            assert not phone.done(), phone
            await asyncio.sleep(0.01)


async def test_the_phone_answers_the_tools_while_dot_next_is_open(own_home: Path) -> None:
    async with served(own_home) as (client, _, runtime):
        assert not await asyncio.to_thread(any_paired, phones_path()), "a run has no phone tools before a pairing"
        _, phone = await pair_phone(client)
        assert await asyncio.to_thread(any_paired, phones_path())
        gate = _Gate()

        # The Mac's token is no phone: an ask is one phone's.
        refused: httpx.Response = await client.get("/phone/asks/next", headers=MAC)
        assert refused.status_code == 403, refused.text

        # No phone holds Dot Next open: the tool says so at once, in words a run reads.
        began: float = time.monotonic()
        with pytest.raises(ToolExecutionError, match="only while it is open"):
            await _call(runtime, gate, "phone_location", {})
        assert time.monotonic() - began < 2.0

        created: JsonObject = {"kind": "created", "id": "EK-1", "calendar": "Home"}
        phone_side = asyncio.ensure_future(_phone_answers(client, phone, created))
        await _open(runtime, phone_side)
        result: ToolResult = await _call(
            runtime, gate, "phone_calendar_add", {"title": "Dentist", "start": "2026-11-28", "end": "2026-11-29"}
        )
        ask: JsonObject = await phone_side
        assert ask["kind"] == "calendar_add" and ask["all_day"] is True
        assert (ask["start"], ask["end"], ask["title"]) == ("2026-11-28", "2026-11-30", "Dentist")
        assert not result.is_error and result.text.startswith("Added to the iPhone's calendar (id EK-1): “Dentist”")
        assert result.untrusted and "Home" in result.text, (
            "the calendar's name is fenced: a shared one is someone else's"
        )
        assert (gate.shown[-1].kind, gate.shown[-1].target) == ("app_control", "iPhone")

        # An answer whose times cannot be read is refused at the door, and the run is told at once (not after 75 s).
        polling = asyncio.ensure_future(client.get("/phone/asks/next", headers=phone))
        reading = asyncio.ensure_future(
            _call(runtime, gate, "phone_calendar_events", {"start": "2026-11-28", "end": "2026-11-29"})
        )
        bad_id: str = str((await polling).json()["id"])
        bad_event: JsonObject = {
            "id": "EK-9",
            "title": "x",
            "start": "2026-11-28T09:00:00+00:00",
            "end": "2026-11-29",
            "all_day": True,
            "location": "",
            "notes": "",
            "calendar": "",
            "organizer": "",
        }
        bad: httpx.Response = await client.post(
            f"/phone/asks/{bad_id}",
            json={"answer": {"kind": "events", "events": [bad_event], "more": False}},
            headers=phone,
        )
        assert bad.status_code == 422, bad.text
        began = time.monotonic()
        with pytest.raises(PhoneAnswerUnreadable, match="could not be read"):
            await reading
        assert time.monotonic() - began < 2.0
        late: httpx.Response = await client.post(
            f"/phone/asks/{bad_id}", json={"answer": {"kind": "events", "events": [], "more": False}}, headers=phone
        )
        assert late.status_code == 404, "the run no longer waits"

        events: JsonObject = {
            "kind": "events",
            "events": [
                {
                    "id": "EK-2",
                    "title": "Standup",
                    "start": "2026-11-28T09:00:00+00:00",
                    "end": "2026-11-28T09:15:00+00:00",
                    "all_day": False,
                    "location": "",
                    "notes": "",
                    "calendar": "Work",
                    "organizer": "",
                }
            ],
            "more": False,
        }
        phone_side = asyncio.ensure_future(_phone_answers(client, phone, events))
        read: ToolResult = await _call(
            runtime, gate, "phone_calendar_events", {"start": "2026-11-28", "end": "2026-11-29"}
        )
        assert (await phone_side)["kind"] == "calendar_events"
        assert read.untrusted and "Standup" in read.text and "<account_content" in read.text
        assert gate.shown[-1].kind == "read"

        # A refusal reaches the run with where the owner allows it.
        refusal: JsonObject = {"kind": "refused", "reason": "denied", "message": "Reminders are not allowed."}
        phone_side = asyncio.ensure_future(_phone_answers(client, phone, refusal))
        refused: ToolResult = await _call(runtime, gate, "phone_reminder_add", {"title": "Call Ada"})
        assert refused.is_error and not refused.untrusted and "Dot Next › Reminders" in refused.text
        assert (await phone_side)["kind"] == "reminder_add"

        # An answer of another kind is refused, and ends the run's wait at once with Dot's own words.
        polling = asyncio.ensure_future(client.get("/phone/asks/next", headers=phone))
        asking = asyncio.ensure_future(_call(runtime, gate, "phone_location", {}))
        location_ask: JsonObject = (await polling).json()
        wrong: httpx.Response = await client.post(
            f"/phone/asks/{location_ask['id']}", json={"answer": created}, headers=phone
        )
        assert wrong.status_code == 422, wrong.text
        with pytest.raises(PhoneAnswerUnreadable, match="another kind"):
            await asking
        here: JsonObject = {
            "kind": "location",
            "latitude": 41.0,
            "longitude": 29.0,
            "accuracy_m": 12.0,
            "at": "2026-11-28T11:00:00Z",
        }
        phone_side = asyncio.ensure_future(_phone_answers(client, phone, here))
        located: ToolResult = await _call(runtime, gate, "phone_location", {})
        location_ask = await phone_side
        assert "41.00000,29.00000" in located.text
        gone: httpx.Response = await client.post(
            f"/phone/asks/{location_ask['id']}", json={"answer": here}, headers=phone
        )
        assert gone.status_code == 404, "an ask answered once takes no second answer"
