"""The gate that paces searches: requests that ask together start one spacing apart, and a cool-down holds back
every request, those that already wait included."""

import asyncio
import time
from itertools import pairwise

from dotagent.web.gate import RequestGate

SPACING_S: float = 0.1


async def test_requests_that_ask_together_start_one_spacing_apart() -> None:
    gate: RequestGate = RequestGate(SPACING_S)
    started: float = time.monotonic()

    async def turn() -> float:
        await gate.turn()
        return time.monotonic() - started

    starts: list[float] = sorted(await asyncio.gather(*(turn() for _ in range(4))))
    gaps: list[float] = [later - earlier for earlier, later in pairwise(starts)]
    assert all(gap >= SPACING_S * 0.8 for gap in gaps), starts


async def test_a_cool_down_holds_back_a_request_that_already_waits() -> None:
    gate: RequestGate = RequestGate(SPACING_S)
    await gate.turn()
    started: float = time.monotonic()
    waiting: asyncio.Task[None] = asyncio.create_task(gate.turn())
    await asyncio.sleep(SPACING_S / 4)
    gate.cool_down(0.5)
    await waiting
    assert time.monotonic() - started >= 0.45
