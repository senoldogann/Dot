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
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from dotagent.config import AppConfig, MemoryConfig
from dotagent.core.errors import InvalidValueError, StoreError
from dotagent.core.text import estimate_text_tokens
from dotagent.core.types import FrozenModel
from dotagent.memory.embedder import Embedder, build_embedder
from dotagent.memory.ranking import Similar, Vector, fused_scores, most_similar, ranked_by_use
from dotagent.memory.words import lowercase, search_terms
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord, MemoryScore

CORE_TAG: str = "core"
# The tags the nightly reflection (:mod:`dotagent.agent.reflection`) writes: the day's journal note, and a lesson
# that rests, which a search no longer finds while the Memory page still lists it.
JOURNAL_TAG: str = "journal"
REST_TAG: str = "rest"
# The source of a note the reflection wrote, in place of a run's id.
REFLECTION_SOURCE: str = "reflection"
# The kv key of the owner section (item 93): what Dot knows about the owner, kept by the nightly reflection.
OWNER_KEY: str = "owner.section"
# The most lines Dot's part of the owner section holds.
OWNER_LINES: int = 12
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


def resting(record: MemoryRecord, now: float, rest_days: int, score: MemoryScore | None, min_uses: int) -> bool:
    """Whether a note rests: it is the agent's (the user's notes and the standing ones never rest) and either the
    reflection retired it (tagged ``rest``), or no run was handed it in ``rest_days`` days since it was written, or
    its ``score`` (how the runs handed it lately ended) is negative over at least ``min_uses`` runs. A resting note
    is not handed to a run on its own; a search still finds it unless it was retired."""
    if record.source_run is None or CORE_TAG in record.tags:
        return False
    if REST_TAG in record.tags:
        return True
    if score is not None and score.handed >= min_uses and score.done < score.failed:
        return True
    return record.used_count == 0 and record.created_at < now - rest_days * 86400.0


async def recent_scores(index: MemoryIndex, memory_ids: Sequence[str], now: float) -> dict[str, MemoryScore]:
    """The scores of ``memory_ids`` over the last ``[memory] score_days``."""
    return await index.store.memory_scores(memory_ids, now - index.config.score_days * 86400.0)


async def recall(index: MemoryIndex, query: str, limit: int) -> list[MemoryHit]:
    """The ``limit`` notes that best fit ``query``, best first: the fusion of the two rankers, with the agent's notes
    lifted by how lately a run was handed them (:func:`dotagent.memory.ranking.ranked_by_use`, half-life
    ``[memory] use_half_life_days``) and by their score (:func:`recent_scores`), either way. A note the reflection
    retired (tagged ``rest``) is never one."""
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
    scores: dict[str, float] = fused_scores(rankings)
    candidates: list[MemoryRecord] = [
        record for record in await index.store.get_memories(list(scores)) if REST_TAG not in record.tags
    ]
    now: float = time.time()
    balances: dict[str, MemoryScore] = await recent_scores(index, [record.id for record in candidates], now)
    ranked: list[MemoryRecord] = ranked_by_use(
        scores, candidates, now, config.use_half_life_days * 86400.0, balances, config.score_min_uses, limit
    )
    return [MemoryHit(record=record, similarity=similarities.get(record.id)) for record in ranked]


def _closest_first(notes: Sequence[MemoryRecord], similarities: dict[str, float], limit: int) -> list[MemoryRecord]:
    """``limit`` of ``notes``: the ones closest to the query first, then the most recently changed."""
    ranked: list[MemoryRecord] = sorted(
        notes, key=lambda record: (-similarities.get(record.id, -1.0), -record.updated_at)
    )
    return ranked[:limit]


class OwnerSection(FrozenModel):
    """What Dot knows about the owner: ``dot_lines``, which the nightly reflection rewrites from the owner's notes and
    the day's runs, and ``owner_lines``, the owner's own corrections, kept as they are and never rewritten;
    ``updated_at`` is when either last changed (``0.0`` before anything was written)."""

    dot_lines: tuple[str, ...]
    owner_lines: tuple[str, ...]
    updated_at: float


EMPTY_OWNER: OwnerSection = OwnerSection(dot_lines=(), owner_lines=(), updated_at=0.0)


async def owner_section(store: Store) -> OwnerSection:
    """The owner section the store holds, empty before the first reflection wrote one."""
    raw: str | None = await store.kv_get(OWNER_KEY)
    if raw is None:
        return EMPTY_OWNER
    try:
        return OwnerSection.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError("the owner section cannot be read", {"key": OWNER_KEY, "error": str(exc)}) from exc


