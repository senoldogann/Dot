"""Long-term memory: notes saved by the agent (or the user) and found again by words and by meaning.

A search runs two rankers over the same notes - BM25 over the words and cosine similarity over the embeddings - and
merges their rankings with reciprocal rank fusion, so a name or an identifier is found as reliably as a paraphrase.
Embeddings come from Ollama or from the OpenAI API (``[memory] embed_provider``); with ``embed_enabled = false`` only
the words are searched. Nothing here falls back silently: when the embedding service fails, the call fails with its
error.

Each stored vector belongs to the embedding model that made it. The model name is kept in the ``kv`` table, and
:func:`sync_embedding_model` drops all vectors when the configured model is another one; :func:`backfill` embeds
the notes that have no vector, so a search always covers every note.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dotagent.config import AppConfig, MemoryConfig
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.memory.embedder import Embedder, build_embedder
from dotagent.memory.ranking import Similar, Vector, most_similar, reciprocal_rank_fusion
from dotagent.memory.words import lowercase, search_terms
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

CORE_TAG: str = "core"
_MODEL_KEY: str = "memory.embed_model"
_BACKFILL_BATCH: int = 32
_QUERY_CHARS: int = 1500
_SIMILAR_SHOWN: int = 3
_MIN_NOTE_CHARS: int = 3
_MAX_TAGS: int = 6
_TAG_CHARS: int = 32
_TAG_NOISE: re.Pattern[str] = re.compile(r"\W+")
_MIN_NOTES_FOR_SHARE: int = 4
_MAX_NOTE_SHARE: float = 0.5


@dataclass(frozen=True)
class MemoryIndex:
    """The note store plus the embedder that makes it searchable by meaning (``None`` when embeddings are off)."""

    store: Store
    embedder: Embedder | None
    config: MemoryConfig


class MemoryHit(FrozenModel):
    """A note found by a search; ``similarity`` is its cosine to the query, ``None`` when only the words matched."""

    record: MemoryRecord
    similarity: float | None


class SavedMemory(FrozenModel):
    """The outcome of saving: the note, whether it is new, and the existing notes that say nearly the same."""

    record: MemoryRecord
    created: bool
    similar: tuple[MemoryHit, ...]


def normalize_text(text: str) -> str:
    """A note as one line: runs of whitespace (newlines included) become a single space."""
    return " ".join(text.split())


def normalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Tags as short lowercase words, without duplicates. A tag never holds a comma: the store joins tags with it."""
    cleaned: dict[str, None] = {}
    for tag in tags:
        lowered: str = lowercase(tag.strip())
        slug: str = _TAG_NOISE.sub("-", lowered).strip("-")[:_TAG_CHARS].strip("-")
        if slug:
            cleaned[slug] = None
    return tuple(cleaned)[:_MAX_TAGS]


def _checked_text(content: str, max_chars: int) -> str:
    text: str = normalize_text(content)
    if len(text) < _MIN_NOTE_CHARS:
        raise InvalidValueError("the note is empty", {"content": content})
    if len(text) > max_chars:
        raise InvalidValueError(
            f"the note is {len(text)} characters long; the limit is {max_chars}. Save one shorter, single fact.",
            {"chars": len(text), "max_chars": max_chars},
        )
    return text


async def open_memory_index(store: Store, config: AppConfig) -> MemoryIndex:
    """The memory of ``store``; its stored vectors are checked against the configured embedding model."""
    embedder: Embedder | None = await build_embedder(config) if config.memory.embed_enabled else None
    index: MemoryIndex = MemoryIndex(store=store, embedder=embedder, config=config.memory)
    await sync_embedding_model(index)
    return index


async def close_memory_index(index: MemoryIndex) -> None:
    if index.embedder is not None:
        await index.embedder.aclose()


async def sync_embedding_model(index: MemoryIndex) -> None:
    """Drop every stored vector when another embedding model made them; they are made again on demand."""
    embedder: Embedder | None = index.embedder
    if embedder is None:
        return
    if await index.store.kv_get(_MODEL_KEY) == embedder.model:
        return
    await index.store.clear_memory_vectors()
    await index.store.kv_set(_MODEL_KEY, embedder.model)


async def backfill(index: MemoryIndex) -> int:
    """Embed the notes that have no vector yet; returns how many were embedded."""
    embedder: Embedder | None = index.embedder
    if embedder is None:
        return 0
    embedded: int = 0
    while True:
        pending: list[MemoryRecord] = await index.store.memories_without_vectors(_BACKFILL_BATCH)
        if not pending:
            return embedded
        vectors: list[Vector] = await embedder.embed([record.content for record in pending])
        for record, vector in zip(pending, vectors, strict=True):
            await index.store.set_memory_vector(record.id, vector)
        embedded += len(pending)


async def reindex(index: MemoryIndex) -> int:
    """Embed every note again; returns how many notes there are."""
    await index.store.clear_memory_vectors()
    return await backfill(index)


async def _similar_notes(index: MemoryIndex, vector: Vector) -> tuple[MemoryHit, ...]:
    close: list[Similar] = most_similar(
        vector, await index.store.all_memory_vectors(), index.config.similar_threshold, _SIMILAR_SHOWN
    )
    scores: dict[str, float] = {item.id: item.similarity for item in close}
    records: list[MemoryRecord] = await index.store.get_memories([item.id for item in close])
    return tuple(MemoryHit(record=record, similarity=scores[record.id]) for record in records)


