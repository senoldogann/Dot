"""The Mac is kept from idle sleep while Dot works, and a minute after (item 1671).

``DOT_CAFFEINATE`` names a stand-in that writes the words it was given and waits: nothing keeps the Mac awake here.
"""

import asyncio
import os
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
async def test_caffeinate_i_is_held_while_dot_works_and_let_go_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: Path = tmp_path / "said.txt"
    monkeypatch.setenv("DOT_CAFFEINATE", _stand_in(tmp_path, said))
    awake: KeepAwake = KeepAwake("-i")
    works: list[bool] = [False]
    stopping: asyncio.Event = asyncio.Event()
    keeping: asyncio.Task[None] = asyncio.create_task(
        keep_awake_while_working(awake, lambda: works[0], 0.5, 0.05, stopping)
    )
    try:
        await asyncio.sleep(0.2)
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


def _stand_in(folder: Path, said: Path) -> str:
    program: Path = folder / "caffeinate"
    program.write_text(f'#!/bin/sh\necho "$@" >> {said}\nexec sleep 60\n')
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return str(program)


def _said(said: Path) -> list[str]:
    return said.read_text().split() if said.exists() else []
