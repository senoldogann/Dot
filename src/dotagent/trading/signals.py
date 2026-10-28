"""What the daemon's watch reads as a move of the market worth a run (item 772), with no model: the prices of the
allowed products, read from Coinbase's list about once a minute and kept two hours, and a product whose price moved
over the last fifteen minutes by at least ``[trading] wake_move_pct`` and by :data:`NOISE_TIMES` times what it usually
moves in fifteen minutes. What it usually moves is the median of its moves over the windows the kept prices show, so
that a coin that swings by five percent an hour wakes nothing at three, and one that never moves wakes nothing at a
tenth of one."""

from collections import deque
from collections.abc import Iterable, Sequence
from statistics import median

from dotagent.core.types import FrozenModel
from dotagent.trading.types import MarketRow

# The window a move is measured over, and how far from its start a price may have been read and still stand for it.
WINDOW_S: float = 900.0
_SLACK_S: float = 90.0
# How long prices are kept, and how many windows a product needs before its usual move is known: about three quarters
# of an hour of looks after the daemon starts or the Mac wakes, in which nothing is read as a move.
_KEPT_S: float = 7200.0
_LEAST_WINDOWS: int = 30
# How many times its usual move over the window a move must be.
NOISE_TIMES: float = 3.0


class Move(FrozenModel):
    """A product's move over the last window: in percent with its sign, against its usual one (the median of its moves
    over the window, without their signs), and its price now."""

    product: str
    pct: float
    usual_pct: float
    price: float


def _windows(kept: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """The moves the prices ``kept`` (oldest first) show over :data:`WINDOW_S`, in percent, each with when it ended: one
    for each price that has another read about a window before it, the one read nearest a window before. A gap (the
    Mac asleep) has none."""
    found: list[tuple[float, float]] = []
    start: int = 0
    for ts, price in kept:
        target: float = ts - WINDOW_S
        while start + 1 < len(kept) and abs(kept[start + 1][0] - target) <= abs(kept[start][0] - target):
            start += 1
        if abs(kept[start][0] - target) <= _SLACK_S:
            found.append((ts, (price / kept[start][1] - 1.0) * 100.0))
    return found


class PriceMemory:
    """The prices of the products looked at, each over the last two hours."""

    def __init__(self) -> None:
        self._seen: dict[str, deque[tuple[float, float]]] = {}

    def add(self, now: float, rows: Iterable[MarketRow]) -> None:
        """Keep the prices of ``rows``, read at ``now``, and forget those older than two hours."""
        for row in rows:
            kept: deque[tuple[float, float]] = self._seen.setdefault(row.product, deque())
            kept.append((now, row.price))
        for product in list(self._seen):
            kept = self._seen[product]
            while kept and now - kept[0][0] > _KEPT_S:
                kept.popleft()
            if not kept:
                del self._seen[product]

    def moves(self, now: float, least_pct: float) -> list[Move]:
        """The products read at ``now`` whose move over the last window is at least ``least_pct`` percent and at least
        :data:`NOISE_TIMES` their usual one, the largest first."""
        found: list[Move] = []
        for product, kept in self._seen.items():
            if kept[-1][0] != now:
                continue
            windows: list[tuple[float, float]] = _windows(list(kept))
            if len(windows) <= _LEAST_WINDOWS or windows[-1][0] != now:
                continue
            last: float = windows[-1][1]
            usual: float = median(abs(move) for _, move in windows[:-1])
            if abs(last) >= least_pct and abs(last) >= NOISE_TIMES * usual:
                found.append(Move(product=product, pct=last, usual_pct=usual, price=kept[-1][1]))
        return sorted(found, key=lambda move: -abs(move.pct))


def move_words(moves: Sequence[Move], quote: str) -> str:
    """The moves as a run reads them first: each product, its move, its usual one and its price."""
    return "; ".join(
        f"{move.product} {move.pct:+.2f} % in {WINDOW_S / 60:.0f} minutes (it usually moves {move.usual_pct:.2f} %), "
        f"now {move.price:.8g} {quote}"
        for move in moves
    )
