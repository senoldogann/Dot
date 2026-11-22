"""The pure parts of the iPhone link: the state steps of the keeper, the list of devices devicectl writes and the
``[phone]`` settings."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.config import PhoneConfig, load_default_config
from dotagent.core.errors import PhoneError
from dotagent.phone.devicectl import CommandResult, XcrunDevicectl, device_available, launch_argv, run_command
from dotagent.phone.keeper import first_line, keep_once, keep_phone, next_state, notice_for, state_line, wait_after
from dotagent.phone.types import PhoneProbe, PhoneState
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


def test_the_owner_is_told_once_until_the_phone_is_up_and_never_of_a_lock() -> None:
    # A locked phone is how the owner uses it (item 1197): however long it lasts, it is not told.
    state = next_state(None, PhoneProbe(url="http://x", locked=True), 0.0)
    waited = state.model_copy(update={"checked_at": 9_000.0})
    assert notice_for(waited, 300.0) is None
    gone = next_state(None, PhoneProbe(url=None, locked=None), 0.0)
    assert notice_for(gone, 300.0) is None
    notice = notice_for(gone.model_copy(update={"checked_at": 300.0}), 300.0)
    assert notice is not None and "cannot reach" in notice[0]
    told = gone.model_copy(update={"checked_at": 300.0, "notified": True})
    # Going between locked, unclear and out of reach is one stretch: told once for it.
    locked = next_state(told, PhoneProbe(url="http://x", locked=True), 400.0)
    unclear = next_state(locked, PhoneProbe(url="http://x", locked=None), 500.0)
    again = next_state(unclear, PhoneProbe(url=None, locked=None), 600.0)
    assert (locked.notified, unclear.notified, again.notified) == (True, True, True)
    assert notice_for(again.model_copy(update={"checked_at": 9_000.0}), 300.0) is None
    # Once the phone is up, the next stretch is told again.
    up = next_state(again, PhoneProbe(url="http://x", locked=False), 700.0)
    assert notice_for(up.model_copy(update={"checked_at": 9_000.0}), 300.0) is None
    unclear_again = next_state(up, PhoneProbe(url="http://x", locked=None), 800.0)
    told_again = notice_for(unclear_again, 0.0)
    assert told_again is not None and "cannot tell" in told_again[0]


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


async def test_a_program_that_cannot_be_started_is_a_phone_error_not_an_oserror() -> None:
    """Item 1313: ``xcrun`` missing raised ``FileNotFoundError`` out of the keeper, which ended its task for good."""
    with pytest.raises(PhoneError, match="could not be started") as caught:
        await run_command(["/nonexistent/dot-item-1313/xcrun", "devicectl"], 5.0)
    assert caught.value.context["argv"] == ["/nonexistent/dot-item-1313/xcrun", "devicectl"]
    # A program that runs is unchanged: its output and exit status come back.
    done: CommandResult = await run_command(["/bin/sh", "-c", "echo out; echo err >&2; exit 3"], 5.0)
    assert (done.returncode, done.stdout.strip(), done.stderr.strip()) == (3, "out", "err")


async def test_a_listing_devicectl_did_not_write_is_a_phone_error() -> None:
    async def wrote_nothing(argv: Sequence[str], timeout_s: float) -> CommandResult:
        return CommandResult(returncode=0, stdout="", stderr="")

    with pytest.raises(PhoneError, match="could not be read"):
        await XcrunDevicectl(wrote_nothing).is_available("00008110-0001")

    async def wrote_it(argv: Sequence[str], timeout_s: float) -> CommandResult:
        device: dict[str, object] = {
            "identifier": "AAAA-1",
            "hardwareProperties": {"udid": "00008110-0001"},
            "connectionProperties": {"tunnelState": "connected"},
        }
        await asyncio.to_thread(
            Path(argv[-1]).write_text, json.dumps({"result": {"devices": [device]}}), encoding="utf-8"
        )
        return CommandResult(returncode=0, stdout="", stderr="")

    assert await XcrunDevicectl(wrote_it).is_available("00008110-0001")


def test_the_first_line_of_an_error_output_is_kept_cut_to_three_hundred_characters() -> None:
    assert first_line("first\nsecond\nthird") == "first"
    assert first_line("  \n\nfirst after blanks\nsecond") == "first after blanks"
    assert first_line("x" * 1000) == "x" * 300
    assert first_line("") is None and first_line(None) is None and first_line(5) is None


class _Link:
    """A phone link of the test: ``checks`` are what each look does, in order (an exception is raised); the last one
    repeats."""

    def __init__(self, checks: list[PhoneState | Exception], relaunch: Exception | bool = False) -> None:
        self._checks: list[PhoneState | Exception] = checks
        self._relaunch: Exception | bool = relaunch
        self.looks: int = 0
        self.relaunches: int = 0
        self.looked: asyncio.Event = asyncio.Event()

    async def check(self) -> PhoneState:
        step: PhoneState | Exception = self._checks[min(self.looks, len(self._checks) - 1)]
        self.looks += 1
        if self.looks >= len(self._checks) + 2:
            self.looked.set()
        if isinstance(step, Exception):
            raise step
        return step

    async def relaunch(self) -> bool:
        self.relaunches += 1
        if isinstance(self._relaunch, Exception):
            raise self._relaunch
        return self._relaunch

    def mark_notified(self) -> None:
        return None


_UP: PhoneState = PhoneState(kind="up", since=0.0, url="http://x", notified=False, checked_at=0.0)
_GONE: PhoneState = PhoneState(kind="unreachable", since=0.0, url=None, notified=False, checked_at=0.0)


async def _nobody(title: str, text: str) -> None:
    return None


async def test_a_round_that_fails_with_anything_is_logged_with_its_traceback_and_the_keeper_goes_on() -> None:
    """Item 1313: an ``OSError`` out of devicectl (not a ``DotError``) ended ``keep_phone`` for the daemon's life, so a
    phone that came back was never kept again."""
    link: _Link = _Link([OSError("no such file: xcrun"), RuntimeError("a bug"), _UP])
    stopping: asyncio.Event = asyncio.Event()
    keeping: asyncio.Task[None] = asyncio.create_task(keep_phone(link, _nobody, 0.001, 0.0, stopping))
    with capture_logs() as logs:
        await asyncio.wait_for(link.looked.wait(), 10)
    assert not keeping.done(), "the keeper is still there"
    stopping.set()
    async with asyncio.timeout(10):
        await keeping
    crashed: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "phone_keeper_round_crashed"]
    assert [(entry["error_type"], entry["exc_info"]) for entry in crashed[:2]] == [
        ("OSError", True),
        ("RuntimeError", True),
    ]
    assert link.looks >= 5, "and it kept looking after both"


def test_a_round_that_fails_waits_longer_each_time_up_to_ten_minutes() -> None:
    assert [wait_after(30.0, failed) for failed in range(8)] == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0, 600.0]
    # The normal case is the configured interval; one longer than the cap is never cut short.
    assert wait_after(30.0, 0) == 30.0 and wait_after(900.0, 0) == 900.0 and wait_after(900.0, 3) == 900.0


async def test_a_launch_that_fails_logs_the_first_line_of_what_devicectl_said() -> None:
    error: PhoneError = PhoneError(
        "devicectl could not launch the runner app on the phone",
        {"returncode": 1, "stderr": "ERROR: The device is locked. (com.apple.dt.CoreDeviceError 1002)\nmore\nlines"},
    )
    link: _Link = _Link([_GONE], relaunch=error)
    with capture_logs() as logs:
        state: PhoneState = await keep_once(link, _nobody, 300.0)
    assert state.kind == "unreachable"
    [failed] = [entry for entry in logs if entry["event"] == "phone_relaunch_failed"]
    assert failed["stderr"] == "ERROR: The device is locked. (com.apple.dt.CoreDeviceError 1002)"
    assert failed["error"] == "devicectl could not launch the runner app on the phone"


async def test_a_phone_that_is_up_is_looked_at_and_left_alone() -> None:
    link: _Link = _Link([_UP])
    state: PhoneState = await keep_once(link, _nobody, 300.0)
    assert (state.kind, link.relaunches) == ("up", 0)
