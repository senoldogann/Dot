"""The Mac is kept from idle sleep while Dot works, and a minute after (item 1671).

``DOT_CAFFEINATE`` names a stand-in that writes the words it was given and waits: nothing keeps the Mac awake here.
"""

import asyncio
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from dotagent.awake import KeepAwake, holds_awake, keep_awake_while_working
from dotagent.config import load_default_config
from dotagent.server.routes_system import awake_state


def test_the_hold_lasts_while_work_goes_on_and_the_grace_after_it() -> None:
    assert holds_awake(True, None, 100.0, 60.0)
    assert holds_awake(False, 50.0, 100.0, 60.0)
    assert not holds_awake(False, 30.0, 100.0, 60.0)
    assert not holds_awake(False, None, 100.0, 60.0)


def test_the_state_says_off_when_the_setting_is_off() -> None:
    config = load_default_config()
    assert config.daemon.keep_awake and config.daemon.keep_awake_after_s == 60
    awake: KeepAwake = KeepAwake("-i")
    assert awake_state(config, awake) == "idle"
    off = config.model_copy(update={"daemon": config.daemon.model_copy(update={"keep_awake": False})})
    assert awake_state(off, awake) == "off"


@pytest.mark.asyncio
async def test_caffeinate_i_recovers_from_a_failed_work_check_and_holds_through_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: Path = tmp_path / "said.txt"
    monkeypatch.setenv("DOT_CAFFEINATE", _stand_in(tmp_path, said))
    awake: KeepAwake = KeepAwake("-i")
    works: list[bool] = [False]
    stopping: asyncio.Event = asyncio.Event()
    failed_check: asyncio.Event = asyncio.Event()

    async def working() -> bool:
        if not failed_check.is_set():
            failed_check.set()
            raise sqlite3.OperationalError("database is locked")
        return works[0]

    keeping: asyncio.Task[None] = asyncio.create_task(keep_awake_while_working(awake, working, 0.5, 0.05, stopping))
    try:
        await asyncio.wait_for(failed_check.wait(), timeout=5)
        assert not awake.on
        works[0] = True
        async with asyncio.timeout(5):
            while not awake.on or not _said(said):  # noqa: ASYNC110 - the hold has no event of its own
                await asyncio.sleep(0.02)
        works[0] = False
        await asyncio.sleep(0.2)
        assert awake.on, "the grace after the last work holds on"
        async with asyncio.timeout(5):
            while awake.on:  # noqa: ASYNC110 - as above
                await asyncio.sleep(0.02)
    finally:
        stopping.set()
        await keeping
    assert not awake.on
    assert _said(said) == ["-i", "-w", str(os.getpid())]


@pytest.mark.asyncio
async def test_a_work_check_that_keeps_failing_lets_a_hold_run_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of item 2044: after one busy tick every check fails; the grace runs out and the hold is released."""
    said: Path = tmp_path / "said.txt"
    monkeypatch.setenv("DOT_CAFFEINATE", _stand_in(tmp_path, said))
    awake: KeepAwake = KeepAwake("-i")
    checks: list[int] = [0]
    stopping: asyncio.Event = asyncio.Event()

    async def working() -> bool:
        checks[0] += 1
        if checks[0] == 1:
            return True
        raise sqlite3.OperationalError("database is locked")

    keeping: asyncio.Task[None] = asyncio.create_task(keep_awake_while_working(awake, working, 0.3, 0.05, stopping))
    try:
        async with asyncio.timeout(5):
            while not awake.on:  # noqa: ASYNC110 - the hold has no event of its own
                await asyncio.sleep(0.02)
            while awake.on:  # noqa: ASYNC110 - as above
                await asyncio.sleep(0.02)
        assert checks[0] > 2, "the checks went on failing while the hold ran out"
    finally:
        stopping.set()
        await keeping


def _stand_in(folder: Path, said: Path) -> str:
    program: Path = folder / "caffeinate"
    program.write_text(f'#!/bin/sh\necho "$@" >> {said}\nexec sleep 60\n')
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return str(program)


def _said(said: Path) -> list[str]:
    return said.read_text().split() if said.exists() else []
