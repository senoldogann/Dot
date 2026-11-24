"""What each phone wants to hear (item 1421): three switches kept for the phone whose key set them, all on until it says
otherwise, strict booleans, and a row that cannot be read is read as all on."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from dotagent.notify.push import PUSH_GROUPS
from dotagent.notify.settings import (
    DEFAULT_PUSH_SETTINGS,
    PushSettings,
    forget_push_settings,
    keep_push_settings,
    push_settings_of,
)
from dotagent.store.db import Store


def test_the_three_switches_are_on_by_default_and_each_names_a_kind_of_push() -> None:
    assert DEFAULT_PUSH_SETTINGS.model_dump() == {"needs_you": True, "ready": True, "done": True}
    off: PushSettings = PushSettings(needs_you=True, ready=False, done=False)
    assert [(group, off.wants(group)) for group in PUSH_GROUPS] == [
        ("needs_you", True),
        ("ready", False),
        ("done", False),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"needs_you": True, "ready": True},
        {"needs_you": True, "ready": True, "done": "yes"},
        {"needs_you": 1, "ready": True, "done": True},
        {"needs_you": True, "ready": True, "done": True, "badge": True},
    ],
)
def test_the_switches_are_given_whole_as_booleans_and_no_other_key_is_taken(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PushSettings.model_validate(body)


async def test_a_phone_keeps_its_own_switches_and_a_removed_phone_forgets_them(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        assert await push_settings_of(store, "p1") == DEFAULT_PUSH_SETTINGS
        quiet: PushSettings = PushSettings(needs_you=True, ready=False, done=False)
        await keep_push_settings(store, "p1", quiet)
        assert await push_settings_of(store, "p1") == quiet
        assert await push_settings_of(store, "p2") == DEFAULT_PUSH_SETTINGS
        await keep_push_settings(store, "p1", DEFAULT_PUSH_SETTINGS)
        assert await push_settings_of(store, "p1") == DEFAULT_PUSH_SETTINGS
        await keep_push_settings(store, "p2", quiet)
        await forget_push_settings(store, "p2")
        assert await push_settings_of(store, "p2") == DEFAULT_PUSH_SETTINGS
    finally:
        await store.close()


async def test_a_row_that_cannot_be_read_is_logged_and_read_as_all_on(tmp_path: Path) -> None:
    """A phone hears more rather than miss what waits for its owner; the row says so in the log."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        await store.kv_set("phone.push.p1", '{"needs_you": false, "ready": "maybe"}')
        with capture_logs() as logs:
            assert await push_settings_of(store, "p1") == DEFAULT_PUSH_SETTINGS
        assert [entry["event"] for entry in logs] == ["phone_push_settings_unreadable"]
        assert logs[0]["phone"] == "p1"
    finally:
        await store.close()