async def save_memory(index: MemoryIndex, content: str, tags: Sequence[str], run_id: str | None) -> SavedMemory:
    """Save a note. The same text twice stays one note (the tags are merged); a new note comes back with the existing
    notes that are nearly the same, so the caller can retire the ones that are outdated."""
    text: str = _checked_text(content, index.config.max_chars)
    clean_tags: tuple[str, ...] = normalize_tags(tags)
    existing: MemoryRecord | None = await index.store.find_memory_by_content(text)
    if existing is not None:
        merged: tuple[str, ...] = normalize_tags([*existing.tags, *clean_tags])
        record: MemoryRecord = (
            existing if merged == existing.tags else await index.store.set_memory_tags(existing.id, merged)
        )
        return SavedMemory(record=record, created=False, similar=())
    vector: Vector | None = None
    similar: tuple[MemoryHit, ...] = ()
    if index.embedder is not None:
        await backfill(index)
        vector = (await index.embedder.embed([text]))[0]
        similar = await _similar_notes(index, vector)
    created: MemoryRecord = await index.store.add_memory(text, clean_tags, run_id, vector)
    return SavedMemory(record=created, created=True, similar=similar)


async def _distinctive_terms(store: Store, terms: tuple[str, ...], total: int) -> tuple[str, ...]:
    """The ``terms`` that tell notes apart. A word found in more than half of the notes (``user``, when every note
    starts with "The user") matches everything and says nothing, once there are enough notes to tell."""
    if total < _MIN_NOTES_FOR_SHARE or not terms:
        return terms
    counts: dict[str, int] = await store.memory_term_counts(terms)
    return tuple(term for term in terms if counts[term] <= total * _MAX_NOTE_SHARE)


async def recall(index: MemoryIndex, query: str, limit: int) -> list[MemoryHit]:
    """The ``limit`` notes that best fit ``query``, best first."""
    text: str = normalize_text(query)[:_QUERY_CHARS]
    total: int = await index.store.count_memories()
    if not text or limit <= 0 or total == 0:
        return []
    config: MemoryConfig = index.config
    terms: tuple[str, ...] = await _distinctive_terms(index.store, search_terms(text), total)
    rankings: list[list[str]] = [await index.store.search_memories_terms(terms, config.candidate_pool)]
    similarities: dict[str, float] = {}
    if index.embedder is not None:
        await backfill(index)
        query_vector: Vector = (await index.embedder.embed([text]))[0]
        close: list[Similar] = most_similar(
            query_vector, await index.store.all_memory_vectors(), config.min_similarity, config.candidate_pool
        )
        rankings.append([item.id for item in close])
        similarities = {item.id: item.similarity for item in close}
    records: list[MemoryRecord] = await index.store.get_memories(reciprocal_rank_fusion(rankings, limit))
    return [MemoryHit(record=record, similarity=similarities.get(record.id)) for record in records]


def _closest_first(notes: Sequence[MemoryRecord], similarities: dict[str, float], limit: int) -> list[MemoryRecord]:
    """``limit`` of ``notes``: the ones closest to the query first, then the most recently changed."""
    ranked: list[MemoryRecord] = sorted(
        notes, key=lambda record: (-similarities.get(record.id, -1.0), -record.updated_at)
    )
    return ranked[:limit]


async def prompt_memories(index: MemoryIndex, query: str) -> list[MemoryRecord]:
    """The notes a run starts with, without repeats: the standing ones (tagged ``core``), the ones the user wrote
    (all of them up to ``user_limit``, past that the ones closest to ``query``) and the agent's notes related to
    ``query`` (the goal and the earlier goals of its thread).

    A goal is full of everyday words that match unrelated notes, so when embeddings are on only the notes whose
    meaning clearly fits the query are taken; ``memory_search`` still finds the others."""
    config: MemoryConfig = index.config
    standing: list[MemoryRecord] = (
        await index.store.memories_with_tag(CORE_TAG, config.core_limit) if config.core_limit > 0 else []
    )
    candidates: list[MemoryHit] = await recall(index, query, config.candidate_pool)
    similarities: dict[str, float] = {hit.record.id: hit.similarity for hit in candidates if hit.similarity is not None}
    written: list[MemoryRecord] = (
        await index.store.user_memories(config.user_limit * 4) if config.user_limit > 0 else []
    )
    known: set[str] = {record.id for record in standing}
    users: list[MemoryRecord] = _closest_first(
        [record for record in written if record.id not in known], similarities, config.user_limit
    )
    known.update(record.id for record in users)
    related: list[MemoryHit] = (
        candidates
        if index.embedder is None
        else [
            hit for hit in candidates if hit.similarity is not None and hit.similarity >= config.inject_min_similarity
        ]
    )
    fresh: list[MemoryRecord] = [hit.record for hit in related if hit.record.id not in known]
    return [*standing, *users, *fresh[: config.inject_top_k]]


async def update_memory_note(index: MemoryIndex, memory_id: str, content: str, tags: Sequence[str]) -> MemoryRecord:
    """Rewrite a note in place (text and tags); it keeps its id, its author and its use count and is embedded again."""
    await index.store.get_memory(memory_id)
    text: str = _checked_text(content, index.config.max_chars)
    vector: Vector | None = None
    if index.embedder is not None:
        vector = (await index.embedder.embed([text]))[0]
    return await index.store.update_memory(memory_id, text, normalize_tags(tags), vector)


async def forget_memory(index: MemoryIndex, memory_id: str) -> MemoryRecord:
    """Delete a note; returns what was deleted. An unknown id is an error."""
    record: MemoryRecord = await index.store.get_memory(memory_id)
    await index.store.delete_memory(memory_id)
    return record
