"""The pure parts of the iPhone link: the state steps of the keeper, the list of devices devicectl writes and the
``[phone]`` settings."""

import json

import pytest

from dotagent.config import PhoneConfig, load_default_config
from dotagent.core.errors import PhoneError
from dotagent.phone.devicectl import device_available, launch_argv
from dotagent.phone.keeper import next_state, notice_for, state_line, state_word
from dotagent.phone.types import PhoneProbe
from dotagent.server.routes_iphone import iphone_view


def test_the_state_keeps_its_start_while_the_kind_holds_and_restarts_when_it_changes() -> None:
    locked: PhoneProbe = PhoneProbe(url="http://x", locked=True)
    first = next_state(None, locked, 10.0)
    again = next_state(first.model_copy(update={"notified": True}), locked, 40.0)
    assert (again.kind, again.since, again.checked_at, again.notified) == ("locked", 10.0, 40.0, True)
    unlocked = next_state(again, PhoneProbe(url="http://x", locked=False), 50.0)
    assert (unlocked.kind, unlocked.since, unlocked.notified) == ("up", 50.0, False)
    gone = next_state(unlocked, PhoneProbe(url=None, locked=None), 60.0)
    assert (gone.kind, gone.url) == ("unreachable", None)
    # An address that answers but does not say whether the phone is locked is neither up nor out of reach.
    unclear = next_state(gone, PhoneProbe(url="http://x", locked=None), 70.0)
    assert (unclear.kind, unclear.url, unclear.since) == ("unclear", "http://x", 70.0)
    assert state_line(unclear, 75.0) == "answering, but not saying whether it is locked (for 5 s; checked 5 s ago)"


def test_the_owner_is_told_once_after_the_wait_and_only_of_a_state_that_needs_them() -> None:
    state = next_state(None, PhoneProbe(url="http://x", locked=True), 0.0)
    assert notice_for(state, 300.0) is None
    waited = state.model_copy(update={"checked_at": 300.0})
    notice = notice_for(waited, 300.0)
    assert notice is not None and "locked" in notice[0].lower()
    assert notice_for(waited.model_copy(update={"notified": True}), 300.0) is None
    up = next_state(state, PhoneProbe(url="http://x", locked=False), 400.0)
    assert notice_for(up.model_copy(update={"checked_at": 9_000.0}), 300.0) is None
    unclear = next_state(up, PhoneProbe(url="http://x", locked=None), 500.0)
    told = notice_for(unclear, 0.0)
    assert told is not None and "cannot tell" in told[0]
    assert state_word(None) == "not checked yet" and state_word(waited) == "reachable but locked"


def test_devicectl_listing_says_whether_the_phone_is_available() -> None:
    def listing(state: str) -> str:
        device: dict[str, object] = {
            "identifier": "AAAA-1",
            "hardwareProperties": {"udid": "00008110-0001"},
            "connectionProperties": {"tunnelState": state},
            "extra": 1,
        }
        return json.dumps({"info": {}, "result": {"devices": [device]}})

    assert device_available(listing("connected"), "00008110-0001")
    assert device_available(listing("disconnected"), "AAAA-1")
    assert not device_available(listing("unavailable"), "00008110-0001")
    assert not device_available(listing("connected"), "someone-else")
    with pytest.raises(PhoneError):
        device_available("not json", "x")
    # A listing of another shape fails loudly, not as no phone or as one in reach (the pre-push review of item 1196).
    shapeless: dict[str, object] = {"identifier": "AAAA-1", "hardwareProperties": {"udid": "00008110-0001"}}
    with pytest.raises(PhoneError, match="shape"):
        device_available(json.dumps({"result": {"devices": [shapeless]}}), "00008110-0001")
    with pytest.raises(PhoneError, match="shape"):
        device_available(json.dumps({"info": {}}), "00008110-0001")
    assert launch_argv("U", "dev.dotagent.wda.xctrunner")[-3:] == (
        "U",
        "--terminate-existing",
        "dev.dotagent.wda.xctrunner",
    )


def test_the_phone_is_off_by_default_and_its_addresses_are_http() -> None:
    config: PhoneConfig = load_default_config().phone
    assert not config.enabled and config.wda_urls == []
    # Off, the page draws no card of the link: GET /api/iphone says only that.
    assert iphone_view(None).model_dump() == {
        "enabled": False,
        "state": None,
        "url": None,
        "since_s": None,
        "checked_s": None,
        "text": None,
    }
    with pytest.raises(ValueError, match="http"):
        PhoneConfig.model_validate({**config.model_dump(), "wda_urls": ["100.1.1.1:8100"]})
