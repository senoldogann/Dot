"""The helper that draws Dot's pointer (item 705), started for real: it draws a moment in the top left of the main
screen, answers a hide at once once it runs, and stops when its input closes. It reads nothing of the screen.
"""

import asyncio

import pytest

from dotagent.desktop.pointer import PointerOverlay

pytestmark = [pytest.mark.desktop, pytest.mark.asyncio]


async def test_the_helper_moves_hides_at_once_comes_back_rests_and_stops() -> None:
    overlay: PointerOverlay = PointerOverlay()
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    try:
        await overlay.warm()
        await overlay.move(160.0, 160.0, False)
        await overlay.move(260.0, 200.0, True)
        async with overlay.hidden():  # the first answer comes once the helper runs
            pass
        started: float = loop.time()
        async with overlay.hidden():
            assert loop.time() - started < 0.1
        await overlay.rest()
        process = overlay._process
        assert process is not None and process.returncode is None and overlay._enabled
    finally:
        await overlay.aclose()
    assert process.returncode == 0
