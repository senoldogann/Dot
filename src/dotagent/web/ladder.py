"""The shared state of the search ladder: which engines sit out, and the answers of recent queries. The web client
holds one of each; the search module reads and writes them."""

from collections.abc import Mapping
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.web.types import SearchHit

# The engines a search can go to.
SearchEngine = Literal["searxng", "ollama", "anthropic", "duckduckgo", "brave", "browser"]

# How long an engine that limited this machine sits out; the searches of that time go to the next rung at once.
# DuckDuckGo's bot check, once met, stays for a while; Brave's is a rate limit of a minute or so; the browser rung
# meeting a check is a sign the machine is marked, so it sits out as long as DuckDuckGo.
BENCH_S: Mapping[SearchEngine, float] = {
    "searxng": 120.0,
    "ollama": 60.0,
    "anthropic": 120.0,
    "duckduckgo": 600.0,
    "brave": 90.0,
    "browser": 600.0,
}
# How long the answer to a query is kept: the same query in that time is answered from memory, with its age.
MEMORY_S: float = 600.0


class Skipped(FrozenModel):
    """A rung the search went past, and why."""

    engine: SearchEngine
    reason: str


class SearchOutcome(FrozenModel):
    """What a search came back with: the hits, the engine that answered, the rungs skipped on the way, and how long
    ago the answer was found (0 when it was found now)."""

    hits: tuple[SearchHit, ...]
    engine: SearchEngine
    skipped: tuple[Skipped, ...]
    age_s: float


class EngineBench:
    """Which engines sit out, and until when (monotonic seconds)."""

    def __init__(self) -> None:
        self._until: dict[SearchEngine, float] = {}

    def bench(self, engine: SearchEngine, seconds: float, now: float) -> None:
        self._until[engine] = max(self._until.get(engine, 0.0), now + seconds)

    def remaining(self, engine: SearchEngine, now: float) -> float | None:
        """How much longer ``engine`` sits out, or ``None`` when it may be asked."""
        until: float | None = self._until.get(engine)
        return None if until is None or until <= now else until - now


class AnswerMemory:
    """The outcomes of recent queries, so the same query asked again within ``MEMORY_S`` costs no engine anything."""

    def __init__(self) -> None:
        self._kept: dict[str, tuple[float, SearchOutcome]] = {}

    def recall(self, query: str, now: float) -> SearchOutcome | None:
        found: tuple[float, SearchOutcome] | None = self._kept.get(memory_key(query))
        if found is None or now - found[0] > MEMORY_S:
            return None
        return found[1].model_copy(update={"age_s": now - found[0]})

    def keep(self, query: str, outcome: SearchOutcome, now: float) -> None:
        stale: list[str] = [key for key, (at, _) in self._kept.items() if now - at > MEMORY_S]
        for key in stale:
            del self._kept[key]
        self._kept[memory_key(query)] = (now, outcome)


def memory_key(query: str) -> str:
    """Queries that differ only in case and spacing are one query."""
    return " ".join(query.lower().split())
