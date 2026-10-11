"""Ranking helpers for the notes: cosine similarity over unit vectors, reciprocal rank fusion, and the lift a note
gets from having been used lately (item 71). All pure."""

import heapq
import math
from collections.abc import Iterable, Mapping, Sequence

from dotagent.core.errors import ProviderProtocolError, StoreError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.models import MemoryRecord, MemoryScore

type Vector = tuple[float, ...]

# Reciprocal rank fusion: an item at rank r in a list scores 1 / (damping + r). 60 is the value of the original paper;
# it keeps one list's first place from outweighing a second list's third place.
_FUSION_DAMPING: int = 60
# A note used a moment ago is lifted by as much as a second place in one list, so two rankers agreeing on a note no run
# built on still beat one ranker and a recent use; the lift halves every half-life.
_USE_LIFT: float = 1.0 / (_FUSION_DAMPING + 2)


class Similar(FrozenModel):
    id: str
    similarity: float


def unit_vector(values: Sequence[float], context: JsonObject) -> Vector:
    """``values`` scaled to length 1, so that the dot product of two such vectors is their cosine similarity.
    ``context`` is added to the error that a vector without a direction raises."""
    if not all(math.isfinite(value) for value in values):
        raise ProviderProtocolError(
            "an embedding holds a number that is not finite", {**context, "dimension": len(values)}
        )
    length: float = math.sqrt(math.sumprod(values, values))
    if length == 0.0:
        raise ProviderProtocolError("an embedding has length zero", {**context, "dimension": len(values)})
    return tuple(value / length for value in values)


def most_similar(query: Vector, stored: Iterable[tuple[str, Vector]], minimum: float, limit: int) -> list[Similar]:
    """The ``limit`` stored unit vectors closest to the unit vector ``query`` whose cosine is at least ``minimum``,
    closest first. A stored vector of another dimension means it came from another model: that is an error."""
    close: list[Similar] = []
    for identifier, vector in stored:
        if len(vector) != len(query):
            raise StoreError(
                "a stored embedding has another dimension than the query",
                {"memory_id": identifier, "stored_dimension": len(vector), "query_dimension": len(query)},
            )
        similarity: float = math.sumprod(query, vector)
        if similarity >= minimum:
            close.append(Similar(id=identifier, similarity=similarity))
    return heapq.nlargest(limit, close, key=lambda item: item.similarity)


def fused_scores(rankings: Sequence[Sequence[str]]) -> dict[str, float]:
    """The reciprocal rank fusion score of every id in ``rankings``, in the order of first appearance: an id scores in
    every ranking that has it."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (_FUSION_DAMPING + rank)
    return scores


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], limit: int) -> list[str]:
    """One best-first list out of several (:func:`fused_scores`); ties keep the order of first appearance."""
    scores: dict[str, float] = fused_scores(rankings)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:limit]


def use_lift(last_used_at: float | None, now: float, half_life_s: float) -> float:
    """How much a note's last use lifts it, 0 to 1: 1 for a use at ``now`` (or later, by a clock that moved), halving
    every ``half_life_s`` seconds, 0 for a note never handed to a run."""
    if last_used_at is None:
        return 0.0
    return math.pow(0.5, max(0.0, now - last_used_at) / half_life_s)


def balance_lift(score: MemoryScore | None, min_uses: int) -> float:
    """How much a note's score moves it, -1 to 1: its balance (the runs handed it that ended done, less the ones
    that failed) over ``min_uses``, capped at one either way; 0 until ``min_uses`` runs were handed it."""
    if score is None or score.handed < min_uses:
        return 0.0
    return max(-1.0, min(1.0, (score.done - score.failed) / min_uses))


def ranked_by_use(
    scores: Mapping[str, float],
    notes: Sequence[MemoryRecord],
    now: float,
    half_life_s: float,
    balances: Mapping[str, MemoryScore],
    min_uses: int,
    limit: int,
) -> list[MemoryRecord]:
    """``notes`` best first, at most ``limit``: each note's fused score plus the lift of its last use
    (:func:`use_lift`, worth a second place in one list at most) and of its balance (:func:`balance_lift`, worth
    as much, either way) when the note is the agent's; the notes the user wrote stand on their fused score alone.
    Ties keep the order of ``notes``."""

    def score(note: MemoryRecord) -> float:
        if note.source_run is None:
            return scores.get(note.id, 0.0)
        lift: float = use_lift(note.last_used_at, now, half_life_s) + balance_lift(balances.get(note.id), min_uses)
        return scores.get(note.id, 0.0) + lift * _USE_LIFT

    return sorted(notes, key=score, reverse=True)[:limit]
