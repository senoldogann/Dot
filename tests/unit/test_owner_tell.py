"""What Dot tells the owner on its own (items 570 and 571): one function sends it, as an event of Dot itself that the
phone's push and the pages' notifications both take; a tell opens a route of Dot's own pages and nothing else, and what
a third party wrote into one stays a line of limited size."""

from pathlib import Path

import pytest

from dotagent.core.errors import InvalidValueError
from dotagent.events import SYSTEM_RUN_ID, EventBus
from dotagent.notify.owner import OWNER_TOLD_EVENT, TEXT_CHARS, TITLE_CHARS, OwnerTold, tell_owner, told
from dotagent.notify.push import PUSH_KINDS, PushMessage, notice_message
from dotagent.server.stream import LIFECYCLE_KINDS
from dotagent.store.db import Store
from dotagent.store.models import EventRecord


def test_a_tell_is_one_line_of_limited_size_and_keeps_its_route() -> None:
    long: str = "word " * 200
    tell: OwnerTold = told("  Bank\nmail ", f"From the bank:\n\n{long}", "#/runs/run_1")
    assert tell.title == "Bank mail"
    assert "\n" not in tell.text and len(tell.text) == TEXT_CHARS and tell.text.endswith("…")
    assert tell.url == "#/runs/run_1"
    assert len(told("t" * 500, "x", "#/runs/run_1").title) == TITLE_CHARS


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "//example.com",
        "/runs/run_1",
        "javascript:alert(1)",
        "#runs/run_1",
        "#/runs/run 1",
        "#/runs/run_1\n#/runs/run_2",
        "#/" + "x" * 300,
        "",
    ],
)
def test_a_tell_opens_only_a_route_of_dots_own_pages(url: str) -> None:
    with pytest.raises(InvalidValueError, match="route of Dot's own pages"):
        told("Title", "Text", url)


def test_the_phone_and_the_pages_both_take_the_tell() -> None:
    assert OWNER_TOLD_EVENT in PUSH_KINDS and OWNER_TOLD_EVENT in LIFECYCLE_KINDS
    event: EventRecord = EventRecord(
        id=0,
        run_id=SYSTEM_RUN_ID,
        ts=1.0,
        kind=OWNER_TOLD_EVENT,
        data=told("Landlord", "Re: rent " + "x" * 400, "#/runs/run_2").model_dump(),
    )
    message: PushMessage | None = notice_message(event)
    assert message is not None
    assert (message.title, message.route, message.state) == ("Landlord", "#/runs/run_2", "idle")
    assert message.body.startswith("Re: rent ") and len(message.body) == 140


async def test_a_tell_is_an_event_of_dot_itself_that_is_not_stored(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        async with bus.subscribe(SYSTEM_RUN_ID) as feed:
            await tell_owner(bus, "Your morning brief", "Nothing needs you.", "#/runs/run_3")
            event: EventRecord = feed.queue.get_nowait()
        assert (event.run_id, event.kind, event.id) == (SYSTEM_RUN_ID, OWNER_TOLD_EVENT, 0)
        assert event.data == {"title": "Your morning brief", "text": "Nothing needs you.", "url": "#/runs/run_3"}
        with pytest.raises(InvalidValueError):
            await tell_owner(bus, "Title", "Text", "https://example.com/")
    finally:
        await store.close()
