"""The watch on the daemon's event loop (item 1672): a call that blocks the loop is caught while it blocks, with the
call's name in the stack, and kept with how long it lasted."""

import asyncio
import time

import pytest

from dotagent.loop_watch import STALL_S, LoopStall, LoopWatch


def _a_blocking_read_of_the_disk() -> None:
    time.sleep(STALL_S + 0.8)


@pytest.mark.asyncio
async def test_a_call_that_blocks_the_loop_is_named_and_timed() -> None:
    watch: LoopWatch = LoopWatch()
    watch.start()
    try:
        await asyncio.sleep(0.7)
        assert watch.stalls() == []
        _a_blocking_read_of_the_disk()
        await asyncio.sleep(1.2)
        stalls: list[LoopStall] = watch.stalls()
    finally:
        watch.stop()
    assert len(stalls) == 1
    [stall] = stalls
    assert "_a_blocking_read_of_the_disk" in stall.stack
    assert not stall.ongoing and STALL_S <= stall.seconds < STALL_S + 2.0


@pytest.mark.asyncio
async def test_a_loop_that_keeps_answering_has_no_stall() -> None:
    watch: LoopWatch = LoopWatch()
    watch.start()
    try:
        for _ in range(30):
            await asyncio.sleep(0.1)
    finally:
        watch.stop()
    assert watch.stalls() == []
