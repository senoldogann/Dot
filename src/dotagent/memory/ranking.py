"""Ranking helpers for the notes: cosine similarity over unit vectors and reciprocal rank fusion. All pure."""

import heapq
import math
from collections.abc import Iterable, Sequence

from dotagent.core.errors import ProviderProtocolError, StoreError
from dotagent.core.types import FrozenModel, JsonObject

type Vector = tuple[float, ...]

# Reciprocal rank fusion: an item at rank r in a list scores 1 / (damping + r). 60 is the value of the original paper;
# it keeps one list's first place from outweighing a second list's third place.
_FUSION_DAMPING: int = 60


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


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], limit: int) -> list[str]:
    """One best-first list out of several: an id scores in every ranking that has it; ties keep the order of first
    appearance."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (_FUSION_DAMPING + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
