"""A night that merged pulls the owner's checkout only once main passed on macOS (item 1899): the check's answer is read
from what `scripts/main_macos_green.py check` printed and its exit code, and the night waits for a verdict that is not
pending, up to its limit."""

import asyncio

from dotagent.repair.github import MacOnMain, read_mac_verdict
from dotagent.repair.night import macos_wait_s, wait_for_macos

SHA: str = "0123456789abcdef0123456789abcdef01234567"


def test_the_answer_is_the_last_line_and_its_exit_code_must_agree() -> None:
    printed: str = f"gate.yml: completed success https://x\nmain {SHA} on macOS: green\n"
    assert read_mac_verdict(printed, 0) == MacOnMain(sha=SHA, verdict="green")
    assert read_mac_verdict(f"main {SHA} on macOS: red\n", 1) == MacOnMain(sha=SHA, verdict="red")
    assert read_mac_verdict(f"main {SHA} on macOS: pending\n", 2) == MacOnMain(sha=SHA, verdict="pending")
    assert read_mac_verdict(f"main {SHA} on macOS: green\n", 1) is None
    assert read_mac_verdict("Traceback (most recent call last): ...", 1) is None
    assert read_mac_verdict(f"main {SHA[:12]} on macOS: green\n", 0) is None


def test_the_night_waits_while_main_is_pending_and_stops_at_its_limit() -> None:
    now: list[float] = [0.0]
    slept: list[float] = []
    answers: list[MacOnMain] = [
        MacOnMain(sha=SHA, verdict="pending"),
        MacOnMain(sha=SHA, verdict="pending"),
        MacOnMain(sha=SHA, verdict="green"),
    ]

    async def check() -> MacOnMain:
        return answers.pop(0)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    said: MacOnMain = asyncio.run(wait_for_macos(check, lambda: now[0], sleep, 900.0, 300.0))
    assert said.verdict == "green" and slept == [300.0, 300.0]

    answers[:] = [MacOnMain(sha=SHA, verdict="red")]
    slept.clear()
    assert asyncio.run(wait_for_macos(check, lambda: now[0], sleep, 900.0, 300.0)).verdict == "red"
    assert slept == []

    pending: MacOnMain = MacOnMain(sha=SHA, verdict="pending")

    async def always_pending() -> MacOnMain:
        return pending

    now[0] = 0.0
    slept.clear()
    assert asyncio.run(wait_for_macos(always_pending, lambda: now[0], sleep, 900.0, 300.0)).verdict == "pending"
    assert slept == [300.0, 300.0, 300.0]


def test_the_wait_ends_inside_the_hours_a_restart_waits_on() -> None:
    # A night that began at 0 has its deadline at two hours; a push may add half an hour, less one check's five minutes.
    assert macos_wait_s(7200.0, 1000.0) == 90 * 60.0
    assert macos_wait_s(7200.0, 3600.0) == 7200.0 + 1800.0 - 300.0 - 3600.0
    assert macos_wait_s(7200.0, 7200.0) == 1800.0 - 300.0
    assert macos_wait_s(7200.0, 9000.0) == 0.0
    assert 7200.0 + macos_wait_s(7200.0, 7200.0) + 300.0 <= 3 * 3600.0