async def set_owner_section(store: Store, section: OwnerSection) -> None:
    await store.kv_set(OWNER_KEY, section.model_dump_json())


def owner_lines_within(lines: Sequence[str], owner_chars: int) -> tuple[str, ...]:
    """``lines`` as Dot's part of the section: one line each, without blanks, at most :data:`OWNER_LINES`, and no
    more than ``owner_chars`` characters in all (the lines past the budget are left out)."""
    kept: list[str] = []
    spent: int = 0
    for line in lines:
        flat: str = " ".join(line.split())
        if not flat or len(kept) >= OWNER_LINES:
            continue
        if spent + len(flat) > owner_chars:
            break
        kept.append(flat)
        spent += len(flat)
    return tuple(kept)


def owner_prompt_lines(section: OwnerSection) -> tuple[str, ...]:
    """The section as the prompt lists it: the owner's own lines first, marked as theirs, then Dot's."""
    return (*(f"(you said) {line}" for line in section.owner_lines), *section.dot_lines)


class Remembered(FrozenModel):
    """The notes a run starts with and the tokens they are estimated to take."""

    records: tuple[MemoryRecord, ...]
    tokens: int


# The tokens a note's rendered line adds to its text and tags: who wrote it and when.
_NOTE_LINE_TOKENS: int = 12
# How many standing and user notes are read before the budget picks among them.
_FETCH_NOTES: int = 200


def note_tokens(record: MemoryRecord) -> int:
    """The tokens a note takes in the prompt, estimated: its text, its tags and the line around them."""
    return estimate_text_tokens(record.content + "".join(f" #{tag}" for tag in record.tags)) + _NOTE_LINE_TOKENS


def memory_budget(config: MemoryConfig, context_length: int | None) -> int:
    """The tokens a run's starting notes may take: ``inject_share`` of the model's window, at most
    ``inject_tokens``; ``inject_tokens`` alone when the provider does not say the window."""
    if context_length is None:
        return config.inject_tokens
    return min(config.inject_tokens, round(context_length * config.inject_share))


def fit_notes(notes: Sequence[MemoryRecord], budget: int, spent: int) -> tuple[list[MemoryRecord], int]:
    """``notes`` in order, each taken when it still fits ``budget`` after ``spent`` tokens; a note that does not fit
    is passed over, a shorter one after it may still fit. Returns the taken notes and the tokens spent in all."""
    taken: list[MemoryRecord] = []
    for note in notes:
        cost: int = note_tokens(note)
        if spent + cost <= budget:
            taken.append(note)
            spent += cost
    return taken, spent


async def prompt_memories(index: MemoryIndex, query: str, budget: int) -> Remembered:
    """The notes a run starts with, without repeats, as many as fit ``budget`` tokens (:func:`fit_notes`), in this
    order: the standing ones (tagged ``core``, newest first), the ones the user wrote (closest to ``query`` first)
    and the agent's notes related to ``query`` (the goal and the earlier goals of its thread; by fit and score) that
    do not rest (:func:`resting`). A note that does not fit is left to ``memory_search``.

    A goal is full of everyday words that match unrelated notes, so when embeddings are on only the notes whose
    meaning clearly fits the query are taken; ``memory_search`` still finds the others, and the resting ones."""
    config: MemoryConfig = index.config
    standing: list[MemoryRecord] = await index.store.memories_with_tag(CORE_TAG, _FETCH_NOTES)
    candidates: list[MemoryHit] = await recall(index, query, config.candidate_pool)
    similarities: dict[str, float] = {hit.record.id: hit.similarity for hit in candidates if hit.similarity is not None}
    written: list[MemoryRecord] = await index.store.user_memories(_FETCH_NOTES)
    known: set[str] = {record.id for record in standing}
    users: list[MemoryRecord] = _closest_first(
        [record for record in written if record.id not in known], similarities, _FETCH_NOTES
    )
    known.update(record.id for record in users)
    related: list[MemoryHit] = (
        candidates
        if index.embedder is None
        else [
            hit for hit in candidates if hit.similarity is not None and hit.similarity >= config.inject_min_similarity
        ]
    )
    now: float = time.time()
    balances: dict[str, MemoryScore] = await recent_scores(index, [hit.record.id for hit in related], now)
    fresh: list[MemoryRecord] = [
        hit.record
        for hit in related
        if hit.record.id not in known
        and not resting(hit.record, now, config.rest_days, balances.get(hit.record.id), config.score_min_uses)
    ]
    taken: list[MemoryRecord] = []
    spent: int = 0
    for group in (standing, users, fresh):
        part, spent = fit_notes(group, budget, spent)
        taken.extend(part)
    return Remembered(records=tuple(taken), tokens=spent)


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
