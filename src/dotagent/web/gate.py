"""Pacing for a web service that answers a burst of requests with its bot check."""

import asyncio
import time


class RequestGate:
    """Lets requests start at least ``spacing_s`` seconds apart, in the order they ask, however many ask at once. A
    request that has started is not waited for, so slow answers still overlap. ``cool_down`` holds back every request,
    those already waiting included."""

    def __init__(self, spacing_s: float) -> None:
        self._spacing_s: float = spacing_s
        self._lock: asyncio.Lock = asyncio.Lock()
        self._open_at: float = 0.0

    async def turn(self) -> None:
        """Return when the caller may send its request."""
        async with self._lock:
            pause: float = self._open_at - time.monotonic()
            while pause > 0:
                await asyncio.sleep(pause)
                pause = self._open_at - time.monotonic()
            self._open_at = time.monotonic() + self._spacing_s

    def cool_down(self, seconds: float) -> None:
        """No request starts for the next ``seconds`` seconds; a longer pause that is already running stays."""
        self._open_at = max(self._open_at, time.monotonic() + seconds)
