"""A watch on the daemon's event loop (item 1672): a thread of its own asks the loop to answer every half second, and
when it has not answered for more than :data:`STALL_S` seconds, it logs ``loop_stalled`` with where the loop's thread
is at that moment, its stack, and keeps the last :data:`KEPT` stalls for Settings › System › Diagnostics.

One loop serves every run, every stream of the pages and the phone, and the API: a call that blocks it (a slow
synchronous read, a PyObjC call that waits on macOS) freezes them all, and nothing said so. The stack is read from the
thread itself (``sys._current_frames``) while the loop is still stuck, so it names the call that blocks it.
"""

import asyncio
import sys
import threading
import time
import traceback
from collections import deque
from types import FrameType

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

# How long the loop may not answer before it is said to have stalled, how often it is asked, and how many stalls are
# kept.
STALL_S: float = 2.0
_ASK_S: float = 0.5
KEPT: int = 20
_STACK_FRAMES: int = 30
_STACK_CHARS: int = 6000


class LoopStall(FrozenModel):
    """A time the loop did not answer: when it began (epoch seconds), for how long (so far, while ``ongoing``), and
    where the loop's thread was when it was seen stalled (its stack, innermost last)."""

    began_at: float
    seconds: float
    ongoing: bool
    stack: str


def stack_of(frame: FrameType | None) -> str:
    """The stack of ``frame``'s thread, innermost call last, its last :data:`_STACK_FRAMES` frames and at most
    :data:`_STACK_CHARS` characters (the end kept)."""
    if frame is None:
        return "(the loop's thread was not found)"
    text: str = "".join(traceback.format_stack(frame, limit=_STACK_FRAMES))
    return text[-_STACK_CHARS:]


class LoopWatch:
    """The watch of one event loop: :meth:`start` from inside the loop, :meth:`stop` on the way out."""

    def __init__(self) -> None:
        self._kept: deque[LoopStall] = deque(maxlen=KEPT)
        self._lock: threading.Lock = threading.Lock()
        self._stop: threading.Event = threading.Event()
        self._answered: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def stalls(self) -> list[LoopStall]:
        """The stalls kept, newest first."""
        with self._lock:
            return list(reversed(self._kept))

    def start(self) -> None:
        """Watch the loop this is called from, from a thread of its own, until :meth:`stop`."""
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        ident: int = threading.get_ident()
        self._thread = threading.Thread(target=self._watch, args=(loop, ident), name="loop-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _watch(self, loop: asyncio.AbstractEventLoop, ident: int) -> None:
        while not self._stop.is_set():
            self._answered.clear()
            asked: float = time.monotonic()
            began_at: float = time.time()
            try:
                loop.call_soon_threadsafe(self._answered.set)
            except RuntimeError:
                return  # The loop is closed: nothing is left to watch.
            if not self._answered.wait(STALL_S):
                self._stalled(began_at, asked, ident)
            self._stop.wait(_ASK_S)

    def _stalled(self, began_at: float, asked: float, ident: int) -> None:
        """The loop has not answered for :data:`STALL_S`: say where it is, then wait for its answer and say how long
        it took."""
        stack: str = stack_of(sys._current_frames().get(ident))
        stall: LoopStall = LoopStall(began_at=began_at, seconds=time.monotonic() - asked, ongoing=True, stack=stack)
        with self._lock:
            self._kept.append(stall)
        log.warning("loop_stalled", seconds=round(stall.seconds, 2), stack=stack)
        while not self._answered.wait(_ASK_S):
            if self._stop.is_set():
                return
        ended: LoopStall = stall.model_copy(update={"seconds": time.monotonic() - asked, "ongoing": False})
        with self._lock:
            if self._kept and self._kept[-1] is stall:
                self._kept[-1] = ended
        log.warning("loop_stall_ended", seconds=round(ended.seconds, 2))
